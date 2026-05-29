#!/usr/bin/env bash
set -Eeuo pipefail

REPO="ComparativeGenomicsToolkit/cactus"
GITHUB_HOST="https://github.com"
SCRIPT_NAME="$(basename "$0")"

# Default: keep downloads and installation under the current directory.
DOWNLOAD_DIR="${CACTUS_DOWNLOAD_DIR:-$PWD}"
INSTALL_ROOT="${CACTUS_INSTALL_ROOT:-$PWD}"

# Optional:
#   CACTUS_VERSION=v3.2.1    Pin a specific version.
#   CACTUS_LEGACY=1          Force the legacy build.
#   CACTUS_LEGACY=0          Force the regular build.
#   CACTUS_LEGACY=auto       Default: use legacy automatically when AVX2 is unavailable.
VERSION_OVERRIDE="${CACTUS_VERSION:-}"
LEGACY_MODE="${CACTUS_LEGACY:-auto}"

usage() {
  cat <<EOF
Usage:
  ${SCRIPT_NAME} [options]

Install the official Cactus binary release into the currently active Conda
environment. By default, this script detects the latest GitHub release,
downloads or reuses the matching tarball, installs the Cactus Python package
and Toil dependencies, writes Conda activate/deactivate hooks, and verifies the
installation with cactus -h.

Before running:
  conda activate cax
  ./${SCRIPT_NAME}

Options:
  -h, --help
      Show this help message and exit.

Environment variables:
  CACTUS_VERSION=v3.2.1
      Pin a specific version. Both v3.2.1 and 3.2.1 are accepted. If unset,
      the latest release is used.

  CACTUS_LEGACY=auto|1|0
      Select the legacy binary package. Default is auto: on x86_64, use the
      legacy package automatically when AVX2 is unavailable. Set to 1 to force
      legacy, or 0 to force the regular package.

  CACTUS_DOWNLOAD_DIR=/path/to/downloads
      Directory for tarball downloads/cache. Default is the current directory.

  CACTUS_INSTALL_ROOT=/path/to/install-root
      Directory where Cactus is extracted. Default is the current directory.

Examples:
  ./${SCRIPT_NAME}
  CACTUS_VERSION=v3.2.1 ./${SCRIPT_NAME}
  CACTUS_LEGACY=1 CACTUS_INSTALL_ROOT="\$HOME/opt" ./${SCRIPT_NAME}
EOF
}

log() {
  printf '%s\n' "$*"
}

step() {
  printf '\n==> %s\n' "$*"
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$#" -gt 0 ]]; then
  usage >&2
  die "Unknown argument(s): $*"
fi

step "Checking runtime environment"
[[ "$(uname -s)" == "Linux" ]] || die "This script only supports Linux / WSL Linux."
[[ -n "${CONDA_PREFIX:-}" ]] || die "Please activate a Conda environment first, for example: conda activate cax"

log "Conda env: ${CONDA_PREFIX}"
need_cmd curl
need_cmd tar
need_cmd grep
need_cmd sed
need_cmd python
log "Required commands found: curl, tar, grep, sed, python"

python - <<'PY' || die "The active Conda environment must use Python >= 3.10"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
log "Python version check passed: $(python --version)"

python -m pip --version >/dev/null 2>&1 || die "pip is missing in the active Conda environment. Run: conda install pip"
log "pip check passed: $(python -m pip --version)"

get_latest_tag() {
  local final_url tag

  final_url="$(
    curl -fsSLI \
      -A "cactus-conda-installer" \
      -o /dev/null \
      -w '%{url_effective}' \
      "${GITHUB_HOST}/${REPO}/releases/latest"
  )"

  tag="${final_url##*/}"

  [[ "$tag" =~ ^v[0-9]+(\.[0-9]+)* ]] || die "Could not parse latest release tag: $tag"
  printf '%s\n' "$tag"
}

if [[ -n "$VERSION_OVERRIDE" ]]; then
  step "Using user-specified Cactus version"
  if [[ "$VERSION_OVERRIDE" == v* ]]; then
    TAG="$VERSION_OVERRIDE"
  else
    TAG="v${VERSION_OVERRIDE}"
  fi
else
  step "Querying the latest Cactus release from GitHub"
  TAG="$(get_latest_tag)"
