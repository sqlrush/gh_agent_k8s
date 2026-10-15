"""k8s/tools/runtime-doctor.sh 的约束闸。

这个脚本是给客户现场一键诊断 / 一键修复用的:跑在客户的 Windows Git Bash 上,
会改客户集群里的出网策略与配置。下面每一条都对应一个真实的坑或一条不能破的约束。
行为本身在本地集群上端到端验过(复刻客户现场的问题);前半钉住容易被改坏的写法,
后半加载脚本里的函数(DOCTOR_LIB=1),直接喂探测结果核对判读与诊断意见。
"""
import os
import pathlib
import re
import subprocess
import textwrap

import pytest

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


# ---- 行为:加载脚本里的函数(DOCTOR_LIB=1)直接喂探测结果 ---------------------------------------


def _bash(snippet, env=None, args=()):
    """在 bash 里加载脚本的函数再跑 snippet;返回 (退出码, stdout+stderr)。"""
    full = {k: v for k, v in os.environ.items() if k.lower() != "https_proxy"}   # 本机的代理别混进来
    full.update(DOCTOR_LIB="1", **(env or {}))
    cmd = ["bash", "-c", '. "$0"; ' + snippet, str(_SCRIPT)] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, env=full, timeout=30)
    return p.returncode, p.stdout + p.stderr


