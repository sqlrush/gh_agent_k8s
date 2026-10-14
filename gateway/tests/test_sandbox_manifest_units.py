"""池化沙箱清单的守卫(方案 docs/specs/2026-10-13-sandbox-pool-design.md §9、§11)。

这套隔离有三条是**清单**决定的,代码管不到,改一行 YAML 就悄悄没了:
  ① 两个容器不共享进程空间——§9.1 实测:打开后主容器经 /proc 读到了副容器挂的 users/ 与控制密钥;
  ② 只有副容器挂 users/、只有副容器有控制密钥;
  ③ 两个容器都只读根、禁止提权、去掉全部特权。
所以按结构解析模板逐条核对,不靠字符串匹配(注释里会出现这些词)。
"""
import pathlib
import string
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "docker"))

import poolctl  # noqa: E402
import poolrun  # noqa: E402

_TEMPLATE = _ROOT / "k8s" / "templates" / "sandbox.yaml"


@pytest.fixture(scope="module")
def pod():
    text = string.Template(_TEMPLATE.read_text(encoding="utf-8")).substitute(
        SANDBOX_ID="07", IMAGE="gaussdb-agent-runtime:t", IMAGE_PULL_POLICY="IfNotPresent")
    return yaml.safe_load(text)


def _c(pod, name):
    return next(c for c in pod["spec"]["containers"] if c["name"] == name)


def _env(c):
    return {e["name"]: e for e in c.get("env", [])}


def test_renders_to_a_bare_pod_with_only_the_three_placeholders(pod):
    """裸 Pod:沙箱生死由平台定;Deployment 会自己补建,与平台的补建叠加就多出沙箱。"""
    assert pod["kind"] == "Pod" and pod["metadata"]["name"] == "sandbox-07"
    assert pod["metadata"]["labels"] == {"app": "gaussdb-agent", "role": "sandbox", "sandbox": "07"}
    assert {c["name"] for c in pod["spec"]["containers"]} == {"sandbox", "poolctl"}
    assert all(c["image"] == "gaussdb-agent-runtime:t" for c in pod["spec"]["containers"]), "两个容器同一个镜像"


def test_the_two_containers_never_share_a_process_namespace(pod):
    assert pod["spec"].get("shareProcessNamespace", False) is False


def test_only_the_sidecar_sees_users_and_only_it_holds_the_control_key(pod):
    main, side = _c(pod, "sandbox"), _c(pod, "poolctl")
    nas = [m for m in main["volumeMounts"] if m["name"] == "nas"]
    assert nas == [{"name": "nas", "mountPath": "/nas/kb", "subPath": "kb", "readOnly": True}], \
        "主容器只许只读挂共享知识库,不许挂 users/"
    assert not any("/pool" in m["mountPath"] for m in main["volumeMounts"])
    assert [m.get("subPath") for m in side["volumeMounts"] if m["name"] == "nas"] == ["users"]
    assert _env(side)["SANDBOX_CONTROL_KEY"]["valueFrom"]["secretKeyRef"]["name"] == "sandbox-control"
    main_text = yaml.safe_dump(main)
    assert "sandbox-control" not in main_text and "SANDBOX_CONTROL_KEY" not in main_text, "控制密钥只在副容器"
    ctl = next(m for m in main["volumeMounts"] if m["name"] == "ctl")
    assert ctl.get("readOnly") is True, "指令目录在主容器里必须只读,否则模型能给自己换工号"


def test_both_containers_are_locked_down(pod):
    spec = pod["spec"]
    assert spec["securityContext"]["runAsNonRoot"] is True and spec["securityContext"]["runAsUser"] == 1000
    assert spec["enableServiceLinks"] is False
    assert spec["automountServiceAccountToken"] is False, "沙箱不需要 k8s API,模型拿到令牌就是多一个面"
    for c in spec["containers"]:
        sc = c["securityContext"]
        assert sc["readOnlyRootFilesystem"] is True, c["name"]
        assert sc["allowPrivilegeEscalation"] is False, c["name"]
        assert sc["capabilities"]["drop"] == ["ALL"], c["name"]
        assert "privileged" not in sc and not sc.get("capabilities", {}).get("add"), c["name"]


