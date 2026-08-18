#!/usr/bin/env bash
set -euo pipefail

# Reproducible Linux setup for AAAGameForge human-motion inference.
# Third-party sources, environments and caches stay outside the Git checkout.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${AAAGF_REPO_ROOT:-$(cd -- "${SCRIPT_DIR}/../../.." && pwd)}"

usage() {
  cat <<'EOF'
Usage: bash scripts/installing/gen_motion/install.sh [RUNTIME_ROOT] [OPTIONS]

Clone pinned Puppeteer and MoMask sources, create three isolated Conda
environments, install their dependencies, and download the selected weights.
RUNTIME_ROOT defaults to AAAGF_RUNTIME_ROOT or the XDG user data directory.

Options:
  --runtime-root PATH  External source and weight root (positional form also works)
  --skip-weights       Install sources and environments without downloading weights
  -h, --help           Show this help message

Optional environment variables:
  AAAGF_CACHE_ROOT       download/build cache root
  AAAGF_CONDA_BIN        Conda executable when it is not on PATH
  AAAGF_CUDA_ARCH_LIST   explicit CUDA architecture list for extension builds
  MAX_JOBS               parallel extension-build jobs (default: 4)
EOF
}

RUNTIME_ARGUMENT=""
DOWNLOAD_WEIGHTS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-root)
      if [[ $# -lt 2 ]]; then
        echo "--runtime-root requires a path" >&2
        exit 2
      fi
      RUNTIME_ARGUMENT="$2"
      shift 2
      ;;
    --skip-weights)
      DOWNLOAD_WEIGHTS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "${RUNTIME_ARGUMENT}" ]]; then
        echo "RUNTIME_ROOT was provided more than once" >&2
        exit 2
      fi
      RUNTIME_ARGUMENT="$1"
      shift
      ;;
  esac
done

RUNTIME_ROOT="${RUNTIME_ARGUMENT:-${AAAGF_RUNTIME_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/aaagameforge}}"
CACHE_ROOT="${AAAGF_CACHE_ROOT:-${XDG_CACHE_HOME:-${HOME}/.cache}/aaagameforge}"
PUPPETEER_COMMIT="1c0f9fc6ad209667a0ec5ceac9b59964938a8b51"
MOMASK_COMMIT="94a6636c9c463b7a9414c3401a6f1b67e6c51824"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer targets Linux. On Windows, run it inside WSL2." >&2
  exit 2
fi

if [[ -n "${AAAGF_CONDA_BIN:-}" ]]; then
  CONDA_BIN="${AAAGF_CONDA_BIN}"
elif [[ -n "${CONDA_EXE:-}" ]]; then
  CONDA_BIN="${CONDA_EXE}"
elif command -v conda >/dev/null 2>&1; then
  CONDA_BIN="conda"
else
  CONDA_BIN=""
fi

export AAAGF_RUNTIME_ROOT="${RUNTIME_ROOT}"
export AAAGF_CACHE_ROOT="${CACHE_ROOT}"
export HF_HOME="${CACHE_ROOT}/huggingface"
export PIP_CACHE_DIR="${CACHE_ROOT}/pip"
export TORCH_HOME="${CACHE_ROOT}/torch"
export CONDA_PKGS_DIRS="${CACHE_ROOT}/conda-pkgs"
export CONDA_CHANNEL_PRIORITY="strict"
export MAX_JOBS="${MAX_JOBS:-4}"
if [[ -n "${AAAGF_CUDA_ARCH_LIST:-}" ]]; then
  export TORCH_CUDA_ARCH_LIST="${AAAGF_CUDA_ARCH_LIST}"
fi

mkdir -p \
  "${RUNTIME_ROOT}/sources" \
  "${RUNTIME_ROOT}/test_assets" \
  "${HF_HOME}" "${PIP_CACHE_DIR}" "${TORCH_HOME}" "${CONDA_PKGS_DIRS}" \
  "${RUNTIME_ROOT}/logs"

if [[ -z "${CONDA_BIN}" ]] || ! "${CONDA_BIN}" --version >/dev/null 2>&1; then
  echo "Conda was not found. Install Miniforge/Conda or set AAAGF_CONDA_BIN." >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Git is required to clone the pinned Puppeteer and MoMask sources." >&2
  exit 2
fi

clone_at_commit() {
  local url="$1"
  local destination="$2"
  local commit="$3"
  if [[ ! -d "${destination}/.git" ]]; then
    git clone "${url}" "${destination}"
  fi
  git -C "${destination}" fetch origin "${commit}"
  git -C "${destination}" checkout --detach "${commit}"
}

clone_at_commit \
  https://github.com/Seed3D/Puppeteer.git \
  "${RUNTIME_ROOT}/sources/Puppeteer" \
  "${PUPPETEER_COMMIT}"
git -C "${RUNTIME_ROOT}/sources/Puppeteer" submodule update --init --recursive --force

clone_at_commit \
  https://github.com/EricGuo5513/momask-codes.git \
  "${RUNTIME_ROOT}/sources/momask-codes" \
  "${MOMASK_COMMIT}"

ensure_env() {
  local name="$1"
  local python_version="$2"
  if ! "${CONDA_BIN}" env list | awk '{print $1}' | grep -Fxq "${name}"; then
    "${CONDA_BIN}" create -n "${name}" -y --override-channels \
      -c conda-forge "python=${python_version}" pip
  fi
}