def _run(args, path_dir=None):
    """直接执行脚本(不是加载函数);path_dir 放假的 kubectl / reg。"""
    env = dict(os.environ)
    env.pop("DOCTOR_LIB", None)
    if path_dir:
        env["PATH"] = "%s%s%s" % (path_dir, os.pathsep, env["PATH"])
    p = subprocess.run(["bash", str(_SCRIPT)] + list(args), capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.parametrize("rc,nc,want", [
    ("0", "1", "OK"),
    ("52", "1", "OK"),            # 连上了、对方没回内容:网络是通的
    ("28", "0", "TCP_TIMEOUT"),
    ("28", "1", "SLOW"),
    ("7", "0", "REFUSED"),        # OrbStack 的策略是拒绝而不是丢包:被挡也可能是这个
    ("6", "0", "DNS"),
    ("5", "0", "PROXY_DNS"),
    ("35", "1", "TLS"),
    ("60", "1", "CERT"),
    ("56", "1", "CUT"),
])
def test_classify_goes_by_whether_tcp_connected(rc, nc, want):
    """第二个参数是 curl 的 num_connects。不用 time_connect:curl 7.88 在 TLS 失败时把它报成 0。"""
    code, out = _bash('classify "%s" "%s"' % (rc, nc))
    assert code == 0 and out.strip() == want


# 现场照片的原样:受约束的 Pod 连接超时,不受约束的 Pod TCP 连上了但 TLS 握手失败
_RT_TIMEOUT = "MODEL 28 000 0 curl: (28) Connection timed out after 10002 milliseconds\nVIA "
_FREE_TLS = ("MODEL 35 000 1 curl: (35) OpenSSL SSL_connect: SSL_ERROR_SYSCALL "
             "in connection to api.deepseek.com:443\nVIA ")
_FREE_TIMEOUT = "MODEL 28 000 0 curl: (28) Connection timed out after 10001 milliseconds\nVIA "


def _verdict(rt, free, env=None):
    snippet = textwrap.dedent('''
        U=u0001; LP=${LP:-}; LP_SRC=${LP_SRC:-}; LP_PAC=""; MODEL_BLOCKED=0; GRMP_BLOCKED=0
        probe_verdict "模型服务" MODEL "$RT" "$FREE" "https://api.deepseek.com"
        echo "BLOCKED=$MODEL_BLOCKED"
        printf '%s' "$ADV"
    ''')
    return _bash(snippet, env=dict({"RT": rt, "FREE": free}, **(env or {})))[1]


def test_probe_counts_connects_not_connect_time():
    text = _SCRIPT.read_text(encoding="utf-8")
    body = re.search(r"^PROBE_SH='(.*?)'$", text, re.MULTILINE | re.DOTALL).group(1)
    assert "%{num_connects}" in body and "time_connect" not in body


def test_policy_block_is_found_even_when_tls_also_fails():
    """**现场踩过。** 第一版把「连接超时」和「TLS 握手失败」都算连不上,结论成了「集群本身不通」;
    其实不受约束的 Pod TCP 连上了 —— 是出网策略挡的,TLS 是放开之后的第二个问题,两条都要报。"""
    out = _verdict(_RT_TIMEOUT, _FREE_TLS)
    assert "BLOCKED=1" in out
    assert "被出网策略挡了" in out and "集群本身连不上" not in out
    assert "放开出网策略之后还会遇到" in out and "SSL_ERROR_SYSCALL" in out, "TLS 失败要原样带上 curl 的报错"
    assert "可以试:bash " in out and "u0001 --fix" in out


def test_both_time_out_means_the_cluster_cannot_reach_it():
    out = _verdict(_RT_TIMEOUT, _FREE_TIMEOUT)
    assert "BLOCKED=0" in out and "集群本身连不上" in out
    assert "--proxy" in out, "集群出不去时给经代理的修复命令"


def test_tls_failure_in_the_user_pod_is_not_blamed_on_the_policy():
    out = _verdict(_FREE_TLS, _FREE_TLS)
    assert "BLOCKED=0" in out and "被出网策略挡了" not in out
    assert "TLS 握手失败" in out and "代理" in out


def test_401_without_key_counts_as_reachable():
    out = _verdict("MODEL 0 401 1 \nVIA ", "MODEL 0 401 1 \nVIA ")
    assert "[正常]" in out and "没带 key" in out and "BLOCKED=0" in out


@pytest.mark.parametrize("lp,want", [
    ("10.1.2.9:8080", "u0001 --fix --proxy 10.1.2.9:8080"),
    ("127.0.0.1:7890", "连不到它"),
    ("proxy.corp.example:8080", "nslookup proxy.corp.example"),
])
def test_proxy_command_follows_this_machines_proxy(lp, want):
    out = _verdict(_RT_TIMEOUT, _FREE_TIMEOUT, env={"LP": lp, "LP_SRC": "Windows 系统代理"})
    assert want in out


def _fake_bin(tmp_path, name, body):
    f = tmp_path / name
    f.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    f.chmod(0o755)
    return tmp_path


def test_reads_the_windows_system_proxy(tmp_path):
    """Git Bash 里没有 HTTPS_PROXY 时,读 Windows 注册表里的系统代理(分协议写法取 https 的那个)。"""
    _fake_bin(tmp_path, "reg", textwrap.dedent(r'''
        case "$*" in
          *ProxyEnable*) printf '\r\nHKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\r\n    ProxyEnable    REG_DWORD    0x1\r\n\r\n' ;;
          *ProxyServer*) printf '\r\nHKEY_CURRENT_USER\\...\r\n    ProxyServer    REG_SZ    http=10.1.2.3:8080;https=10.1.2.4:8443\r\n\r\n' ;;
          *) exit 1 ;;
        esac
    '''))
    code, out = _bash('detect_local_proxy; echo "LP=$LP SRC=$LP_SRC"', env={"PATH": "%s%s%s" % (tmp_path, os.pathsep, os.environ["PATH"])})
    assert "LP=10.1.2.4:8443 SRC=Windows 系统代理" in out


def test_reads_the_proxy_env_var():
    code, out = _bash('detect_local_proxy; echo "LP=$LP"', env={"HTTPS_PROXY": "http://me:pw@10.1.2.9:3128/"})
    assert "LP=10.1.2.9:3128" in out, "账号密码与协议头要去掉,只留 IP:端口"


def test_advice_commands_are_one_per_line():
    code, out = _bash('advise "问题甲" "意见甲" "cmd one" "" "cmd two"; advise "问题乙" "意见乙"; printf "%s" "$ADV"')
    assert out.splitlines() == ["1. 问题甲", "   意见:意见甲", "   可以试:cmd one", "          cmd two",
                                "2. 问题乙", "   意见:意见乙"]


# ---- 用法错误:现场敲成 ./runtime-doctor.sh bash u0001 ------------------------------------------

def test_two_user_ids_are_refused_instead_of_taking_the_last():
    code, out = _run(["bash", "u0001"])
    assert code == 2 and "只能给一个工号" in out and "「bash」「u0001」" in out


@pytest.mark.parametrize("args,want", [
    (["u0001", "--proxy", "10.1.2.9:8080"], "要和 --fix 一起用"),
    (["u0001", "--fix", "--proxy", "proxy.corp:8080"], "要写成 IP:端口"),
    (["u0001", "--fix", "--grmp", "localhost:8080"], "要写成 IP:端口"),
])
def test_bad_options_are_refused(args, want):
    code, out = _run(args)
    assert code == 2 and want in out


def test_unknown_user_hints_at_a_typo(tmp_path):
    """没开通、也没登录过的工号:不往下诊断一堆「问题」,直接提示参数是不是写错了。"""
    _fake_bin(tmp_path, "kubectl", 'case "$*" in *"configmap agent-config"*) exit 0 ;; *) exit 1 ;; esac\n')
    code, out = _run(["bash"], path_dir=tmp_path)
    assert code == 2 and "参数写错了" in out and "grmp-token-bash" in out
