# =============================================================================
# Cam2Sim "pipeline" image
#
# Contains two Conda environments matching the original bare-metal setup:
#   - data_extraction  (Python 3.10): stages 1, 2, 3, 5A, 5B, COLMAP
#   - nerfstudio       (Python 3.10): stage 4, 5C, 5D (Gaussian Splatting / gsplat)
#
# The `dave_2` environment is intentionally NOT here — it runs in its own
# lightweight container (docker/dave2/Dockerfile) since TensorFlow 2.13
# requires Python 3.8, which conflicts with everything else in this image.
#
# GPU: targets CUDA 11.8 (matches torch==2.1.0+cu118, carla==0.9.16 client,
# nerfstudio==1.1.5). Verified compatible with RTX 20/30-series and driver
# 610.x via NVIDIA's forward driver compatibility.
# =============================================================================
FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV NVIDIA_DRIVER_CAPABILITIES=all

# -----------------------------------------------------------------------------
# System packages
#   - build-essential/git/wget/curl: general build tooling
#   - cmake: needed to build lzfse from source below
#   - gcc-11/g++-11: required by Nerfstudio/gsplat CUDA extension compilation
#     (see README note: default GCC on newer Ubuntu is too new for CUDA 11.8)
#   - colmap: stage 4 sparse reconstruction (via apt, GPU-enabled build)
#   - libgl1/libglib2.0-0/libsm6/libxext6/libxrender1/libx11-6/mesa/libglu1:
#     X11 + OpenGL libs needed for pygame + pyrender GUI windows (5C/5D)
#   - ffmpeg: occasionally needed by video/frame tooling
#
#   NOTE: liblzfse-dev is intentionally NOT installed via apt here — it's
#   only packaged starting Ubuntu 24.04 (noble); this image's Ubuntu 22.04
#   (jammy) base doesn't have it. Built from source instead, just below.
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    wget \
    curl \
    unzip \
    ca-certificates \
    gcc-11 \
    g++-11 \
    colmap \
    ffmpeg \
    python3.10-dev \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libx11-6 \
    libxi6 \
    libxxf86vm1 \
    libglu1-mesa \
    mesa-utils \
    x11-apps \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# lzfse (built from source): jammy's apt repos don't have liblzfse-dev, but
# pyliblzfse/fpsample (pulled in by data_extraction_requirements.txt) need
# the header + library to compile against. Small, dependency-free library,
# so building it is simple and doesn't need pinning to any particular
# commit — installs to /usr/local/{include,lib} where gcc/pip find it by
# default.
# -----------------------------------------------------------------------------
RUN git clone --depth 1 https://github.com/lzfse/lzfse.git /tmp/lzfse \
    && mkdir -p /tmp/lzfse/build \
    && cd /tmp/lzfse/build \
    && cmake .. \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /tmp/lzfse

# Use gcc-11/g++-11 as the default compiler (needed by gsplat/tinycudann build)
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 100 \
    && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-11 100

# -----------------------------------------------------------------------------
# Miniconda
# -----------------------------------------------------------------------------
ENV CONDA_DIR=/opt/conda
ENV PATH=$CONDA_DIR/bin:$PATH

RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p $CONDA_DIR \
    && rm /tmp/miniconda.sh \
    && conda config --set always_yes true \
    && conda clean -afy

# Anaconda now requires explicit Terms of Service acceptance for the
# default channels before any non-interactive `conda create`/`conda
# install` will run. Without this, builds fail with
# "CondaToSNonInteractiveError". Accept once here, up front.
RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main \
    && conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r

# -----------------------------------------------------------------------------
# Global pip constraint: setuptools<81.
#
# setuptools 81+ (Feb 2026) removed pkg_resources entirely, which several
# packages built here still import directly in legacy setup.py code
# (tiny-cuda-nn, and - once forced to build from source for multi-arch
# CUDA support below - mmcv/mmdet3d too). Simply pinning setuptools in an
# env, or passing --no-build-isolation per-install, proved unreliable:
# pip still spun up an isolated build environment for mmcv/mmdet3d
# (visible as a /tmp/pip-build-env-.../overlay/... path in the traceback)
# that fetched its own fresh, unpinned setuptools regardless of what the
# env or the --no-build-isolation flag said.
#
# PIP_CONSTRAINT is different: pip explicitly documents that constraints
# files apply even to a package's *build* dependencies inside an isolated
# build environment, not just to the packages you're directly installing.
# Setting this once, globally, before any env is created, is a more
# reliable fix than per-install flags - every pip invocation for the rest
# of this Dockerfile respects it automatically.
# -----------------------------------------------------------------------------
RUN echo "setuptools<81" > /tmp/pip-constraints.txt
ENV PIP_CONSTRAINT=/tmp/pip-constraints.txt

