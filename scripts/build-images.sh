#!/usr/bin/env bash
# 构建两个后端镜像。默认本机架构;--platform linux/amd64,linux/arm64 交叉构建(需要 buildx)。
# 用法: scripts/build-images.sh [TAG] [--platform P] [--push]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TAG=""
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then TAG=$1; shift; fi
[ -n "$TAG" ] || TAG="$(git -C "$HERE" describe --tags --always)-oc1.18.27"
PLATFORM=""; PUSH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) PLATFORM="--platform $2"; shift 2 ;;
    --push) PUSH="--push"; shift ;;
    *) echo "unknown $1" >&2; exit 2 ;;
  esac
done
LOAD=""; [ -z "$PLATFORM" ] && LOAD="--load"
for T in runtime kb-import; do
  # shellcheck disable=SC2086
  docker buildx build $PLATFORM $LOAD $PUSH -f "$HERE/docker/Dockerfile" --target "$T" -t "gaussdb-agent-$T:$TAG" "$HERE"
done
# 网关是独立 Dockerfile:它不带 skill、不装 psycopg2、也不要 opencode 二进制,
# 跟两个用户镜像没有可复用的层,放一起只会让用户镜像多背 100 MB。
# shellcheck disable=SC2086
docker buildx build $PLATFORM $LOAD $PUSH -f "$HERE/docker/Dockerfile.gateway" -t "gaussdb-agent-gateway:$TAG" "$HERE"
echo "built: gaussdb-agent-runtime:$TAG gaussdb-agent-kb-import:$TAG gaussdb-agent-gateway:$TAG"