ensure_env aaagf-puppeteer 3.10.13
ensure_env aaagf-momask 3.10
ensure_env aaagf-retarget-bpy 3.11

# Puppeteer needs a CUDA 11.8 build toolchain for flash-attn and custom ops.
"${CONDA_BIN}" install -n aaagf-puppeteer -y --override-channels \
  -c nvidia/label/cuda-11.8.0 -c conda-forge \
  "python=3.10.13" pip cmake ninja \
  "cuda-nvcc=11.8.89" \
  "cuda-cudart-dev=11.8.89" "cuda-cccl=11.8.89" \
  "cuda-driver-dev=11.8.89" "cuda-nvrtc-dev=11.8.89" \
  "gcc_linux-64=11" "gxx_linux-64=11" \
  libopengl "numpy=1.26.4"

# MoMask and bpy remain isolated because their NumPy/Python constraints differ.
"${CONDA_BIN}" install -n aaagf-momask -y --override-channels \
  -c conda-forge "python=3.10" pip ffmpeg "numpy=1.23.5"
"${CONDA_BIN}" install -n aaagf-retarget-bpy -y --override-channels \
  -c conda-forge "python=3.11" pip xorg-libsm xorg-libxext xorg-libxrender \
  xorg-libxi libxkbcommon.so.0 # Linux runtime libraries required by bpy for motion retargeting and FBX export

"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  torch==2.1.1 torchvision==0.16.1 torchaudio==2.1.1 \
  --index-url https://download.pytorch.org/whl/cu118
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  "cython==0.29.36"
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  "tetgen==0.5.2" --no-build-isolation
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  -r "${RUNTIME_ROOT}/sources/Puppeteer/requirements.txt"
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  "numpy==1.26.4" "setuptools==69.5.1" "wheel==0.43.0"
"${CONDA_BIN}" run -n aaagf-puppeteer bash -lc \
  'export CUDA_HOME="${CONDA_PREFIX}"; python -m pip install flash-attn==2.6.3 --no-build-isolation'
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  torch-scatter -f https://data.pyg.org/whl/torch-2.1.1+cu118.html
"${CONDA_BIN}" run -n aaagf-puppeteer python -m pip install \
  --no-index --no-cache-dir pytorch3d \
  -f https://dl.fbaipublicfiles.com/pytorch3d/packaging/wheels/py310_cu118_pyt211/download.html

"${CONDA_BIN}" run -n aaagf-momask python -m pip install \
  torch==2.1.1 torchvision==0.16.1 torchaudio==2.1.1 \
  --index-url https://download.pytorch.org/whl/cu118
"${CONDA_BIN}" run -n aaagf-momask python -m pip install \
  "setuptools==69.5.1" "wheel==0.43.0"
"${CONDA_BIN}" run -n aaagf-momask python -m pip install \
  "chumpy==0.70" --no-build-isolation
"${CONDA_BIN}" run -n aaagf-momask python -m pip install \
  "numpy==1.23.5" "einops==0.6.1" "ffmpy==0.3.1" "ftfy==6.1.1" \
  "gdown==4.7.1" "Pillow>=9.2,<11" "PyYAML>=6" scipy scikit-learn \
  scikit-image "matplotlib>=3.6,<3.8" tqdm trimesh \
  "vector-quantize-pytorch==1.6.30" \
  smplx huggingface_hub "requests>=2.32,<3" "urllib3>=2.2,<3" \
  "certifi>=2024"
"${CONDA_BIN}" run -n aaagf-momask python -m pip install \
  "git+https://github.com/openai/CLIP.git"

"${CONDA_BIN}" run -n aaagf-retarget-bpy python -m pip install \
  "bpy==4.2.0" "numpy<2" "trimesh>=4.2"

MICHELANGELO_LINK="${RUNTIME_ROOT}/sources/Puppeteer/skinning/third_partys/Michelangelo"
if [[ ! -e "${MICHELANGELO_LINK}" ]]; then
  ln -s ../../skeleton/third_partys/Michelangelo "${MICHELANGELO_LINK}"
fi

if [[ "${DOWNLOAD_WEIGHTS}" -eq 1 ]]; then
  "${CONDA_BIN}" run -n aaagf-momask python \
    "${SCRIPT_DIR}/download_weights.py" \
    --runtime-root "${RUNTIME_ROOT}" \
    --cache-root "${CACHE_ROOT}"
fi

cat <<EOF
Motion runtime environments are ready.
Runtime root:     ${RUNTIME_ROOT}
Cache root:       ${CACHE_ROOT}
Puppeteer Python: $("${CONDA_BIN}" run -n aaagf-puppeteer python -c 'import sys; print(sys.executable)')
MoMask Python:    $("${CONDA_BIN}" run -n aaagf-momask python -c 'import sys; print(sys.executable)')
Retarget Python:  $("${CONDA_BIN}" run -n aaagf-retarget-bpy python -c 'import sys; print(sys.executable)')

Before a real run:
  export AAAGF_RUNTIME_ROOT="${RUNTIME_ROOT}"
  export AAAGF_CACHE_ROOT="${CACHE_ROOT}"
  source "${REPO_ROOT}/scripts/installing/gen_motion/runtime_env.sh"
EOF

if [[ "${DOWNLOAD_WEIGHTS}" -eq 0 ]]; then
  cat <<EOF

Weights were skipped. Download them later with:
  "${CONDA_BIN}" run -n aaagf-momask python \
    "${REPO_ROOT}/scripts/installing/gen_motion/download_weights.py"
EOF
fi
