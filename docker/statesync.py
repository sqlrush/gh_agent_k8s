"""Pod 本地态 ⇄ NAS 同步(2026-09-21 策略变更)。

原策略:状态直接放在 NAS 上(`XDG_DATA_HOME=$NAS_ME/xdg-data`),每次写立刻落盘,
**零丢失窗口**,代价是 SQLite 跑在 NFS 上,写路径最慢的一环。

新策略:启动时 NAS → 本地,读写全在本地,定期回写,销毁前完整同步。
用户已确认接受「优雅关闭零丢失,非优雅关闭丢一个同步周期」。

**非优雅关闭包括**:OOMKilled(SIGKILL,没有 trap 的机会)、节点故障/断电、
`kubectl delete pod --force --grace-period=0`。这三种一个字节都同步不了,
是任何「本地读写 + 定期回写」方案的架构性代价,补不了 —— 只能让它可见(见 dirty 标记)。

四条硬规则:

① **SQLite 走 online backup API,绝不文件拷贝。** 开着 WAL 时主库/`-wal`/`-shm`
   三个文件不是任一瞬间都一致,拷出来的副本可能直接损坏,而且是静默的。
② **没 load 成功就不许传播删除。** 容器重建后本地是空的;这时跑一次「本地 → NAS」
   并传播删除,用户全部历史一次清空。所以删除只在 loaded 标记存在时才做。
③ **共享目录不本地化。** `/nas/kb` 是 runtime 读、kb-import 写的共享知识库,
   本地化意味着管理员刚导入的知识 runtime 看不到,**而且不报错**。
④ **加密凭据留在 NAS。** 本地化只是把密钥多复制一份到本地盘,没有性能收益。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

# 要本地化的子路径(相对 $NAS_ME)。**只加确实有性能收益的**,每加一项都放大丢失面。
LOCALIZED: Tuple[str, ...] = (
    "xdg-data",        # opencode.db 与 git 快照 —— 性能收益全在这
    "xdg-state",
    "workspace",       # git 仓 + 用户上传的文件
)
# **`gdaa` 整个留在 NAS**,两个原因:
#   · 它里面既有要本地化的 sessions/(登录句柄)又有绝不能本地化的 credentials/(加密凭据),
#     拆开就得靠符号链接 —— NFS + readOnlyRootFilesystem 上符号链接是 bug 的温床;
#   · 句柄是几百字节的小文件,本地化没有性能收益,不值得为它引入那份复杂度。
#   代价:非优雅退出时句柄还在而用那个句柄的对话丢了,用户重新 login 一次即可。

MARK_LOADED = ".state-loaded"
MARK_DIRTY = ".state-dirty"
SNAPSHOT_PREFIX = ".unclean-"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SKIP_SUFFIXES = ("-wal", "-shm", ".db-journal")   # 由 backup API 负责,别单独拷


@dataclass(frozen=True)
class Layout:
    nas: pathlib.Path            # $NAS_ME
    local: pathlib.Path          # /data/state

    def pairs(self) -> List[Tuple[pathlib.Path, pathlib.Path]]:
        return [(self.nas / p, self.local / p) for p in LOCALIZED]


@dataclass
class Report:
    files_copied: int = 0
    dbs_copied: int = 0
    files_deleted: int = 0
    bytes_copied: int = 0
    refused_delete: bool = False
    errors: List[str] = field(default_factory=list)

    def line(self) -> str:
        s = "状态同步:文件 %d(%.1f MB)· 库 %d · 删除 %d" % (
            self.files_copied, self.bytes_copied / 1048576.0, self.dbs_copied, self.files_deleted)
        if self.refused_delete:
            s += " · **未传播删除**(本地没有 load 成功的标记)"
        if self.errors:
            s += " · 失败 %d 项:%s" % (len(self.errors), "; ".join(self.errors[:3]))
        return s


# --- 标记 ---------------------------------------------------------------------

def loaded(lay: Layout) -> bool:
    return (lay.local / MARK_LOADED).exists()


def is_dirty(lay: Layout) -> bool:
    return (lay.nas / MARK_DIRTY).exists()


def mark_dirty(lay: Layout) -> None:
    """开始用本地态了。走完收尾才清掉;下次启动还看得见就说明上次是非优雅退出。"""
    lay.nas.mkdir(parents=True, exist_ok=True)
    (lay.nas / MARK_DIRTY).write_text(str(int(time.time())), encoding="utf-8")


def clear_dirty(lay: Layout) -> None:
    try:
        (lay.nas / MARK_DIRTY).unlink()
    except FileNotFoundError:
        pass


# --- SQLite -------------------------------------------------------------------

def _is_db(p: pathlib.Path) -> bool:
    return p.suffix.lower() in _DB_SUFFIXES


def copy_db(src: pathlib.Path, dst: pathlib.Path) -> None:
    """一致性快照。**不要换成 shutil.copy** —— 见模块开头第 ① 条。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    s = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
    try:
        d = sqlite3.connect(tmp)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()
    os.replace(tmp, dst)          # 原子替换:同步中途被杀也不会留下半截的库


# --- 复制 ---------------------------------------------------------------------

