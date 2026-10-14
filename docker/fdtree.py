"""按文件描述符的目录树操作:读写方不信任目录内容时用(池化方案 §9,2026-10-14 评审)。

**为什么不按路径。** 池化的副容器挂着全部用户目录(`/pool/users`),却要搬运主容器可写的 state 卷——
而主容器里跑着能执行命令的模型。按路径复制时:
  · `workspace/x → /pool/users/u9999/…` 这样一个链接,就让副容器把别人的历史拷进本人目录;
  · 「先 lstat、是链接就跳过」也不够:模型在后台循环把同一个名字在普通文件与链接之间来回换,
    检查时是普通文件、打开时已是链接(检查与使用之间被抢先);
  · 中间某一级目录换成链接,只查最后一级的做法照样被带出去;
  · 链接指向 `/proc/self/environ`,搬走的是副容器自己的控制密钥。
NAS 上的本人目录同样不可信:按需模式下主容器对它可写,能预先埋下链接,
而链接里的路径字符串到了副容器里会被解析成 `/pool/users/…`。

**这里的做法**:从根目录的描述符出发,每一级都用 `O_NOFOLLOW` 打开(目录再加 `O_DIRECTORY`);
文件打开**之后**再 `fstat` 确认是普通文件、且与根目录在同一设备上。判断与读取针对的是同一个
已经打开的对象,中途换成链接也换不走。写入同样逐级不跟随,临时文件 `O_EXCL` 创建再原子改名
(改名替换的是目录项本身,目标是链接时替换掉链接,不会写穿到链接指向的地方)。

只用标准库;Linux(镜像)与 macOS(开发机跑测试)上行为一致(2026-10-14 实测)。
"""
from __future__ import annotations

import errno
import os
import secrets
import stat
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Set, Tuple

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC
# O_NONBLOCK:名字是 FIFO 时打开不会卡住(之后 fstat 认出它不是普通文件再拒)
_O_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC
_O_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC
_CHUNK = 1 << 20
_TMP_SUFFIX = ".part"
# 打开链接时的报错:文件是 ELOOP,目录(O_DIRECTORY|O_NOFOLLOW)是 ENOTDIR;EMLINK 是部分 BSD 的写法
_LINKISH = (errno.ELOOP, errno.ENOTDIR, errno.EMLINK)


class UnsafePath(OSError):
    """名字指向的不是普通文件 / 普通目录(链接、FIFO、设备、跨设备)——不跟随,也不读写。"""


@dataclass
class Stats:
    files: int = 0          # 实际写出的文件数
    bytes: int = 0
    skipped: int = 0        # 链接、FIFO 等被拒绝的条目
    deleted: int = 0
    errors: List[str] = field(default_factory=list)


# --- 打开 ---------------------------------------------------------------------

def open_root(path) -> int:
    """根目录是我方自己的挂载点,路径本身可信;但最后一级也不许是链接(防配置写错)。"""
    return os.open(os.fspath(path), _O_DIR)


def _split(rel: str) -> List[str]:
    if rel in ("", None):
        return []
    parts = rel.split("/")
    if rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError("相对路径不合法:%r" % rel)
    return parts