fi
log "Version to install: ${TAG}"

step "Selecting binary package type"
case "$LEGACY_MODE" in
  auto)
    USE_LEGACY=0
    if [[ "$(uname -m)" == "x86_64" ]] && ! grep -qw avx2 /proc/cpuinfo 2>/dev/null; then
      USE_LEGACY=1
    fi
    ;;
  1|true|TRUE|yes|YES|y|Y)
    USE_LEGACY=1
    ;;
  0|false|FALSE|no|NO|n|N)
    USE_LEGACY=0
    ;;
  *)
    die "CACTUS_LEGACY must be auto, 1, or 0"
    ;;
esac

if [[ "$USE_LEGACY" -eq 1 ]]; then
  TARBALL="cactus-bin-legacy-${TAG}.tar.gz"
else
  TARBALL="cactus-bin-${TAG}.tar.gz"
fi
if [[ "$USE_LEGACY" -eq 1 ]]; then
  log "Selected legacy package: ${TARBALL}"
else
  log "Selected regular package: ${TARBALL}"
fi

URL="${GITHUB_HOST}/${REPO}/releases/download/${TAG}/${TARBALL}"
TARBALL_PATH="${DOWNLOAD_DIR}/${TARBALL}"

step "Installation settings"
log "Conda env     : ${CONDA_PREFIX}"
log "Cactus tag    : ${TAG}"
log "Tarball       : ${TARBALL}"
log "Download path : ${TARBALL_PATH}"
log "Install root  : ${INSTALL_ROOT}"
log "Download URL  : ${URL}"

mkdir -p "$DOWNLOAD_DIR" "$INSTALL_ROOT"

download_tarball() {
  step "Downloading or reusing the Cactus tarball"
  log "curl resume and retry support is enabled."

  if [[ -s "$TARBALL_PATH" ]]; then
    if tar -tzf "$TARBALL_PATH" >/dev/null 2>&1; then
      log "Existing complete tarball found; skipping download: ${TARBALL_PATH}"
      return 0
    else
      log "Incomplete tarball detected; attempting resume: ${TARBALL_PATH}"
    fi
  fi

  if ! curl \
    -fL \
    -A "cactus-conda-installer" \
    --retry 10 \
    --retry-delay 5 \
    --retry-all-errors \
    --connect-timeout 30 \
    --speed-time 60 \
    --speed-limit 1024 \
    -C - \
    "$URL" \
      -o "$TARBALL_PATH"; then

    log "Resume failed; removing the partial file and downloading from scratch..."
    rm -f "$TARBALL_PATH"

    curl \
      -fL \
      -A "cactus-conda-installer" \
      --retry 10 \
      --retry-delay 5 \
      --retry-all-errors \
      --connect-timeout 30 \
      --speed-time 60 \
      --speed-limit 1024 \
      "$URL" \
      -o "$TARBALL_PATH"
  fi

  tar -tzf "$TARBALL_PATH" >/dev/null 2>&1 || die "Downloaded tarball is still invalid: ${TARBALL_PATH}"
  log "tarball integrity check passed."
}

download_tarball

step "Detecting tarball top-level directory"
TOPDIR="$(
  python - "$TARBALL_PATH" <<'PY'
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:gz") as tf:
    for member in tf:
        top = member.name.split("/", 1)[0]
        if top:
            print(top)
            break
PY
)"
[[ -n "$TOPDIR" && "$TOPDIR" != "." && "$TOPDIR" != "/" ]] || die "Could not detect tarball top-level directory"
log "Top-level directory: ${TOPDIR}"

CACTUS_DIR="${INSTALL_ROOT}/${TOPDIR}"

step "Extracting Cactus"
log "Target directory: ${CACTUS_DIR}"
rm -rf "$CACTUS_DIR"
tar -xzf "$TARBALL_PATH" -C "$INSTALL_ROOT"

[[ -d "$CACTUS_DIR" ]] || die "Directory not found after extraction: $CACTUS_DIR"
[[ -d "${CACTUS_DIR}/bin" ]] || die "Missing ${CACTUS_DIR}/bin"
[[ -d "${CACTUS_DIR}/lib" ]] || die "Missing ${CACTUS_DIR}/lib"
log "Extraction complete; bin/lib directory checks passed."