def _changed(src: pathlib.Path, dst: pathlib.Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except FileNotFoundError:
        return True
    return a.st_size != b.st_size or int(a.st_mtime) > int(b.st_mtime)


def _copy_tree(src_root: pathlib.Path, dst_root: pathlib.Path, rep: Report) -> Set[str]:
    """复制 src_root → dst_root,返回相对路径集合(给删除传播用)。"""
    seen: Set[str] = set()
    if not src_root.exists():
        dst_root.mkdir(parents=True, exist_ok=True)
        return seen
    for cur, _dirs, files in os.walk(src_root):
        rel_dir = pathlib.Path(cur).relative_to(src_root)
        (dst_root / rel_dir).mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith(_SKIP_SUFFIXES) or name.endswith(".part"):
                continue
            src = pathlib.Path(cur) / name
            rel = str(rel_dir / name)
            seen.add(rel)
            dst = dst_root / rel_dir / name
            try:
                if _is_db(src):
                    # **库一律复制,不做 mtime/size 比较。** copy_db 写出去的目标 mtime 是
                    # 「同步那一刻」,总比源新,所以「源比目标新」这个判据会漏掉与同步
                    # 发生在同一秒内的写入;而 SQLite 原地改页时 size 也可能不变。
                    # 一次 backup 对几百 KB 的库是微秒级,不值得为省这点 IO 冒漏同步的风险。
                    # 库大到让 60 秒周期扛不住时,该调大 STATE_SYNC_SECONDS,不是在这里加判断。
                    copy_db(src, dst)
                    rep.dbs_copied += 1
                elif _changed(src, dst):
                    shutil.copy2(src, dst)
                    rep.files_copied += 1
                    rep.bytes_copied += src.stat().st_size
            except (OSError, sqlite3.Error) as exc:
                # 一个文件坏了不能让整次同步停摆 —— 记下来,其余照抄
                rep.errors.append("%s:%s" % (rel, exc))
    return seen


def _prune(dst_root: pathlib.Path, keep: Set[str], rep: Report) -> None:
    if not dst_root.exists():
        return
    for cur, _dirs, files in os.walk(dst_root):
        rel_dir = pathlib.Path(cur).relative_to(dst_root)
        for name in files:
            if name in (MARK_LOADED, MARK_DIRTY) or name.endswith(_SKIP_SUFFIXES):
                continue
            rel = str(rel_dir / name)
            if rel in keep:
                continue
            try:
                (pathlib.Path(cur) / name).unlink()
                rep.files_deleted += 1
            except OSError as exc:
                rep.errors.append("删 %s:%s" % (rel, exc))


# --- 对外三个动作 -------------------------------------------------------------

def load(lay: Layout, log: Callable[[str], None] = print) -> Report:
    """启动:NAS → 本地。上次是非优雅退出时,先给 NAS 留一份快照。"""
    rep = Report()
    if is_dirty(lay):
        # 快照必须放在 **$NAS_ME 里面**。`/nas` 本身是只读的(只有 /nas/me 与 /nas/kb
        # 这两个 subPath 挂载点可写),放同级目录永远建不出来 —— 2026-09-21 真跑时
        # 报的是 `[Errno 30] Read-only file system: '/nas/me.unclean-…'`。
        # 只快照 LOCALIZED 那几个子目录:既避免把快照目录自己也拷进去,又够用。
        snap = lay.nas / (SNAPSHOT_PREFIX + "%d" % int(time.time()))
        try:
            for sub in LOCALIZED:
                src = lay.nas / sub
                if src.exists():
                    shutil.copytree(src, snap / sub, dirs_exist_ok=True)
            log("⚠ 上次是**非优雅**退出(dirty 标记还在):NAS 上那份已先快照到 %s/。"
                "这次启动会用它继续,但上次最后一个同步周期内的对话可能已丢失。"
                % snap.relative_to(lay.nas.parent))
        except OSError as exc:
            log("⚠ 上次是**非优雅**退出,但快照失败(%s);继续启动 —— "
                "上次最后一个同步周期内的对话可能已丢失。" % exc)
    lay.local.mkdir(parents=True, exist_ok=True)
    for nas_dir, local_dir in lay.pairs():
        _copy_tree(nas_dir, local_dir, rep)
    (lay.local / MARK_LOADED).write_text(str(int(time.time())), encoding="utf-8")
    log("状态已加载到本地:%s" % rep.line())
    return rep


def sync(lay: Layout, log: Callable[[str], None] = lambda _m: None) -> Report:
    """定期 / 收尾:本地 → NAS。没有 loaded 标记时**不传播删除**(见第 ② 条)。"""
    rep = Report()
    may_delete = loaded(lay)
    rep.refused_delete = not may_delete
    for nas_dir, local_dir in lay.pairs():
        seen = _copy_tree(local_dir, nas_dir, rep)
        if may_delete:
            _prune(nas_dir, seen, rep)
    log(rep.line())
    return rep


def finish(lay: Layout, log: Callable[[str], None] = print) -> Report:
    """销毁前:完整同步一次,然后清掉 dirty 标记 —— 顺序不能反。"""
    rep = sync(lay, log=lambda _m: None)
    clear_dirty(lay)
    log("收尾同步完成并已清 dirty 标记。%s" % rep.line())
    return rep
