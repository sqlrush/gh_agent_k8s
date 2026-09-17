#!/usr/bin/env bash
# 离线交付包:把两个后端镜像(两种架构)导出成 tar.gz + sha256,连同 k8s 清单与文档打成一个 dist/ 目录。
# 用法: scripts/package-images.sh <TAG> [--arch amd64,arm64]
#   前提: 镜像已构建——本机架构用 <TAG>,另一架构用 <TAG>-<arch>(scripts/build-images.sh <TAG>-<arch> --platform linux/<arch>)。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
TAG=${1:?tag}; shift || true
ARCHS="amd64,arm64"
while [ $# -gt 0 ]; do case "$1" in --arch) ARCHS=$2; shift 2 ;; *) echo "unknown $1" >&2; exit 2 ;; esac; done
HOST_ARCH=$(docker info --format '{{.Architecture}}' 2>/dev/null | sed 's/aarch64/arm64/; s/x86_64/amd64/')
DIST="$HERE/dist/gaussdb-agent-$TAG"
rm -rf "$DIST"; mkdir -p "$DIST"
COMMIT=$(git -C "$HERE" rev-parse --short HEAD)
{
  echo "gaussdb-agent 离线交付包  标签 $TAG  仓库提交 $COMMIT  打包 $(date +%F)"
  echo
} > "$DIST/MANIFEST.txt"

for ARCH in ${ARCHS//,/ }; do
  SUFFIX=""; [ "$ARCH" != "$HOST_ARCH" ] && SUFFIX="-$ARCH"
  IMAGES=""
  for T in runtime kb-import; do
    IMG="gaussdb-agent-$T:$TAG$SUFFIX"
    docker image inspect "$IMG" >/dev/null 2>&1 || { echo "缺镜像 $IMG(先 build-images.sh)" >&2; exit 1; }
    IMAGES="$IMAGES $IMG"
    printf '%-10s %-40s id=%s size=%sMB\n' "$ARCH" "$IMG" "$(docker image inspect "$IMG" --format '{{.Id}}' | cut -c8-19)" \
      "$(docker image inspect "$IMG" --format '{{.Size}}' | awk '{printf "%d", $1/1048576}')" >> "$DIST/MANIFEST.txt"
  done
  OUT="$DIST/gaussdb-agent-$TAG-$ARCH.tar.gz"
  # shellcheck disable=SC2086
  docker save $IMAGES | gzip -6 > "$OUT"
  (cd "$DIST" && shasum -a 256 "$(basename "$OUT")" > "$(basename "$OUT").sha256")
  echo "  → $(basename "$OUT") $(du -h "$OUT" | cut -f1)" >> "$DIST/MANIFEST.txt"
done

# 清单与文档
tar czf "$DIST/gaussdb-agent-$TAG-k8s.tar.gz" -C "$HERE" k8s docs/env-contract.md docs/k8s-deploy.md docs/delivery-容器化交付手册.md docs/specs
(cd "$DIST" && shasum -a 256 "gaussdb-agent-$TAG-k8s.tar.gz" > "gaussdb-agent-$TAG-k8s.tar.gz.sha256")
cat >> "$DIST/MANIFEST.txt" <<EOF
  → gaussdb-agent-$TAG-k8s.tar.gz  k8s/ 清单模板 + docs/(契约、部署手册、交付手册、设计)

导入镜像:
  docker:      gunzip -c gaussdb-agent-$TAG-<arch>.tar.gz | docker load
  containerd:  gunzip -c gaussdb-agent-$TAG-<arch>.tar.gz | ctr -n k8s.io images import -
  然后按内网仓库地址重打标签并 push;k8s/templates 里的镜像名由 provision 的 --image-tag 决定。
校验: shasum -a 256 -c *.sha256
EOF
echo "== $DIST"; cat "$DIST/MANIFEST.txt"
