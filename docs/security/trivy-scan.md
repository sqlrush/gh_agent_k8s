# 镜像漏洞扫描记录（trivy）

扫描方式（Mac，不装工具，用 trivy 自己的容器）：

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v $HOME/.cache/trivy:/root/.cache/ \
  aquasec/trivy:latest image --scanners vuln --format json --output /root/.cache/trivy.json gaussdb-agent-runtime:<TAG>
```

两个镜像共用 base 层，结果一致（差异只在 skills 目录，纯 Python 源码，无依赖差异）。

## agent-v0.4-oc1.18.27（修复前基线，2026-09-18）

| 严重度 | 条数 | 其中有修复版本 |
|---|---|---|
| CRITICAL | 14 | 0 |
| HIGH | 93 | 3 |
| MEDIUM | 175 | 8 |
| LOW | 148 | 1 |
| UNKNOWN | 4 | 2 |

有修复版本的 14 条全部来自三个包：`libpcre2-8-0`（6 条，`10.42-1 → 10.42-1+deb12u1`）、`liblzma5`（2 条，`5.4.1-1+deb12u2`）、`pip`（6 条，`25.0.1 → 26.x`）。

CRITICAL 14 条全部无修复版本：`perl` / `perl-base` / `perl-modules` / `libperl5.36` 三个 CVE ×4 包（Debian 状态 affected / fix_deferred）、`libsqlite3-0` CVE-2025-7458（affected）、`zlib1g` CVE-2023-45853（will_not_fix，minizip 部分，镜像不用）。perl 是 `git` 的依赖（`git init` 工作目录需要），sqlite 是 Python 标准库 `sqlite3` 模块的依赖（podctl 的 integrity_check / checkpoint 用）。

## 处理

- Dockerfile：`apt-get upgrade -y` 拿 deb12u 安全更新；装完依赖 `pip uninstall -y pip` 并删 `ensurepip/_bundled`（运行时不需要 pip，而 pip 条目与它 vendored 的包是全部 Python 包漏洞的来源）。→ 有修复版本的 14 条清零。
- 无修复版本的：随基础镜像刷新（`python:3.12-slim-bookworm` 的后续 digest）；客户扫描器若把这些判为阻断，依据是 Debian 官方状态（fix_deferred / will_not_fix），非镜像可控。
- 不做的：删 perl（git 硬依赖）；换 alpine（psycopg2-binary 的 musl 轮子与 opencode 的 glibc 构建都不合适）。

## 修复后

### agent-v0.4.1-oc1.18.27（2026-09-18，`apt-get upgrade` + 删除 pip / ensurepip 轮子之后）

| 严重度 | 条数 | 其中有修复版本 |
|---|---|---|
| CRITICAL | 14 | 0 |
| HIGH | 90 | 0 |
| MEDIUM | 167 | 0 |
| LOW | 147 | 0 |
| UNKNOWN | 2 | 0 |

**有修复版本的从 14 → 0，Python 包漏洞 0。** 中途发现只升级 pip 不够：trivy 报的 `msgpack 1.1.2` / `setuptools 70.3.0` 是 pip 自己 vendored 的副本（`pip/_vendor/msgpack`、`ensurepip/_bundled/pip-25.0.1.whl` 里的 `pkg_resources`），跟我们的三个依赖无关；镜像运行时不需要 pip，所以装完依赖直接 `pip uninstall -y pip` 并删 `ensurepip/_bundled`，Dockerfile 里随后 `python3 -c "import psycopg2, cryptography, yaml"` 钉住依赖仍可导入，docker 冒烟 14/14。

剩下的全部是 Debian 无修复版本的 OS 包（perl / libsqlite3 / zlib 与其它 bookworm 基础包），扫描原始 JSON 在 Mac `~/kf-verify/scan/`。客户侧复扫命令同文首。
