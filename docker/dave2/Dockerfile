# =============================================================================
# Cam2Sim "dave2" image
#
# Runs system_under_test/communicator.py as a standalone TCP server
# (localhost:5090) used by Step 5 modes 5B and 5D.
#
# Isolated from the pipeline image because TensorFlow==2.13.1 requires
# Python 3.8, which conflicts with the data_extraction/nerfstudio envs.
#
# GPU is optional here: DAVE-2 (a small steering CNN) runs comfortably on
# CPU. Left off by default to keep the full VRAM budget on the RTX 2080
# for CARLA + Gaussian Splatting rendering during 5D. Add `--gpus all` in
# docker-compose.yml if you want it on GPU instead.
# =============================================================================
FROM python:3.8-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir \
        tensorflow==2.13.1 \
        pillow \
        opencv-python-headless

WORKDIR /workspace/cam2sim/system_under_test

# Code + final.h5 weights are bind-mounted at runtime, not baked into the
# image (final.h5 is fetched by fetch_assets.sh on the host).
CMD ["python", "communicator.py"]