# -----------------------------------------------------------------------------
# data_extraction environment
# -----------------------------------------------------------------------------
COPY data_extraction_requirements.txt /tmp/data_extraction_requirements.txt

RUN conda create -n data_extraction python=3.10 -y \
    && conda run -n data_extraction pip install -U pip setuptools wheel \
    && conda run -n data_extraction pip install -r /tmp/data_extraction_requirements.txt

# OpenMMLab stack for stage 2 (parked-vehicle detection). mmengine/mmdet
# are pure-Python-ish (fine as prebuilt wheels via mim), but mmcv and
# mmdet3d ship compiled CUDA ops (deformable conv, NMS, etc.) - prebuilt
# wheels from mim only include kernels for whatever GPU architectures
# OpenMMLab happened to target when they built that wheel. This breaks
# with "CUDA error: no kernel image is available for execution on the
# device" on any GPU generation not in that list (e.g. an H100/Hopper,
# compute capability 9.0, isn't covered by CUDA-11.8-era mmcv wheels).
#
# Fix: build mmcv/mmdet3d from source with an explicit multi-architecture
# TORCH_CUDA_ARCH_LIST, so this one image works across whatever GPU
# generation it happens to run on - RTX 2080 (Turing, 7.5) through H100
# (Hopper, 9.0), plus the generations in between. Building from source
# takes noticeably longer than pulling a wheel (compiling every op for
# every listed architecture) - budget extra build time for this step.
# Extend this list if you test on a GPU generation not yet covered.
ENV FORCE_CUDA=1
ENV MMCV_WITH_OPS=1
ENV TORCH_CUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0+PTX"

# NOTE: forcing mmcv/mmdet3d to build from source (--no-binary) means
# they now hit the same setuptools 81+ / pkg_resources issue documented
# above for tiny-cuda-nn - their legacy setup.py also imports
# pkg_resources directly, and pip's build isolation fetches its own
# unpinned setuptools regardless of what's installed in the env unless
# --no-build-isolation is set. Same two-part fix: pin setuptools<81 in
# this env too (data_extraction, separate from nerfstudio's own pin
# above), and use --no-build-isolation. --no-build-isolation requires
# torch to already be importable in the env, which it is by this point
# (installed via data_extraction_requirements.txt).
RUN conda run -n data_extraction pip install "setuptools<81" \
    && conda run -n data_extraction pip install -U openmim \
    && conda run -n data_extraction mim install mmengine \
    && conda run -n data_extraction pip install --no-build-isolation \
       mmcv==2.1.0 --no-binary mmcv --no-cache-dir \
    && conda run -n data_extraction mim install mmdet==3.2.0 \
    && conda run -n data_extraction pip install --no-build-isolation \
       mmdet3d==1.4.0 --no-binary mmdet3d --no-cache-dir

# -----------------------------------------------------------------------------
# nerfstudio environment
#   Nerfstudio's own installer assumes an interactive host; we replicate its
#   steps explicitly here for a reproducible, non-interactive build.
#   Pinned to nerfstudio==1.1.5 + torch==2.1.0+cu118 as used by the project.
# -----------------------------------------------------------------------------
RUN conda create -n nerfstudio python=3.10 -y

RUN conda run -n nerfstudio pip install -U pip wheel \
    && conda run -n nerfstudio pip install "setuptools<81" \
    && conda run -n nerfstudio pip install torch==2.1.0+cu118 torchvision==0.16.0+cu118 \
       --extra-index-url https://download.pytorch.org/whl/cu118

