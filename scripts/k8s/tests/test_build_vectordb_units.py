"""第 5 个镜像(gaussdb-agent-vectordb)的构建:openGauss 官方二进制包 → 校验 → 打包成镜像。

行为在 Mac(arm64)上实际构建并起库验过:约 18 秒就绪,vector 类型在,<=> 可用,GS_DB 指定的库已建。
这里钉住三类容易被改坏的东西:上游文件原样不动、下载物必须校验、推送闸与 build-images.sh 一致。
"""
import hashlib
import os
import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DIR = _ROOT / "docker" / "vectordb"
_SCRIPT = _ROOT / "scripts" / "build-vectordb-image.sh"
# openGauss-server 仓库 v7.0.0-RC1 标签 docker/dockerfiles/7.0.0-rc1/entrypoint.sh,与官方镜像里的那份相同
_UPSTREAM_ENTRYPOINT_SHA256 = "18c1c236c49d82a0b9f2b30b83a4dcdb0a87138d4df1eb347300d337fae4093d"


def _sums():
    out = {}
    for line in (_DIR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split()
            out[name] = digest
    return out


def test_entrypoint_is_byte_identical_to_upstream():
    """清单里的 GS_PASSWORD / GS_PORT / GS_DB 靠的是官方 entrypoint 的行为。要改行为请改 Dockerfile,别改它。"""
    digest = hashlib.sha256((_DIR / "entrypoint.sh").read_bytes()).hexdigest()
    assert digest == _UPSTREAM_ENTRYPOINT_SHA256


def test_every_download_is_pinned_for_both_architectures():
    sums = _sums()
    for arch, og in (("arm64", "aarch64"), ("amd64", "x86_64")):
        assert "openGauss-Lite-7.0.0-RC1-openEuler22.03-%s.tar.gz" % og in sums
        assert "gosu-%s" % arch in sums
    assert all(re.fullmatch(r"[0-9a-f]{64}", d) for d in sums.values())


def test_script_verifies_before_using_a_download():
    """下载到 .part,校验通过才改名;不通过就删掉并失败 —— 不能留下一个半截或被替换过的包。"""
    body = _SCRIPT.read_text(encoding="utf-8")
    assert ".part" in body and 'mv "$PKG_DIR/$1.part" "$PKG_DIR/$1"' in body
    assert body.index("got=$(sha256_of") < body.index('mv "$PKG_DIR/$1.part"')


def test_packages_stay_out_of_image_layers_and_out_of_git():
    """二进制包经 bind mount 进构建:COPY 进来再删,包仍留在那一层里,两个架构白白多 60MB。"""
    df = (_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "--mount=type=bind,source=docker/vectordb/pkg" in df
    assert not re.search(r"^COPY .*pkg", df, re.MULTILINE)
    assert "docker/vectordb/pkg/" in (_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_missing_package_names_fail_the_build():
    """openEuler 的 dnf 会静默跳过不存在的包名(glibc-langpack-en 就是这样漏掉的,镜像起来才报 locale 无效)。"""
    df = (_DIR / "Dockerfile").read_text(encoding="utf-8")
    assert "strict=True" in df and "locale -a | grep -qx en_US.utf8" in df


@pytest.fixture
def run(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("docker", "curl"):
        fake = bin_ / tool
        fake.write_text('#!/bin/sh\necho "%s $@" >> "$CALLS"\nexit 0\n' % tool, encoding="utf-8")
        fake.chmod(0o755)
    calls = tmp_path / "calls.txt"

    def _run(*args):
        env = dict(os.environ, PATH="%s:%s" % (bin_, os.environ.get("PATH", "")), CALLS=str(calls))
        proc = subprocess.run(["bash", str(_SCRIPT), *args], env=env, capture_output=True, text=True)
        return proc, calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return _run


def test_push_without_registry_is_refused(run):
    proc, calls = run("t1", "--push")
    assert proc.returncode == 2 and "--registry" in proc.stderr and not calls


def test_single_arch_push_is_refused_unless_acknowledged(run):
    proc, calls = run("t1", "--push", "--registry", "reg.example/x")
    assert proc.returncode == 2 and "single-arch" in proc.stderr and not calls


def test_unknown_architecture_is_refused(run):
    proc, calls = run("t1", "--platform", "linux/s390x")
    assert proc.returncode == 2 and not calls
