#!/usr/bin/env bash
# 离线交付包:把**四个**镜像(两种架构)导出成 tar.gz + sha256,连同 k8s 清单与文档打成一个 dist/ 目录。
#   gaussdb-agent-runtime / -kb-import   每用户一个的业务 Pod
#   gaussdb-agent-gateway                接入网关(全体共用)
#   opengauss:7.0.0-RC1                  知识库向量存储的基础镜像
# 后两个漏了的话,客户拿到包也部署不起来 —— 网关起不来、向量库拉不到镜像,
# 而现场往往没有外网可以补拉。
# 用法: scripts/package-images.sh <TAG> [--arch amd64,arm64]
#   前提: 镜像已构建——本机架构用 <TAG>,另一架构用 <TAG>-<arch>(scripts/build-images.sh <TAG>-<arch> --platform linux/<arch>)。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TAG=${1:?tag}; shift || true
ARCHS="amd64,arm64"
while [ $# -gt 0 ]; do case "$1" in --arch) ARCHS=$2; shift 2 ;; *) echo "unknown $1" >&2; exit 2 ;; esac; done
HOST_ARCH=$(docker info --format '{{.Architecture}}' 2>/dev/null | sed 's/aarch64/arm64/; s/x86_64/amd64/')
# 向量库的基础镜像。与 k8s/base/vectordb.yaml 里的版本必须一致 —— 不一致的话
# 客户载入的是一个版本、清单要拉的是另一个版本,而现场没有外网可以补。
VECTORDB_IMAGE=${VECTORDB_IMAGE:-opengauss/opengauss:7.0.0-RC1}
# 包名:标签本身以 agent- 开头时不再重复(gaussdb-agent-v0.4-…),否则 gaussdb-agent-<TAG>
case "$TAG" in agent-*) PKG="gaussdb-$TAG" ;; *) PKG="gaussdb-agent-$TAG" ;; esac
DIST="$HERE/dist/$PKG"
rm -rf "$DIST"; mkdir -p "$DIST"
COMMIT=$(git -C "$HERE" rev-parse --short HEAD)
{
  echo "gaussdb-agent 离线交付包  标签 $TAG  仓库提交 $COMMIT  打包 $(date +%F)"
  echo
} > "$DIST/MANIFEST.txt"

for ARCH in ${ARCHS//,/ }; do
  SUFFIX=""; [ "$ARCH" != "$HOST_ARCH" ] && SUFFIX="-$ARCH"
  IMAGES=""
  for T in runtime kb-import gateway frontend; do
    IMG="gaussdb-agent-$T:$TAG$SUFFIX"
    docker image inspect "$IMG" >/dev/null 2>&1 || { echo "缺镜像 $IMG(先 build-images.sh)" >&2; exit 1; }
    IMAGES="$IMAGES $IMG"
    printf '%-10s %-40s id=%s size=%sMB\n' "$ARCH" "$IMG" "$(docker image inspect "$IMG" --format '{{.Id}}' | cut -c8-19)" \
      "$(docker image inspect "$IMG" --format '{{.Size}}' | awk '{printf "%d", $1/1048576}')" >> "$DIST/MANIFEST.txt"
  done
  # 向量库用的是上游 openGauss 镜像,不是我们构建的 —— 现场没外网时必须随包带走。
  # 本机没有就先 docker pull(交叉架构那一份用 --platform 拉)。
  VECDB="$VECTORDB_IMAGE"
  if ! docker image inspect "$VECDB" >/dev/null 2>&1; then
    echo "  本机没有 $VECDB,尝试拉取(需要一次外网)" >&2
    docker pull --platform "linux/$ARCH" "$VECDB" >/dev/null 2>&1 \
      || { echo "拉不到 $VECDB —— 请先在有外网的机器上 docker pull 再导入" >&2; exit 1; }
  fi
  IMAGES="$IMAGES $VECDB"
  printf '%-10s %-40s id=%s size=%sMB\n' "$ARCH" "$VECDB" "$(docker image inspect "$VECDB" --format '{{.Id}}' | cut -c8-19)" \
    "$(docker image inspect "$VECDB" --format '{{.Size}}' | awk '{printf "%d", $1/1048576}')" >> "$DIST/MANIFEST.txt"
  OUT="$DIST/$PKG-$ARCH.tar.gz"
  # shellcheck disable=SC2086
  docker save $IMAGES | gzip -6 > "$OUT"
  (cd "$DIST" && shasum -a 256 "$(basename "$OUT")" > "$(basename "$OUT").sha256")
  echo "  → $(basename "$OUT") $(du -h "$OUT" | cut -f1)" >> "$DIST/MANIFEST.txt"
done

# 清单与文档
# **文档清单不要再写死。** 写死的后果是:加了新文档却忘了加进这一行,客户拿到的包里
# 没有最重要的那两份,而打包脚本一声不响地成功了。改成整个 docs/ 目录打进去,
# 只排除内部用的:
#   plans/     我们的实施计划
#   security/  扫描报告
#   specs/     设计 spec —— 里面有备选方案、退路、残余风险、未定项与决策过程。
#              交付文档不写我们的讨论过程,这一份从头到尾都是讨论过程。
# COPYFILE_DISABLE=1:在 macOS 上打包时,bsdtar 会为每个带扩展属性的文件额外塞一个
# `._<原名>` 的资源叉文件(客户解开后 docs/ 里会多出一堆 ._参数手册.md)。
# 那些文件还带着本机的 com.apple.provenance 属性。交付给客户的包里不该有。
# 这个变量在 Linux 的 GNU tar 上是无害的空操作。
COPYFILE_DISABLE=1 tar czf "$DIST/$PKG-k8s.tar.gz" -C "$HERE" \
    --exclude='docs/plans' --exclude='docs/security' --exclude='docs/specs' --exclude='docs/prototypes' \
    --exclude='._*' --exclude='.DS_Store' \
    k8s docs
(cd "$DIST" && shasum -a 256 "$PKG-k8s.tar.gz" > "$PKG-k8s.tar.gz.sha256")
cat >> "$DIST/MANIFEST.txt" <<EOF
  → $PKG-k8s.tar.gz  k8s/ 清单模板 + docs/(快速搭建、命令卡、部署手册、接入手册、对接清单、参数手册、功能清单)

导入镜像:
  docker:      gunzip -c $PKG-<arch>.tar.gz | docker load
  containerd:  gunzip -c $PKG-<arch>.tar.gz | ctr -n k8s.io images import -
  然后按内网仓库地址重打标签并 push;k8s/templates 里的镜像名由 provision 的 --image-tag 决定。
校验: shasum -a 256 -c *.sha256
EOF
echo "== $DIST"; cat "$DIST/MANIFEST.txt"
