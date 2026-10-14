"""池化布局的状态搬运——副容器用(方案 §4、§8、§9)。

副容器挂着全部用户目录(`/pool/users`)与本沙箱的 state 卷(`/pool/state`)。两块盘都按**不可信**处理:
  · state 卷从 opencode 启动那一刻起由主容器写,而主容器里的模型能执行命令;
  · NAS 上的本人目录在按需模式下由主容器直接读写,可能预先埋了链接。
所以这里对两边的一切读写都经 `fdtree`(逐级不跟随、打开后确认普通文件),**绑定之后不用 SQLite 打开
state 卷上的任何文件**:库的一致性快照由主容器生成在 `.pool/snap/`,这里只把快照当普通文件搬。

沿用现有模式(statesync)的规则:dirty 标记、非正常退出先给 NAS 留快照、没载入成功不传播删除、
库自检与从备份恢复、`.owner` 独占(30 秒刷新、90 秒过期)。

调用方(poolctl)负责打开并持有三个根描述符:`users/` 根、本人目录、state 卷根。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import fdtree
import podctl
import statesync

# 池化下本地化的子目录。比现有模式多两项:gdaa/sessions(登录句柄)与 reports(大盘的数据)——
# 主容器看不到 NAS,它们只能先载入本地。gdaa 下其余内容(config.yaml 每次绑定重新生成;
# credentials/ 与 key 容器版用不到)不载入、不回写,NAS 上原样保留(方案 §8)。
POOL_LOCALIZED: Tuple[str, ...] = ("xdg-data", "xdg-state", "workspace", "gdaa/sessions", "reports")
POOL_DIR = ".pool"                      # state 卷上两个容器之间的工作区,不回写 NAS
SNAP_DIR = POOL_DIR + "/snap"           # 主容器生成的库快照:<子目录>/<相对路径>
RESTORE_DIR = POOL_DIR + "/restore"     # 自检时从 NAS 备份拷过来待检的副本
MAIN_DB = "xdg-data/opencode/opencode.db"
BACKUP_DIR = "backup"
OWNER = podctl.OWNER_NAME
MARK_DIRTY = statesync.MARK_DIRTY
SNAPSHOT_PREFIX = statesync.SNAPSHOT_PREFIX
OWNER_STALE_S = 90
USER_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,52}[a-z0-9])?$")   # 与网关同一条规则
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


class PoolError(Exception):
    """带一个给平台分支用的 reason,和一句给人看的说明。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass
class Moved:
    files: int = 0
    dbs: int = 0
    bytes: int = 0
    skipped: int = 0
    deleted: int = 0
    seconds: float = 0.0
    errors: List[str] = field(default_factory=list)

    def add(self, st: fdtree.Stats) -> None:
        self.files += st.files
        self.bytes += st.bytes
        self.skipped += st.skipped
        self.deleted += st.deleted
        self.errors.extend(st.errors)

    def to_json(self) -> dict:
        return {"files": self.files, "dbs": self.dbs, "mb": round(self.bytes / 1048576.0, 1),
                "seconds": round(self.seconds, 2), "skipped": self.skipped, "errors": len(self.errors)}


def is_db_name(name: str) -> bool:
    return name.lower().endswith(_DB_SUFFIXES)


def _is_sidecar(name: str) -> bool:
    return name.endswith(_SIDECAR_SUFFIXES)


def _name(rel: str) -> str:
    return rel.rpartition("/")[2]


def _utc_stamp(now: Optional[float] = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))


# --- 本人目录 -------------------------------------------------------------------

