#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

IMAGE="${IMAGE:-pingluzhang/cax}"
VERSION="${VERSION:-$(cat VERSION)}"
CACTUS_LEGACY_TARBALL="${CACTUS_LEGACY_TARBALL:-}"
RAMAX_REF="${RAMAX_REF:-master}"
BUILD_JOBS="${BUILD_JOBS:-$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)}"
TEMP_TARBALL=""

cleanup() {
  if [[ -n "${TEMP_TARBALL}" ]]; then
    rm -f "${TEMP_TARBALL}"
  fi
}
trap cleanup EXIT

build_args=(
  --platform linux/amd64
  --build-arg CACTUS_LEGACY=1
  --build-arg RAMAX_REF="${RAMAX_REF}"
  --build-arg BUILD_JOBS="${BUILD_JOBS}"
)

if [[ -z "${CACTUS_LEGACY_TARBALL}" && -f cactus-bin-legacy-v3.2.1.tar.gz ]]; then
  CACTUS_LEGACY_TARBALL="cactus-bin-legacy-v3.2.1.tar.gz"
fi

if [[ -n "${CACTUS_LEGACY_TARBALL}" ]]; then
  if [[ ! -f "${CACTUS_LEGACY_TARBALL}" ]]; then
    echo "ERROR: missing legacy Cactus tarball: ${CACTUS_LEGACY_TARBALL}" >&2
    echo "Unset CACTUS_LEGACY_TARBALL to let Docker download it, or point it at a readable tarball." >&2
    exit 1
  fi
  if [[ "${CACTUS_LEGACY_TARBALL}" == */* ]]; then
    TEMP_TARBALL="$(mktemp ".docker-cactus-legacy.XXXXXX.tar.gz")"
    cp "${CACTUS_LEGACY_TARBALL}" "${TEMP_TARBALL}"
    build_args+=(--build-arg CACTUS_TARBALL="${TEMP_TARBALL}")
  else
    build_args+=(--build-arg CACTUS_TARBALL="${CACTUS_LEGACY_TARBALL}")
  fi
else
  echo "No local legacy Cactus tarball found; Docker will download cactus-bin-legacy-v3.2.1.tar.gz."
fi

docker build "${build_args[@]}" \
  -t "${IMAGE}:${VERSION}-legacy" \
  -t "${IMAGE}:legacy" \
  .
