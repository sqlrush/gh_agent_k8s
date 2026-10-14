"""k8s/tools/runtime-doctor.sh 的约束闸。

这个脚本是给客户现场一键诊断 / 一键修复用的:跑在客户的 Windows Git Bash 上,
会改客户集群里的出网策略与配置。下面每一条都对应一个真实的坑或一条不能破的约束。
行为本身在本地集群上端到端验过(复刻客户现场的四个问题),这里只钉住容易被改坏的写法。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "k8s" / "tools" / "runtime-doctor.sh"


def _code_lines():
    """去掉注释行之后的脚本行(注释里会提到 python/jq 之类的字眼)。"""
    return [l for l in _SCRIPT.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]


def test_ships_with_the_package_and_the_docs_point_to_it():
    """离线包只打 k8s/ 与 docs/:脚本必须在 k8s/ 下,手册里要有入口,客户才找得到。"""
    assert _SCRIPT.is_file()
    for name in ("部署手册-从零到上线.md", "仓库结构说明.md"):
        assert "runtime-doctor.sh" in (_ROOT / "docs" / name).read_text(encoding="utf-8"), name


def test_needs_only_bash_and_kubectl():
    """客户的 Git Bash 里不一定有 python,也没有 jq。"""
    code = "\n".join(_code_lines())
    for tool in ("python", "jq ", "base64"):
        assert tool not in code, "脚本用到了 %s —— 客户的 Git Bash 里不一定有" % tool.strip()


def test_disables_git_bash_path_conversion():
    """Git Bash 会把参数里像路径的 /spec/egress/... 改写成 Windows 路径,补丁就打歪了。"""
    assert re.search(r"^export MSYS_NO_PATHCONV=1", _SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)


def test_egress_probe_waits_before_testing():
    """**第一版踩过。** 出网策略对新 Pod 要过一会儿才生效(OrbStack 实测约 1 秒):
    探测抢在生效前跑,受约束的 Pod 也连得上,被挡的模型就误报成「通」。"""
    text = _SCRIPT.read_text(encoding="utf-8")
    probe = re.search(r"^PROBE_SH='(.*?)'$", text, re.MULTILINE | re.DOTALL)
    assert probe, "找不到 PROBE_SH"
    body = probe.group(1)
    m = re.search(r"sleep (\d+)", body)
    assert m and body.index("sleep") < body.index("curl"), "探测前必须先等策略生效"
    assert int(m.group(1)) >= 5


def test_never_prints_the_model_key():
    """读 Secret model-api 只为判断 key 填没填:只许数长度,不许解码或打印。"""
    lines = [l for l in _code_lines() if "secret model-api" in l]
    assert lines, "脚本不再检查 model-api?"
    for l in lines:
        assert "wc -c" in l, "读 model-api 的地方必须只数长度:" + l.strip()


def test_every_policy_patch_is_guarded_by_a_test_op():
    """改出网策略一律走 patch_rule:先 test 那条规则还是刚才看到的样子,再 replace。
    别人在这期间改过就拒绝,而不是按下标盲改、把别的规则覆盖掉。"""
    code = _code_lines()
    patch_calls = [l for l in code if "patch networkpolicy" in l]
    assert len(patch_calls) == 1, "patch networkpolicy 只应出现在 patch_rule 里:\n" + "\n".join(patch_calls)
    body = "\n".join(code)
    fn = body[body.index("patch_rule() {"):]
    fn = fn[:fn.index("\n}")]
    assert '\\"op\\":\\"test\\"' in fn and "patch networkpolicy" in fn
