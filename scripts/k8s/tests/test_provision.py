"""provision.py:网关「按工号建 Pod」那一步的确定性部分——角色判定、模板渲染。"""
import pathlib
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts" / "k8s"))

import provision  # noqa: E402


def test_is_kb_admin_reads_the_mock_group():
    assert provision.is_kb_admin("u2001\nu2002\n", "u2002") is True
    assert provision.is_kb_admin("u2001\nu2002\n", "u1001") is False
    assert provision.is_kb_admin("", "u2001") is False


def test_plan_gives_everyone_runtime_and_admins_kb_import():
    assert provision.plan("u1001", "u2001\n", auto_role=True, kb_admin_flag=False) == ["runtime"]
    assert provision.plan("u2001", "u2001\n", auto_role=True, kb_admin_flag=False) == ["runtime", "kb-import"]
    assert provision.plan("u1001", "", auto_role=False, kb_admin_flag=True) == ["runtime", "kb-import"]


def _docs(name: str, **vars_):
    text = provision.render((_ROOT / "k8s" / "templates" / f"{name}.yaml").read_text(encoding="utf-8"), **vars_)
    return list(yaml.safe_load_all(text))


def test_runtime_template_renders_spec_contract():
    docs = _docs("runtime", USER_ID="u1001", IMAGE="gaussdb-agent-runtime:dev", IMAGE_PULL_POLICY="IfNotPresent",
                 SUBPATH_ROOT="users")
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert dep["metadata"]["name"] == "runtime-u1001" and dep["spec"]["strategy"]["type"] == "Recreate"
    pod = dep["spec"]["template"]["spec"]
    assert pod["terminationGracePeriodSeconds"] == 60
    assert pod["securityContext"]["runAsUser"] == 1000 and pod["securityContext"]["fsGroup"] == 1000
    c = pod["containers"][0]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    env = {e["name"]: e for e in c["env"]}
    assert env["GSDB_USER_ID"]["value"] == "u1001"
    assert env["POD_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.name"
    assert env["GRMP_AUTH_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == "grmp-token-u1001"
    assert env["OPENCODE_SERVER_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == "pod-auth-u1001"
    mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
    assert mounts["/nas/me"]["subPath"] == "users/u1001"
    assert mounts["/nas/kb"]["readOnly"] is True and mounts["/nas/kb"]["subPath"] == "kb"
    # 基本认证开着时 /global/health 也要口令:探针必须在容器内带 $OPENCODE_SERVER_PASSWORD 访问,口令不能写进清单
    probe = " ".join(c["readinessProbe"]["exec"]["command"])
    assert "/global/health" in probe and "$OPENCODE_SERVER_PASSWORD" in probe and "httpGet" not in c["readinessProbe"]
    assert c["lifecycle"]["preStop"]["exec"]["command"] == ["sleep", "5"]
    svc = next(d for d in docs if d["kind"] == "Service")
    assert svc["metadata"]["name"] == "runtime-u1001" and svc["spec"]["ports"][0]["port"] == 4096


def test_kb_import_template_is_read_write_and_tokenless():
    docs = _docs("kb-import", USER_ID="u2001", IMAGE="gaussdb-agent-kb-import:dev", IMAGE_PULL_POLICY="IfNotPresent",
                 SUBPATH_ROOT="admins")
    dep = next(d for d in docs if d["kind"] == "Deployment")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    mounts = {m["mountPath"]: m for m in c["volumeMounts"]}
    assert mounts["/nas/me"]["subPath"] == "admins/u2001" and "readOnly" not in mounts["/nas/kb"]
    names = {e["name"] for e in c["env"]}
    assert "GRMP_AUTH_TOKEN" not in names and dep["metadata"]["labels"]["role"] == "kb-import"


def test_render_refuses_unknown_placeholder():
    with pytest.raises(KeyError):
        provision.render("name: ${USER_ID} ${NOPE}", USER_ID="x")


def test_read_token_accepts_export_prefix_and_quotes():
    text = "# 注释\nexport OTHER=1\nexport GRMP_AUTH_TOKEN=\"abc-123\"\nexport KIMI_API_KEY=zzz\n"
    assert provision.read_token(text) == "abc-123"
    assert provision.read_token("GRMP_AUTH_TOKEN='x'\n") == "x"
    assert provision.read_token("KIMI_API_KEY=zzz\n") is None


def test_runtime_template_injects_optional_sm2_private_key_and_kb_import_does_not():
    """2026-09-18 中间件加固:SM2 私钥整个应用一把,runtime 从共享 Secret grmp-sm2 取且 optional(没建时照常起、不签名);
    kb-import 不调中间件,不给它。"""
    rt = next(d for d in _docs("runtime", USER_ID="u1001", IMAGE="i", IMAGE_PULL_POLICY="IfNotPresent", SUBPATH_ROOT="users")
              if d["kind"] == "Deployment")
    env = {e["name"]: e for e in rt["spec"]["template"]["spec"]["containers"][0]["env"]}
    ref = env["GRMP_SM2_PRIVATE_KEY"]["valueFrom"]["secretKeyRef"]
    assert ref["name"] == "grmp-sm2" and ref["key"] == "GRMP_SM2_PRIVATE_KEY" and ref["optional"] is True
    kb = next(d for d in _docs("kb-import", USER_ID="u2001", IMAGE="i", IMAGE_PULL_POLICY="IfNotPresent", SUBPATH_ROOT="admins")
              if d["kind"] == "Deployment")
    assert "GRMP_SM2_PRIVATE_KEY" not in {e["name"] for e in kb["spec"]["template"]["spec"]["containers"][0]["env"]}