def open_user(users_fd: int, user_id: str) -> int:
    """打开(没有就建)`users/<工号>/`,确认可写。返回描述符,调用方关闭。

    建不出来 / 写不进去时说清属主:kubelet 按 subPath 建出来的目录属主是 root(v1.1.6 待修第 1 项),
    部署手册要求 NAS 上的 users/ 预先 chown 1000——没做的话这里就是第一个撞上的地方。
    """
    if not USER_ID_RE.match(user_id or ""):
        raise ValueError("工号格式不对:%r" % user_id)

    def _explain(fd: int, what: str) -> PoolError:
        st = os.fstat(fd)
        return PoolError("users_not_writable",
                         "%s 不可写:属主 uid=%d gid=%d、权限 %o,副容器以 uid=%d 运行。"
                         "请按部署手册在 NFS 服务器上对 users/ 执行 chown -R 1000:1000"
                         % (what, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode), os.getuid()))
    try:
        os.mkdir(user_id, 0o700, dir_fd=users_fd)
    except FileExistsError:
        pass
    except PermissionError:
        raise _explain(users_fd, "users/") from None
    try:
        fd = fdtree.open_dir(users_fd, user_id)
    except fdtree.UnsafePath:
        raise PoolError("unsafe_user_dir", "users/%s 不是普通目录(可能是符号链接),拒绝使用" % user_id) from None
    if fd is None:
        raise PoolError("user_dir_missing", "users/%s 建好之后又不见了" % user_id)
    probe = ".write-probe"
    try:
        fdtree.write_small(fd, probe, b"")
        fdtree.unlink(fd, probe)
    except PermissionError:
        err = _explain(fd, "users/%s" % user_id)
        os.close(fd)
        raise err from None
    return fd


# --- .owner 独占 ----------------------------------------------------------------

def _owner(user_fd: int) -> dict:
    try:
        raw = fdtree.read_small(user_fd, OWNER, limit=4096)
    except fdtree.UnsafePath:
        return {}               # 预先埋的链接 / 怪文件不是任何沙箱写的标记:当成没有,写时替换掉它
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_owner(user_fd: int, pod: str, now: float) -> None:
    fdtree.write_small(user_fd, OWNER, json.dumps({"pod": pod, "ts": now}).encode("utf-8"))


def owner_acquire(user_fd: int, pod: str, now: Optional[float] = None,
                  stale_s: int = OWNER_STALE_S) -> Tuple[bool, str, int]:
    """拿独占标记。返回 (拿到没有, 当前持有者, 还要等几秒才过期)。**不等待**——bind 立即回 409(方案 §7.1)。"""
    now = time.time() if now is None else now
    cur = _owner(user_fd)
    holder = str(cur.get("pod") or "")
    try:
        age = now - float(cur.get("ts") or 0)
    except (TypeError, ValueError):
        age = stale_s
    if not cur or holder == pod or age >= stale_s:
        _write_owner(user_fd, pod, now)
        return True, pod, 0
    return False, holder, max(1, int(stale_s - age + 0.999))


def owner_refresh(user_fd: int, pod: str, now: Optional[float] = None) -> bool:
    """还是自己持有才刷新;被别人接管了(本副容器停了 90 秒以上)返回 False——调用方必须停止回写。"""
    if _owner(user_fd).get("pod") != pod:
        return False
    _write_owner(user_fd, pod, time.time() if now is None else now)
    return True


def owner_release(user_fd: int, pod: str) -> None:
    if _owner(user_fd).get("pod") == pod:
        fdtree.unlink(user_fd, OWNER)


# --- dirty 标记 -----------------------------------------------------------------

def is_dirty(user_fd: int) -> bool:
    try:
        os.stat(MARK_DIRTY, dir_fd=user_fd, follow_symlinks=False)
        return True             # 不管是不是普通文件:宁可多留一份快照
    except FileNotFoundError:
        return False


def mark_dirty(user_fd: int, now: Optional[float] = None) -> None:
    fdtree.write_small(user_fd, MARK_DIRTY, str(int(time.time() if now is None else now)).encode())


def clear_dirty(user_fd: int) -> None:
    fdtree.unlink(user_fd, MARK_DIRTY)


# --- 载入 -----------------------------------------------------------------------

def _copy_sub(src_root: int, dst_root: int, sub: str, moved: Moved,
              skip: Optional[Callable[[str], bool]] = None) -> Optional[set]:
    """复制一个子目录。源不存在 → None;源是链接等 → 记错并返回 None。"""
    try:
        src = fdtree.open_path(src_root, sub, create=False)
    except fdtree.UnsafePath as exc:
        moved.errors.append("%s:%s" % (sub, exc))
        return None
    if src is None:
        return None
    try:
        dst = fdtree.open_path(dst_root, sub, create=True)
    except OSError as exc:
        os.close(src)
        moved.errors.append("%s:%s" % (sub, exc))
        return None
    try:
        st = fdtree.Stats()
        seen = fdtree.copy_tree(src, dst, stats=st,
                                skip=lambda rel: _is_sidecar(_name(rel)) or (skip is not None and skip(rel)))
        moved.add(st)
        return seen
    finally:
        os.close(src)
        os.close(dst)


