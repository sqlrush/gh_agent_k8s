"""Fuyao 运行模式的集群清单(k8s/fuyao、k8s/overlays/fuyao)守卫(2026-11-17)。

与 base 同一套规矩:不注入 Service 发现变量、只读根文件系统、emptyDir 有上限;凭据代理对 Secret 只能按名取、不能列举;
集群服务与 base 同一个镜像版本(发版时一起改);沙箱组建在平台命名空间、不进 overlay。
"""
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FY = _ROOT / "k8s" / "fuyao"


def _docs(path):
    return [d for d in path.read_text(encoding="utf-8").split("\n---\n") if d.strip()]


def test_cluster_services_follow_the_base_hardening_rules():
    for name in ("statesvc.yaml", "credproxy.yaml"):
        text = (_FY / name).read_text(encoding="utf-8")
        assert "enableServiceLinks: false" in text, name
        assert "readOnlyRootFilesystem: true" in text and 'drop: ["ALL"]' in text, name
        assert "runAsNonRoot: true" in text, name
        for body in re.findall(r"emptyDir: \{([^}]*)\}", text):
            assert "sizeLimit" in body, name


def test_credproxy_can_get_but_never_list_secrets():
    role = [d for d in _docs(_FY / "credproxy.yaml") if "kind: Role\n" in d][0]
    assert re.search(r'resources: \["secrets"\]\s*\n\s*verbs: \["get"\]', role), "只能按名取 grmp-token-<工号>"
    assert '"list"' not in role and '"watch"' not in role


def test_cluster_services_use_the_same_image_version_as_the_base():
    base = (_ROOT / "k8s" / "base" / "gateway.yaml").read_text(encoding="utf-8")
    tag = re.search(r"image: gaussdb-agent-gateway:(\S+)", base).group(1)
    for name in ("statesvc.yaml", "credproxy.yaml"):
        assert "gaussdb-agent-runtime:%s" % tag in (_FY / name).read_text(encoding="utf-8"), \
            "%s 的镜像版本要与 base 一致(发版脚本一起改)" % name


def test_sandbox_groups_live_in_the_platform_namespace_and_stay_out_of_the_overlay():
    groups = (_FY / "sandboxgroups.yaml").read_text(encoding="utf-8")
    assert groups.count("kind: SandboxGroup") == 2 and groups.count("namespace: flux-system") == 2
    kz = (_FY / "kustomization.yaml").read_text(encoding="utf-8")
    assert "sandboxgroups" not in kz.split("resources:")[1], "沙箱组由平台管理员单独 apply"
    ov = (_ROOT / "k8s" / "overlays" / "fuyao" / "configmap-gateway-fuyao.yaml").read_text(encoding="utf-8")
    assert 'RUNTIME_MODE: "fuyao"' in ov
    for g in re.findall(r"name: (sg-gsdb-\w+)", groups):
        assert g in ov, "overlay 里的沙箱组名要与 sandboxgroups.yaml 一致:%s" % g
