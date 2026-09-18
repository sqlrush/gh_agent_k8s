# syntax=docker/dockerfile:1.7
# 两个后端镜像出自同一文件:--target runtime / --target kb-import。
# 内网不联外:opencode、ripgrep、models.json 都在构建期取好。
ARG OPENCODE_VERSION=1.18.27

# ---------------------------------------------------------------- base
FROM python:3.12-slim-bookworm AS base
ARG OPENCODE_VERSION
ARG TARGETARCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai LANG=C.UTF-8
# upgrade:基础镜像里 libpcre2 / liblzma 等有 deb12u 安全更新,构建时一并拿到(trivy 记录见 docs/security/)
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends ca-certificates curl ripgrep git tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --user-group --create-home --shell /bin/bash agent

# opencode 官方 release 包(glibc 版);包内是单个可执行文件
RUN set -eux; case "$TARGETARCH" in amd64) A=x64 ;; arm64) A=arm64 ;; *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; esac; \
    curl -fsSL -o /tmp/oc.tgz "https://github.com/sst/opencode/releases/download/v${OPENCODE_VERSION}/opencode-linux-${A}.tar.gz"; \
    mkdir -p /tmp/oc && tar xzf /tmp/oc.tgz -C /tmp/oc && install -m 0755 "$(find /tmp/oc -type f -name opencode | head -1)" /usr/local/bin/opencode; \
    rm -rf /tmp/oc /tmp/oc.tgz; opencode --version

# 缓存目录预置:模型清单与 ripgrep,运行时不再下载
RUN mkdir -p /opt/agent/cache/opencode/bin \
    && curl -fsSL -o /opt/agent/cache/opencode/models.json https://models.dev/api.json \
    && cp /usr/bin/rg /opt/agent/cache/opencode/bin/rg

# Python 依赖(白名单三项;psycopg2-binary 与 psycopg2 同模块名)
# 装完依赖就把 pip 连同 ensurepip 自带的 pip 轮子删掉:运行时用不到,而 trivy 报的 python 包漏洞全部来自
# pip 自带的 vendored msgpack / pkg_resources(setuptools),不是我们的依赖
RUN pip install --no-cache-dir "psycopg2-binary==2.9.10" "cryptography>=41" "PyYAML>=6" \
    && pip uninstall -y pip && rm -rf /usr/local/lib/python3.12/ensurepip/_bundled \
    && python3 -c "import psycopg2, cryptography, yaml"

# 技能代码:用仓库自己的安装脚本装进 opencode 的技能目录(common/、scripts/registry/ 一起)
COPY agent/ /opt/agent/src/
RUN cd /opt/agent/src && bash install-opencode.sh --dest /opt/agent/config/opencode/skills --no-backup \
        --agents-md /opt/agent/config/opencode/AGENTS.md >/tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; } \
    && grep -E "installed" /tmp/install.log | tail -1 && rm -rf /opt/agent/src /tmp/install.log
COPY docker/podctl.py docker/entrypoint.sh /opt/agent/
RUN chmod 0755 /opt/agent/entrypoint.sh && chown -R agent:agent /opt/agent && mkdir -p /data /nas && chown agent:agent /data

USER agent
WORKDIR /data
EXPOSE 4096
# 设了 OPENCODE_SERVER_PASSWORD 时 /global/health 也要基本认证,探针带上口令(k8s 模板里的 exec 探针同样写法)
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS ${OPENCODE_SERVER_PASSWORD:+-u "opencode:$OPENCODE_SERVER_PASSWORD"} http://127.0.0.1:4096/global/health || exit 1
ENTRYPOINT ["/opt/agent/entrypoint.sh"]

# ---------------------------------------------------------------- runtime:诊断 skill + 知识库查询
FROM base AS runtime
USER root
RUN rm -rf /opt/agent/config/opencode/skills/gaussdb-kb-import \
    && test "$(ls -d /opt/agent/config/opencode/skills/gaussdb-* | wc -l)" = "17"
USER agent
LABEL org.opencontainers.image.title="gaussdb-agent-runtime" \
      org.opencontainers.image.description="opencode + 16 diagnostic skills + gaussdb-kb (query); kb/ mounted read-only"

# ---------------------------------------------------------------- kb-import:知识库查询 + 导入
FROM base AS kb-import
USER root
RUN cd /opt/agent/config/opencode/skills && for d in gaussdb-*; do case "$d" in gaussdb-kb|gaussdb-kb-import) ;; *) rm -rf "$d" ;; esac; done \
    && test "$(ls -d gaussdb-* | wc -l)" = "2"
USER agent
ENV GSDB_KB_INBOX=/nas/kb/inbox/uploads
LABEL org.opencontainers.image.title="gaussdb-agent-kb-import" \
      org.opencontainers.image.description="opencode + gaussdb-kb + gaussdb-kb-import; kb/ mounted read-write; no GRMP token"