step "Configuring Cactus environment variables for this install process"
export CACTUS_DIR
export PATH="${CACTUS_DIR}/bin${PATH:+:${PATH}}"
export PYTHONPATH="${CACTUS_DIR}/lib${PYTHONPATH:+:${PYTHONPATH}}"
export LD_LIBRARY_PATH="${CACTUS_DIR}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
log "CACTUS_DIR=${CACTUS_DIR}"
log "Temporarily updated PATH/PYTHONPATH/LD_LIBRARY_PATH."

step "Installing Cactus Python package and Toil dependencies"
cd "$CACTUS_DIR"

log "Upgrading setuptools/pip/wheel..."
python -m pip install -U setuptools pip wheel
log "Installing Cactus Python package..."
python -m pip install -U .
log "Installing Toil requirements..."
python -m pip install -U -r ./toil-requirement.txt

ACTIVATE_DIR="${CONDA_PREFIX}/etc/conda/activate.d"
DEACTIVATE_DIR="${CONDA_PREFIX}/etc/conda/deactivate.d"

step "Writing Conda activate/deactivate hooks"
log "activate hook   : ${ACTIVATE_DIR}/cactus.sh"
log "deactivate hook : ${DEACTIVATE_DIR}/cactus.sh"
mkdir -p "$ACTIVATE_DIR" "$DEACTIVATE_DIR"

Q_CACTUS_DIR="$(printf '%q' "$CACTUS_DIR")"

cat > "${ACTIVATE_DIR}/cactus.sh" <<EOF
# Auto-generated by cactus-install.sh
export CACTUS_DIR=${Q_CACTUS_DIR}

export _CACTUS_OLD_PATH="\${PATH:-}"
export _CACTUS_OLD_PYTHONPATH="\${PYTHONPATH:-}"
export _CACTUS_OLD_LD_LIBRARY_PATH="\${LD_LIBRARY_PATH:-}"

case ":\${PATH:-}:" in
  *":\${CACTUS_DIR}/bin:"*) ;;
  *) export PATH="\${CACTUS_DIR}/bin\${PATH:+:\${PATH}}" ;;
esac

case ":\${PYTHONPATH:-}:" in
  *":\${CACTUS_DIR}/lib:"*) ;;
  *) export PYTHONPATH="\${CACTUS_DIR}/lib\${PYTHONPATH:+:\${PYTHONPATH}}" ;;
esac

case ":\${LD_LIBRARY_PATH:-}:" in
  *":\${CACTUS_DIR}/lib:"*) ;;
  *) export LD_LIBRARY_PATH="\${CACTUS_DIR}/lib\${LD_LIBRARY_PATH:+:\${LD_LIBRARY_PATH}}" ;;
esac
EOF

cat > "${DEACTIVATE_DIR}/cactus.sh" <<'EOF'
# Auto-generated by cactus-install.sh
if [ -n "${_CACTUS_OLD_PATH+x}" ]; then
  export PATH="${_CACTUS_OLD_PATH}"
  unset _CACTUS_OLD_PATH
fi

if [ -n "${_CACTUS_OLD_PYTHONPATH+x}" ]; then
  export PYTHONPATH="${_CACTUS_OLD_PYTHONPATH}"
  unset _CACTUS_OLD_PYTHONPATH
fi

if [ -n "${_CACTUS_OLD_LD_LIBRARY_PATH+x}" ]; then
  export LD_LIBRARY_PATH="${_CACTUS_OLD_LD_LIBRARY_PATH}"
  unset _CACTUS_OLD_LD_LIBRARY_PATH
fi

unset CACTUS_DIR
EOF

chmod +x "${ACTIVATE_DIR}/cactus.sh" "${DEACTIVATE_DIR}/cactus.sh"
log "Conda hooks written."

step "Verifying cactus -h"
cactus -h >/tmp/cactus-install-help.txt
head -n 20 /tmp/cactus-install-help.txt

step "Installation complete"
log "Cactus installed successfully."
log "Tarball     : ${TARBALL_PATH}"
log "Install dir : ${CACTUS_DIR}"
log ""
log "Reactivate the Conda environment to load the saved settings:"
log ""
log "  conda deactivate"
log "  conda activate ${CONDA_DEFAULT_ENV:-your-env-name}"
log ""
log "Then test again:"
log ""
log "  cactus -h"
