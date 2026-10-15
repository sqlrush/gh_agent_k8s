# syntax=docker/dockerfile:1.7
# 第 5 个镜像 gaussdb-agent-vectordb:知识库向量库 = openGauss 7.0.0-RC1 官方二进制包 + 官方 entrypoint。
#
# 与 Docker Hub 上的 opengauss/opengauss:7.0.0-RC1 是同一套装法(那个镜像里装的也是 Lite 二进制包,
# 已逐层核对过),区别只有两处:
#   · 基础系统:官方用 openEuler 20.03(已停止维护),这里用 openEuler 22.03 LTS SP4 与对应的 22.03 版二进制包
#   · 解包后删掉内层 .bin(约 40MB 的安装包本体,官方镜像把它留在了镜像里)
# 为什么是 openGauss 7 而不是 PG+pgvector:vector 类型是 7.0 内核自带的,见 k8s/base/vectordb.yaml。
#
# 构建入口是 scripts/build-vectordb-image.sh:它先把二进制包与 gosu 下载到 docker/vectordb/pkg/,
# 按 docker/vectordb/SHA256SUMS 逐个校验,再执行 docker buildx build。
# 二进制包经 bind mount 进构建,不进任何镜像层;两种架构的包都放在 pkg/ 里时,按 TARGETARCH 取对应的那个。
#
# entrypoint.sh 取自 openGauss-server 仓库 v7.0.0-RC1 标签的 docker/dockerfiles/7.0.0-rc1/entrypoint.sh
# (木兰宽松许可证 v2),与官方镜像里的那份逐字节相同,**不改它**:清单里的 GS_PASSWORD / GS_PORT / GS_DB
# 都是它认的变量。它用 gosu 从 root 切到 omm,所以 gosu 也要装。
FROM openeuler/openeuler:22.03-lts-sp4

ARG TARGETARCH
ARG OG_VERSION=7.0.0-RC1

# findutils:entrypoint 用 find 修正数据目录属主(基础镜像不带,缺了容器一启动就退出)
# en_US.utf8:entrypoint 与 gs_initdb 用这个 locale,基础镜像只有 C.utf8,缺了 gs_initdb 报 invalid locale settings。
#   openEuler 22.03 没有 glibc-langpack-en 这个包(dnf 默认还会静默跳过不存在的包名,所以加了 strict=True);
#   整包 glibc-all-langpacks 几百 MB,这里装源码只生成 en_US 一个,同一层里再把源码卸掉。
#   --no-archive:基础镜像的 locale-archive 是空文件,往里追加会报 cannot read archive header
RUN dnf install -y --setopt=install_weak_deps=False --setopt=strict=True \
        shadow tar gzip findutils libaio util-linux procps-ng glibc-locale-source \
    && localedef --no-archive -i en_US -f UTF-8 en_US.UTF-8 \
    && dnf remove -y glibc-locale-source \
    && dnf clean all && rm -rf /var/cache/dnf \
    && locale -a | grep -qx en_US.utf8

RUN --mount=type=bind,source=docker/vectordb/pkg,target=/tmp/pkg \
    set -eux; \
    case "$TARGETARCH" in arm64) A=aarch64 ;; amd64) A=x86_64 ;; *) echo "不支持的架构 $TARGETARCH" >&2; exit 1 ;; esac; \
    PKG="openGauss-Lite-${OG_VERSION}-openEuler22.03-${A}"; \
    groupadd -g 70 omm; \
    useradd -u 70 -g omm -m -d /home/omm omm; \
    mkdir -p /usr/local/opengauss /var/lib/opengauss /var/run/opengauss /docker-entrypoint-initdb.d; \
    tar -xzf "/tmp/pkg/${PKG}.tar.gz" -C /usr/local/opengauss; \
    tar -xzf "/usr/local/opengauss/${PKG}.bin" -C /usr/local/opengauss; \
    rm -f "/usr/local/opengauss/${PKG}.bin"; \
    install -m 0755 "/tmp/pkg/gosu-${TARGETARCH}" /usr/local/bin/gosu; \
    gosu nobody true; \
    chown -R omm:omm /usr/local/opengauss /var/lib/opengauss /var/run/opengauss /docker-entrypoint-initdb.d; \
    chmod 2777 /var/run/opengauss; \
    chmod 1777 /tmp

# omm 的登录环境:k8s 探针与 scripts/k8s/init-vectordb.sh 都用 `su - omm -c gsql ...`,靠这几行找到 gsql
RUN { echo 'export GAUSSHOME=/usr/local/opengauss'; \
      echo 'export PATH=$GAUSSHOME/bin:$PATH'; \
      echo 'export LD_LIBRARY_PATH=$GAUSSHOME/lib:$LD_LIBRARY_PATH'; } >> /home/omm/.bashrc \
    && sed -i 's/^TMOUT=.*/TMOUT=0/' /etc/bashrc

COPY docker/vectordb/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh && ln -s /usr/local/bin/entrypoint.sh /entrypoint.sh \
    && su - omm -c 'gaussdb -V'

ENV LANG=en_US.utf8 PGDATA=/var/lib/opengauss/data
LABEL org.opencontainers.image.title="gaussdb-agent-vectordb" \
      org.opencontainers.image.source="https://github.com/sqlrush/gh_agent_k8s" \
      org.opencontainers.image.description="openGauss ${OG_VERSION} (Lite, official binary package) for the knowledge-base vector store"

EXPOSE 5432
ENTRYPOINT ["entrypoint.sh"]
CMD ["gaussdb"]