# tiny-cuda-nn (compiled CUDA extension - needs gcc-11 set above)
#
# NOTE: setuptools 81+ (Feb 2026) removed pkg_resources entirely, which
# tiny-cuda-nn's bindings/torch/setup.py still imports directly at the top
# of the file (`from pkg_resources import parse_version`) - this breaks
# the build with "ModuleNotFoundError: No module named 'pkg_resources'"
# regardless of Python/CUDA/torch version. Pinning setuptools<81 in the
# env (above) isn't enough on its own: pip's build isolation creates a
# separate temporary venv for building the wheel and fetches its own
# (latest, broken) setuptools into it, ignoring what's pinned in the main
# env. --no-build-isolation forces pip to build using the env's already-
# installed (pinned) packages instead. Re-check if this workaround can be
# dropped on a future nerfstudio/tiny-cuda-nn upgrade.
ENV CC=/usr/bin/gcc-11
ENV CXX=/usr/bin/g++-11
# Multi-architecture build, same reasoning as mmcv above: a single value
# (originally 75, tuned only for the RTX 2080) fails with "no kernel image
# available" on any other GPU generation. tiny-cuda-nn's build accepts a
# semicolon-separated list (no "+PTX" suffix support here, unlike
# TORCH_CUDA_ARCH_LIST above - every target architecture must be listed
# explicitly). Extend this list if you test on a GPU generation not yet
# covered. Multi-arch build takes noticeably longer than a single target.
ENV TCNN_CUDA_ARCHITECTURES="75;80;86;89;90"

RUN conda run -n nerfstudio pip install ninja \
    && conda run -n nerfstudio pip install --no-build-isolation \
       "git+https://github.com/NVlabs/tiny-cuda-nn.git#subdirectory=bindings/torch"

RUN conda run -n nerfstudio pip install nerfstudio==1.1.5

# -----------------------------------------------------------------------------
# Patch: torch>=2.6 changed torch.load()'s default to weights_only=True, which
# breaks loading Splatfacto checkpoints during replay/inference (symptom:
# "Split: none" / black GS panel / "No GS models loaded - falling back to
# only_carla mode", with a torch.load / weights_only warning just above it).
# We pin torch==2.1.0 above (pre-dates the default flip), so this shouldn't
# trigger - but we patch defensively in case a transitive dependency bumps
# torch later, since the failure mode is silent (no crash, just a black
# panel) rather than a loud error.
#
# Only patches eval_load_checkpoint() in eval_utils.py (used for
# replay/inference). Does NOT touch trainer.py's checkpoint loading, which
# is for resuming *training* and should keep the strict default.
# -----------------------------------------------------------------------------
RUN EVAL_UTILS=$(conda run -n nerfstudio python -c "import nerfstudio.utils.eval_utils as m; print(m.__file__)") \
    && echo "Patching: $EVAL_UTILS" \
    && python3 - "$EVAL_UTILS" <<'PATCH_EOF'
import re
import sys

path = sys.argv[1]
with open(path, "r") as f:
    src = f.read()

# Match torch.load(load_path, ...) calls that don't already set weights_only
pattern = re.compile(r"torch\.load\(\s*load_path([^)]*)\)")

def add_weights_only(match):
    inner = match.group(1)
    if "weights_only" in inner:
        return match.group(0)
    return f"torch.load(load_path{inner}, weights_only=False)"

patched, n = pattern.subn(add_weights_only, src)

if n == 0:
    print(f"[WARN] No torch.load(load_path...) call found in {path} - "
          "nerfstudio's internals may have changed. Check manually if the "
          "GS replay panel comes up black.")
else:
    with open(path, "w") as f:
        f.write(patched)
    print(f"[OK] Patched {n} torch.load(load_path...) call(s) in {path}")
PATCH_EOF

# Extra packages required by 5C/5D (CARLA client, pygame UI, CARLA<->UTM projection)
RUN conda run -n nerfstudio pip install \
    carla==0.9.16 \
    pygame==2.6.1 \
    pyproj==3.5.0 \
    pyrender==0.1.45

# -----------------------------------------------------------------------------
# Convenience: activate data_extraction by default in interactive shells
# -----------------------------------------------------------------------------
RUN echo "source $CONDA_DIR/etc/profile.d/conda.sh" >> /etc/bash.bashrc \
    && echo "conda activate data_extraction" >> /etc/bash.bashrc

WORKDIR /workspace/cam2sim

# Project code + data are bind-mounted at runtime via docker-compose, not
# baked into the image, so day-to-day script edits don't require rebuilding.
CMD ["/bin/bash"]