def snapshot_unclean(user_fd: int, now: Optional[float] = None) -> str:
    """上次没走完收尾:覆盖之前先在本人目录里留一份(与现有模式同名同位置)。返回快照目录名。"""
    name = SNAPSHOT_PREFIX + "%d" % int(time.time() if now is None else now)
    for sub in POOL_LOCALIZED:
        try:
            src = fdtree.open_path(user_fd, sub, create=False)
        except fdtree.UnsafePath:
            continue
        if src is None:
            continue
        try:
            dst = fdtree.open_path(user_fd, name + "/" + sub, create=True)
            try:
                fdtree.copy_tree(src, dst, stats=fdtree.Stats(), only_if_changed=False)
            finally:
                os.close(dst)
        finally:
            os.close(src)
    return name


def load(user_fd: int, state_fd: int, log: Callable[[str], None] = print,
         now: Optional[float] = None) -> Tuple[Moved, bool]:
    """NAS → state 卷。库原样搬(NAS 上的库是静止的:独占在我们手里,写它的只有原子改名)。

    返回 (搬运统计, 上次是不是非正常退出)。
    """
    t0 = time.monotonic()
    unclean = is_dirty(user_fd)
    if unclean:
        snap = snapshot_unclean(user_fd, now)
        log("⚠ 上次是**非优雅**退出(dirty 标记还在):NAS 上那份已先快照到 %s/。"
            "最后一个同步周期内的对话可能已丢失。" % snap)
    moved = Moved()
    for sub in POOL_LOCALIZED:
        seen = _copy_sub(user_fd, state_fd, sub, moved)
        if seen is None:                # NAS 上还没有(新用户)或不可用:本地照样建出空目录
            os.close(fdtree.open_path(state_fd, sub, create=True))
            continue
        moved.dbs += sum(1 for rel in seen if is_db_name(_name(rel)))
    moved.seconds = time.monotonic() - t0
    return moved, unclean


# --- 库自检(只在 opencode 启动之前调用) -------------------------------------------

def selfcheck(state_dir: pathlib.Path, user_fd: int, state_fd: int,
              log: Callable[[str], None] = print) -> str:
    """返回 ok / absent / restored / reset。

    **只在 opencode 启动之前调用**:那时主容器还没执行过任何用户命令,state 卷可信,可以用 SQLite 按路径打开。
    NAS 上的备份不可信(可能是预先埋的链接),先按描述符拷进 state 卷再查,查过的才用。
    """
    db = state_dir / MAIN_DB
    if not db.exists():
        return "absent"
    if podctl._integrity_ok(db):
        return "ok"
    corrupt = db.with_name("opencode.db.corrupt-%s" % _utc_stamp())
    for suffix in ("", "-wal", "-shm"):
        src = db.with_name(db.name + suffix)
        if src.exists():
            src.rename(corrupt.with_name(corrupt.name + suffix))
    log("数据库自检: opencode.db 损坏,已改名为 %s 留证" % corrupt.name)
    try:
        bfd = fdtree.open_path(user_fd, BACKUP_DIR, create=False)
    except fdtree.UnsafePath:
        bfd = None
    if bfd is not None:
        try:
            dev = os.fstat(bfd).st_dev
            tdir = fdtree.open_path(state_fd, RESTORE_DIR, create=True)
            try:
                for name in reversed([n for n in fdtree.listdir(bfd) if podctl._BACKUP_RE.match(n)]):
                    try:
                        fdtree.copy_file(bfd, name, tdir, name, dev=dev, only_if_changed=False)
                    except fdtree.UnsafePath:
                        log("数据库自检: 备份 %s 不是普通文件(可能是链接),跳过" % name)
                        continue
                    cand = state_dir / RESTORE_DIR / name
                    if podctl._integrity_ok(cand):
                        os.replace(cand, db)
                        log("数据库自检: 已从备份 %s 恢复;该备份之后的对话丢失" % name)
                        return "restored"
                    log("数据库自检: 备份 %s 也损坏,跳过" % name)
                    fdtree.unlink(tdir, name)
            finally:
                os.close(tdir)
        finally:
            os.close(bfd)
    log("数据库自检: 没有可用备份,以空库启动;历史对话丢失,请从 NAS 快照找回")
    return "reset"


# --- 回写 -----------------------------------------------------------------------