def open_dir(parent_fd: int, name: str) -> Optional[int]:
    """打开一级子目录。不存在 → None;是链接或不是目录 → UnsafePath。"""
    try:
        return os.open(name, _O_DIR, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in _LINKISH:
            raise UnsafePath(exc.errno, "不是普通目录(可能是符号链接),不跟随:%s" % name) from None
        raise


def open_path(root_fd: int, rel: str, create: bool, mode: int = 0o700) -> Optional[int]:
    """逐级打开 rel(`/` 分隔,不许 `..`、`.`、空段、绝对路径)。

    create=True 时缺的目录建出来;create=False 时任一级不存在 → None。返回的描述符由调用方关闭。
    """
    cur = os.dup(root_fd)
    try:
        for part in _split(rel):
            if create:
                try:
                    os.mkdir(part, mode, dir_fd=cur)
                except FileExistsError:
                    pass
            nxt = open_dir(cur, part)
            if nxt is None:
                os.close(cur)
                return None
            os.close(cur)
            cur = nxt
        return cur
    except BaseException:
        os.close(cur)
        raise


# --- 遍历 ---------------------------------------------------------------------

def walk(root_fd: int, prefix: str = "") -> Iterator[Tuple[str, int, str, os.stat_result]]:
    """深度优先列出所有**非目录**条目:(相对路径, 所在目录描述符, 名字, lstat)。

    子目录一律经 open_dir 进入(拒绝链接),所以指向别处的目录链接只会作为一个条目出现、不会被进入。
    yield 出的目录描述符只在迭代到下一个目录之前有效。
    """
    # **列目录用一个现开的描述符,不用 root_fd 本身。** overlayfs 上(容器的 /tmp)对「打开之后才新建了文件」
    # 的目录描述符,第一次 scandir 返回空列表、第二次才正常(2026-10-14 在 python:3.12-slim 里实测;
    # ext4 / tmpfs 上没有这个现象)。复制方向上那等于「源目录是空的」,删除传播方向上更糟。
    lfd = os.open(".", _O_DIR, dir_fd=root_fd)
    try:
        with os.scandir(lfd) as it:
            names = sorted(e.name for e in it)
    finally:
        os.close(lfd)
    for name in names:
        try:
            st = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(st.st_mode):
            try:
                sub = open_dir(root_fd, name)
            except UnsafePath:
                # 列出时是目录、打开时已被换成链接:当成一个不可信条目交给调用方计数
                yield prefix + name, root_fd, name, os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                continue
            if sub is None:
                continue
            try:
                yield from walk(sub, prefix + name + "/")
            finally:
                os.close(sub)
        else:
            yield prefix + name, root_fd, name, st


# --- 读写单个文件 ---------------------------------------------------------------

def _open_regular(dir_fd: int, name: str, dev: Optional[int]) -> Tuple[int, os.stat_result]:
    try:
        fd = os.open(name, _O_READ, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in _LINKISH or exc.errno == errno.ENXIO:     # ENXIO:套接字
            raise UnsafePath(exc.errno, "不是普通文件(可能是符号链接),不读:%s" % name) from None
        raise
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise UnsafePath(errno.EINVAL, "不是普通文件,不读:%s" % name)
    if dev is not None and st.st_dev != dev:
        os.close(fd)
        raise UnsafePath(errno.EXDEV, "不在同一设备上,不读:%s" % name)
    return fd, st


def _tmp_name(name: str) -> str:
    return ".%s.%s%s" % (name, secrets.token_hex(6), _TMP_SUFFIX)


def is_temporary(name: str) -> bool:
    """本模块(以及老的 statesync)写到一半留下的临时文件:两个方向都不搬。"""
    return name.endswith(_TMP_SUFFIX)


def _write_atomic(dst_dir_fd: int, name: str, chunks: Callable[[int], None], mode: int,
                  mtime_ns: Optional[Tuple[int, int]] = None) -> None:
    tmp = _tmp_name(name)
    out = os.open(tmp, _O_NEW, mode, dir_fd=dst_dir_fd)
    try:
        try:
            chunks(out)
            os.fchmod(out, mode)            # 不受 umask 影响
            if mtime_ns is not None:
                os.utime(out, ns=mtime_ns)
        finally:
            os.close(out)
        os.replace(tmp, name, src_dir_fd=dst_dir_fd, dst_dir_fd=dst_dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dst_dir_fd)
        except OSError:
            pass
        raise


def _granularity(ns: int) -> int:
    for g in (1_000_000_000, 1_000_000, 1_000):
        if ns % g == 0:
            return g
    return 1


def _differs(st: os.stat_result, dst_dir_fd: int, name: str) -> bool:
    """按目标的时间精度比:NAS 只存到秒(或微秒)时,逐纳秒比较会让每个周期都重拷。"""
    try:
        d = os.stat(name, dir_fd=dst_dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if not stat.S_ISREG(d.st_mode) or d.st_size != st.st_size:
        return True
    g = _granularity(d.st_mtime_ns)
    return st.st_mtime_ns // g != d.st_mtime_ns // g


def copy_file(src_dir_fd: int, name: str, dst_dir_fd: int, dst_name: str, *,
              dev: Optional[int] = None, only_if_changed: bool = True) -> int:
    """复制一个普通文件;返回写出的字节数(没变则 0)。不是普通文件 / 跨设备 → UnsafePath。"""
    fd, st = _open_regular(src_dir_fd, name, dev)
    try:
        if only_if_changed and not _differs(st, dst_dir_fd, dst_name):
            return 0

        def pump(out: int) -> None:
            while True:
                buf = os.read(fd, _CHUNK)
                if not buf:
                    break
                view = memoryview(buf)
                while view:
                    view = view[os.write(out, view):]

        _write_atomic(dst_dir_fd, dst_name, pump, stat.S_IMODE(st.st_mode) or 0o600,
                      (st.st_atime_ns, st.st_mtime_ns))
        return st.st_size
    finally:
        os.close(fd)


def read_small(dir_fd: int, name: str, limit: int = 65536) -> Optional[bytes]:
    """读一个小文件(标记、回执)。不存在 → None;链接 / 非普通文件 / 超过 limit → UnsafePath。"""
    try:
        fd, st = _open_regular(dir_fd, name, None)
    except FileNotFoundError:
        return None
    try:
        if st.st_size > limit:
            raise UnsafePath(errno.EFBIG, "文件超过 %d 字节,不读:%s" % (limit, name))
        return os.read(fd, limit + 1)[:limit]
    finally:
        os.close(fd)


def write_small(dir_fd: int, name: str, data: bytes, mode: int = 0o600) -> None:
    """原子写一个小文件。目标是预先埋的链接时替换掉链接本身,不写穿。"""
    _write_atomic(dir_fd, name, lambda out: os.write(out, data), mode)


def unlink(dir_fd: int, name: str) -> bool:
    """删一个目录项(链接删的是链接本身)。不存在 → False。"""
    try:
        os.unlink(name, dir_fd=dir_fd)
        return True
    except FileNotFoundError:
        return False


# --- 整棵树 -------------------------------------------------------------------

def _parent_and_name(rel: str) -> Tuple[str, str]:
    head, _sep, tail = rel.rpartition("/")
    return head, tail


class _DirCache:
    """walk 按目录成组产出文件,目标目录只缓存当前这一个,不攒一堆描述符。"""

    def __init__(self, root_fd: int):
        self.root_fd = root_fd
        self.rel: Optional[str] = None
        self.fd: Optional[int] = None

    def get(self, rel_dir: str) -> int:
        if rel_dir != self.rel or self.fd is None:
            self.close()
            self.fd = open_path(self.root_fd, rel_dir, create=True)     # 抛错时 fd 仍是 None
            self.rel = rel_dir
        return self.fd

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.rel, self.fd = None, None


def copy_tree(src_root_fd: int, dst_root_fd: int, *, stats: Stats,
              skip: Optional[Callable[[str], bool]] = None, only_if_changed: bool = True) -> Set[str]:
    """把 src 下所有普通文件复制到 dst 同样的相对位置;返回见到的相对路径集合(给删除传播用)。

    链接、FIFO、跨设备的条目计入 stats.skipped,不进返回集合——它们在源里「不算文件」,
    目标里同名的旧文件因此会被删除传播清掉(镜像语义)。出错的条目记 errors 但**留在集合里**:
    一次 IO 错误不该让 NAS 上那份被删。
    """
    dev = os.fstat(src_root_fd).st_dev
    seen: Set[str] = set()
    dirs = _DirCache(dst_root_fd)
    try:
        for rel, dfd, name, lst in walk(src_root_fd):
            if is_temporary(name) or (skip is not None and skip(rel)):
                continue
            if not stat.S_ISREG(lst.st_mode):
                stats.skipped += 1
                continue
            parent, base = _parent_and_name(rel)
            try:
                dst_dir = dirs.get(parent)
            except OSError as exc:              # 含 UnsafePath:目标那边某一级目录被埋了链接
                seen.add(rel)
                stats.errors.append("%s:%s" % (rel, exc))
                continue
            try:
                n = copy_file(dfd, name, dst_dir, base, dev=dev, only_if_changed=only_if_changed)
            except UnsafePath:                  # 源里的条目打开时已不是普通文件(被抢先换掉)
                stats.skipped += 1
                continue
            except OSError as exc:
                seen.add(rel)
                stats.errors.append("%s:%s" % (rel, exc))
                continue
            seen.add(rel)
            if n or not only_if_changed:
                stats.files += 1
                stats.bytes += n
    finally:
        dirs.close()
    return seen


def prune(root_fd: int, keep: Set[str], *, stats: Stats,
          protect: Optional[Callable[[str], bool]] = None) -> None:
    """删掉 root 下不在 keep 里的非目录条目(含链接本身,不跟随);protect 为真的跳过。目录保留。"""
    for rel, dfd, name, _st in walk(root_fd):
        if rel in keep or (protect is not None and protect(rel)):
            continue
        try:
            if unlink(dfd, name):
                stats.deleted += 1
        except OSError as exc:
            stats.errors.append("删 %s:%s" % (rel, exc))