def test_main_gets_the_shared_grmp_token_and_pool_mode(pod):
    env = _env(_c(pod, "sandbox"))
    assert env["POOL_MODE"]["value"] == "1"
    assert env["GRMP_AUTH_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "grmp-token", "池化下令牌全体共用(R2)"
    assert "GSDB_USER_ID" not in env, "工号在绑定时才知道,不能写死在模板里"
    assert "OPENCODE_SERVER_PASSWORD" not in env, "口令每次绑定随机生成,不走 Secret"


def test_grace_period_covers_the_stop_handshake(pod):
    cfg = poolctl.Config(key="k" * 16, sandbox_id="x", pod="x")
    assert pod["spec"]["terminationGracePeriodSeconds"] >= cfg.stop_wait_seconds + 30, \
        "删 Pod 时副容器要等主容器的最终回执(%ds)再回写" % cfg.stop_wait_seconds


def test_mount_paths_match_the_code_defaults(pod):
    """模板与代码各写一份路径:对不上的表现是「绑定成功、什么都没载入」或「指令永远收不到」。"""
    main = {m["name"]: m["mountPath"] for m in _c(pod, "sandbox")["volumeMounts"]}
    side = {m["name"]: m["mountPath"] for m in _c(pod, "poolctl")["volumeMounts"]}
    rc = poolrun.Config()
    cc = poolctl.Config(key="k" * 16, sandbox_id="x", pod="x")
    assert main["ctl"] == str(rc.ctl_dir) and main["state"] == str(rc.state)
    assert main["nas"] == str(rc.kb_dir)
    assert side["ctl"] == str(cc.ctl_dir) and side["state"] == str(cc.state_dir)
    assert side["nas"] == str(cc.users_dir) and side["poolpriv"] == str(cc.priv_dir)
    probe = _c(pod, "poolctl")["readinessProbe"]["httpGet"]
    assert probe == {"path": "/healthz", "port": cc.port}


def test_emptydirs_are_bounded_and_on_disk(pod):
    for v in pod["spec"]["volumes"]:
        if "emptyDir" in v:
            assert "sizeLimit" in v["emptyDir"] and "medium" not in v["emptyDir"], v


def _policies(path):
    return {d["metadata"]["name"]: d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d}


@pytest.mark.parametrize("path", [_ROOT / "k8s" / "base" / "networkpolicy.yaml",
                                  _ROOT / "k8s" / "local" / "networkpolicy-local.yaml"], ids=["base", "local"])
def test_sandbox_policy_admits_only_the_platform_and_never_nfs(path):
    pol = _policies(path)["sandbox-policy"]["spec"]
    assert pol["podSelector"]["matchLabels"] == {"app": "gaussdb-agent", "role": "sandbox"}
    assert set(pol["policyTypes"]) == {"Ingress", "Egress"}
    ports = {p["port"] for rule in pol["ingress"] for p in rule["ports"]}
    assert ports == {4096, 4097, 4098}
    for rule in pol["ingress"]:
        for src in rule["from"]:
            sel = (src.get("podSelector") or src.get("namespaceSelector") or {}).get("matchLabels", {})
            assert sel == {"gaussdb-agent/platform": "true"}, "入站只放行客户平台:%s" % src
    out_ports = {p["port"] for rule in pol["egress"] for p in rule.get("ports", [])}
    assert 2049 not in out_ports and 111 not in out_ports, "不能放行 NFS:模型会自己去连 NFS 服务器"


def test_vectordb_admits_the_sandbox():
    pol = _policies(_ROOT / "k8s" / "base" / "networkpolicy.yaml")["vectordb-policy"]["spec"]
    roles = {s["podSelector"]["matchLabels"].get("role") for r in pol["ingress"] for s in r["from"]}
    assert "sandbox" in roles


def test_secret_examples_for_the_pool_exist_and_are_not_applied_by_kustomize():
    base = _ROOT / "k8s" / "base"
    for name, key in (("secret-sandbox-control.example.yaml", "KEY"), ("secret-grmp-token.example.yaml", "GRMP_AUTH_TOKEN")):
        doc = yaml.safe_load((base / name).read_text(encoding="utf-8"))
        assert doc["kind"] == "Secret" and key in doc["stringData"]
        assert name not in (base / "kustomization.yaml").read_text(encoding="utf-8").split("resources:")[1].split("#")[0]