Ident = Tuple[int, int, int]


def sync(user_fd: int, state_fd: int, snap_seen: Dict[str, Ident], may_delete: bool = True) -> Moved:
    """state 卷 → NAS。普通文件按 fdtree 增量搬;库**只搬主容器生成的快照**(.pool/snap/),
    state 卷上活着的库文件一概不碰。snap_seen 记各个快照上次搬过时的身份(inode、纳秒修改时间、大小),
    没变就不搬(M1 在副容器这一侧的样子)。

    某个子目录在 state 卷上整个不见了(或被换成链接)时**不对它传播删除**:那更像是误删,
    NAS 上那份是这个人全部的历史。
    """
    t0 = time.monotonic()
    moved = Moved()
    for sub in POOL_LOCALIZED:
        seen = _copy_sub(state_fd, user_fd, sub, moved, skip=lambda rel: is_db_name(_name(rel)))
        if seen is None:
            moved.errors.append("%s:state 卷上没有这个目录,本轮不对它传播删除" % sub)
            continue
        _sync_snapshots(user_fd, state_fd, sub, snap_seen, seen, moved)
        if may_delete:
            try:
                dst = fdtree.open_path(user_fd, sub, create=True)
            except OSError as exc:
                moved.errors.append("%s:%s" % (sub, exc))
                continue
            try:
                st = fdtree.Stats()
                fdtree.prune(dst, seen, stats=st,
                             protect=lambda rel: _is_sidecar(_name(rel)) or fdtree.is_temporary(_name(rel)))
                moved.add(st)
            finally:
                os.close(dst)
    moved.seconds = time.monotonic() - t0
    return moved


def _sync_snapshots(user_fd: int, state_fd: int, sub: str, snap_seen: Dict[str, Ident],
                    seen: set, moved: Moved) -> None:
    try:
        snap = fdtree.open_path(state_fd, SNAP_DIR + "/" + sub, create=False)
    except fdtree.UnsafePath as exc:
        moved.errors.append("%s 快照目录:%s" % (sub, exc))
        return
    if snap is None:
        return
    try:
        dev = os.fstat(state_fd).st_dev
        for rel, dfd, name, lst in fdtree.walk(snap):
            if not is_db_name(name):
                continue
            key = sub + "/" + rel
            ident = (lst.st_ino, lst.st_mtime_ns, lst.st_size)
            seen.add(rel)               # 快照在 = 库还在:不能被删除传播当成「已删」
            if snap_seen.get(key) == ident:
                continue
            parent, _sep, base = rel.rpartition("/")
            try:
                dst = fdtree.open_path(user_fd, sub + ("/" + parent if parent else ""), create=True)
            except OSError as exc:
                moved.errors.append("%s:%s" % (key, exc))
                continue
            try:
                n = fdtree.copy_file(dfd, name, dst, base, dev=dev, only_if_changed=False)
                moved.dbs += 1
                moved.bytes += n
                snap_seen[key] = ident
            except fdtree.UnsafePath:
                moved.skipped += 1
            except OSError as exc:
                moved.errors.append("%s:%s" % (key, exc))
            finally:
                os.close(dst)
    finally:
        os.close(snap)


def backup(user_fd: int, state_fd: int, keep: int = 3, now: Optional[float] = None) -> Optional[str]:
    """退出备份:把主容器的最终快照拷成 NAS 上的 backup/opencode.db.<UTC 时间戳>,保留最近 keep 份。没有快照 → None。"""
    parent, _sep, base = MAIN_DB.rpartition("/")
    try:
        sdir = fdtree.open_path(state_fd, SNAP_DIR + "/" + parent, create=False)
    except fdtree.UnsafePath:
        return None
    if sdir is None:
        return None
    try:
        bdir = fdtree.open_path(user_fd, BACKUP_DIR, create=True)
        try:
            target = "opencode.db.%s" % _utc_stamp(now)
            try:
                fdtree.copy_file(sdir, base, bdir, target, dev=os.fstat(state_fd).st_dev, only_if_changed=False)
            except FileNotFoundError:
                return None
            olds = [n for n in fdtree.listdir(bdir) if podctl._BACKUP_RE.match(n)]
            for old in (olds[:-keep] if keep > 0 else []):
                fdtree.unlink(bdir, old)
            return target
        finally:
            os.close(bdir)
    finally:
        os.close(sdir)
