#!/usr/bin/env bash
# 构建第 5 个镜像 gaussdb-agent-vectordb(知识库向量库):下载 openGauss 官方二进制包 → 校验 → 打包成镜像。
# Dockerfile 与说明见 docker/vectordb/Dockerfile。
#
# 用法: scripts/build-vectordb-image.sh [TAG] [--platform P] [--registry R --push [--single-arch]]
#   本地构建(本机架构):  scripts/build-vectordb-image.sh
#   推仓库(两种架构):    scripts/build-vectordb-image.sh 7.0.0-RC1 \
#                            --registry <镜像仓库前缀> --platform linux/amd64,linux/arm64 --push
#
# 构建机不能上外网时:在能上网的机器上把 docker/vectordb/SHA256SUMS 里列的文件下载好,
# 拷到构建机的 docker/vectordb/pkg/ 下再执行本脚本 —— 文件已在且校验通过就不再下载。
# 下载地址可用环境变量换成内网镜像站:OG_BASE_URL(openGauss 包)、GOSU_BASE_URL(gosu)。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OG_VERSION=7.0.0-RC1
OG_BASE_URL=${OG_BASE_URL:-https://opengauss.obs.cn-south-1.myhuaweicloud.com/$OG_VERSION/openEuler22.03}
GOSU_BASE_URL=${GOSU_BASE_URL:-https://github.com/tianon/gosu/releases/download/1.17}
PKG_DIR="$HERE/docker/vectordb/pkg"
SUMS="$HERE/docker/vectordb/SHA256SUMS"

TAG=""
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then TAG=$1; shift; fi
TAG=${TAG:-$OG_VERSION}
PLATFORM=""; PUSH=""; REGISTRY=""; SINGLE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) PLATFORM=${2:?}; shift 2 ;;
    --registry) REGISTRY="${2%/}"; shift 2 ;;
    --push) PUSH="--push"; shift ;;
    --single-arch) SINGLE="yes"; shift ;;
    *) echo "不认识的参数 $1" >&2; exit 2 ;;
  esac
done
# 推送的两道闸与 build-images.sh 相同:必须带仓库前缀;必须是两种架构,除非显式 --single-arch
if [ -n "$PUSH" ]; then
  [ -n "$REGISTRY" ] || { echo "--push 必须同时给 --registry <镜像仓库前缀>" >&2; exit 2; }
  case "$PLATFORM" in
    *amd64*arm64*|*arm64*amd64*) ;;
    *) [ -n "$SINGLE" ] || { echo "--push 只给了 ${PLATFORM:-本机架构}:另一种架构的节点会拉不到。加 --platform linux/amd64,linux/arm64,或显式 --single-arch" >&2; exit 2; } ;;
  esac
fi

# 要构建哪些架构
if [ -n "$PLATFORM" ]; then
  ARCHS=$(printf '%s' "$PLATFORM" | tr ',' '\n' | sed 's#^linux/##')
else
  case "$(uname -m)" in x86_64|amd64) ARCHS=amd64 ;; aarch64|arm64) ARCHS=arm64 ;; *) echo "不支持的本机架构 $(uname -m)" >&2; exit 1 ;; esac
fi

sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
# 取一个文件:已在且校验通过就跳过;否则下载后校验,不对就删掉并报错
fetch() {  # $1 文件名 $2 下载地址
  local want got
  want=$(awk -v f="$1" '$2 == f { print $1 }' "$SUMS")
  [ -n "$want" ] || { echo "SHA256SUMS 里没有 $1" >&2; exit 1; }
  if [ -f "$PKG_DIR/$1" ] && [ "$(sha256_of "$PKG_DIR/$1")" = "$want" ]; then
    echo "  已有 $1(校验通过)"; return 0
  fi
  echo "  下载 $1"
  curl -fL --retry 3 -o "$PKG_DIR/$1.part" "$2" || { rm -f "$PKG_DIR/$1.part"; echo "下载失败:$2" >&2; exit 1; }
  got=$(sha256_of "$PKG_DIR/$1.part")
  if [ "$got" != "$want" ]; then
    rm -f "$PKG_DIR/$1.part"
    echo "校验失败:$1 的 sha256 是 $got,应为 $want" >&2; exit 1
  fi
  mv "$PKG_DIR/$1.part" "$PKG_DIR/$1"
  echo "  校验通过 $1"
}

mkdir -p "$PKG_DIR"
echo "== 1 准备二进制包(架构:$(echo $ARCHS | tr '\n' ' '))"
for a in $ARCHS; do
  case "$a" in
    arm64) fetch "openGauss-Lite-$OG_VERSION-openEuler22.03-aarch64.tar.gz" "$OG_BASE_URL/arm/openGauss-Lite-$OG_VERSION-openEuler22.03-aarch64.tar.gz" ;;
    amd64) fetch "openGauss-Lite-$OG_VERSION-openEuler22.03-x86_64.tar.gz" "$OG_BASE_URL/x86/openGauss-Lite-$OG_VERSION-openEuler22.03-x86_64.tar.gz" ;;
    *) echo "不支持的架构 $a(只支持 amd64 / arm64)" >&2; exit 2 ;;
  esac
  fetch "gosu-$a" "$GOSU_BASE_URL/gosu-$a"
done

echo "== 2 构建镜像"
PREFIX=""; [ -n "$REGISTRY" ] && PREFIX="$REGISTRY/"
LOAD=""; [ -z "$PLATFORM" ] && [ -z "$PUSH" ] && LOAD="--load"
IMG="${PREFIX}gaussdb-agent-vectordb:$TAG"
# shellcheck disable=SC2086
docker buildx build ${PLATFORM:+--platform $PLATFORM} $LOAD $PUSH --build-arg OG_VERSION="$OG_VERSION" \
  -f "$HERE/docker/vectordb/Dockerfile" -t "$IMG" "$HERE"
echo "built: $IMG"
