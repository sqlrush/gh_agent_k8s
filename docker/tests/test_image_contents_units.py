"""entrypoint / podctl 用到的东西必须真的在镜像里。

2026-09-21 踩到:新增 docker/statesync.py,Dockerfile 的 COPY 只列了 podctl.py 与
entrypoint.sh,结果 Pod 起来就 `MododuleNotFoundError: No module named 'statesync'`,
探针 120 秒不过。**单测全绿,镜像却起不来** —— 因为没有一条测试看过 Dockerfile。

这条闸很便宜:凡是 docker/ 下被 podctl 或 entrypoint 引用到的 .py,都要出现在 COPY 行里。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "docker" / "Dockerfile"
_PODCTL = _ROOT / "docker" / "podctl.py"
_ENTRY = _ROOT / "docker" / "entrypoint.sh"


def _copied_files() -> set:
    """Dockerfile 里 COPY 进 /opt/agent/ 的文件名。"""
    out = set()
    for m in re.finditer(r"^COPY\s+(.+?)\s+/opt/agent/\s*$", _DOCKERFILE.read_text(encoding="utf-8"),
                         re.MULTILINE):
        for token in m.group(1).split():
            out.add(pathlib.PurePosixPath(token).name)
    return out


def test_podctl_and_entrypoint_are_copied():
    assert {"podctl.py", "entrypoint.sh"} <= _copied_files()


def test_every_local_module_podctl_imports_is_copied():
    """podctl 里 `import X` 且 docker/X.py 存在 → X.py 必须也被 COPY 进镜像。"""
    src = _PODCTL.read_text(encoding="utf-8")
    copied = _copied_files()
    missing = []
    for name in set(re.findall(r"^\s*import\s+([a-z_][a-z0-9_]*)\s*$", src, re.MULTILINE)):
        if (_ROOT / "docker" / ("%s.py" % name)).is_file() and ("%s.py" % name) not in copied:
            missing.append(name)
    assert not missing, "podctl 会 import 但镜像里没有:%s —— Pod 一起来就 ModuleNotFoundError" % missing


def _registered_subcommands() -> set:
    """podctl 里注册过的子命令:直接 add_parser("x") 的,和 for name in (...) 循环注册的。"""
    src = _PODCTL.read_text(encoding="utf-8")
    names = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', src))
    for group in re.findall(r"for name in \(([^)]*)\):", src):
        names |= {t.strip().strip('"').strip("'") for t in group.split(",") if t.strip()}
    return names


def test_entrypoint_only_calls_podctl_subcommands_that_exist():
    """entrypoint 调的每个 `$PODCTL <子命令>` 都要注册过。

    拼错一个子命令,表现是 Pod 启动到那一行就退出(argparse 退 2 而 set -e 直接终止),
    日志里只有一句 usage —— 看着像参数问题,其实是名字打错了。
    """
    used = set(re.findall(r"\$PODCTL\s+([a-z][a-z-]*)", _ENTRY.read_text(encoding="utf-8")))
    unknown = sorted(used - _registered_subcommands())
    assert not unknown, "entrypoint 调了 podctl 没注册的子命令:%s" % unknown


def test_the_new_state_subcommands_are_wired():
    """状态策略的三个动作都要能从命令行调到 —— 少一个 entrypoint 就断在那一步。"""
    assert {"state-load", "state-sync", "state-finish"} <= _registered_subcommands()
