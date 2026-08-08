# Cam2Sim — Docker setup

Containerized version of the pipeline for hosts (like Ubuntu 26.04) where
the pinned dependency versions (CUDA 11.8, torch 2.1.0, TF 2.13.1, gcc-11)
don't install cleanly on the native system.

## Layout

```
docker-compose.yml
docker/pipeline/Dockerfile   # data_extraction + nerfstudio conda envs, COLMAP
docker/dave2/Dockerfile      # TensorFlow 2.13 / Python 3.8 DAVE-2 server
scripts/run_step5.sh         # container-native replacement for step5.sh
fetch_assets.sh              # downloads weights/datasets (run on host first)
```

`carla-server` uses the official `carlasim/carla:0.9.16` image directly — no
custom Dockerfile needed.

## One-time host setup

1. **NVIDIA Container Toolkit** (lets Docker see the GPU):
   ```bash
   sudo apt install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
   ```
   The last command should print your RTX 2080. If it doesn't, stop here —
   nothing downstream will work without this.

2. **X11 access for the pygame window** (needed for 5C/5D's live view):
   ```bash
   xhost +local:docker
   ```
   Run this once per host login session, before `docker compose up`. It's
   intentionally scoped to local Docker containers, not the whole network.

3. **Fetch model weights and datasets** (run once, on the host — no GPU/conda
   needed for this step):
   ```bash
   bash fetch_assets.sh --all
   ```

## Build and start

```bash
docker compose build
docker compose up -d pipeline carla-server
```

Check CARLA came up cleanly:
```bash
docker compose logs -f carla-server
```

## Running pipeline stages

Components 1-4 and validation (6) don't need CARLA running continuously — exec
directly into the pipeline container:

```bash
docker compose exec pipeline bash
conda activate data_extraction
bash 1_extract_ROS_data/step1.sh
bash 2_process_datasets/step2.sh
bash 3_generate_simulation_data/step3.sh   # skips 3C, since carla-server is already up
```

Component 4 (Gaussian Splatting training) uses the `nerfstudio` env inside the
same container — COLMAP and `ns-train` are both available there.

Component 5 (driving simulation) is orchestrated by `scripts/run_step5.sh`,
which replaces the old `gnome-terminal`-per-step approach:

```bash
bash scripts/run_step5.sh --mode 5A   # CARLA-only trajectory replay
bash scripts/run_step5.sh --mode 5B   # CARLA-only + DAVE-2 closed loop
bash scripts/run_step5.sh --mode 5C   # + Gaussian Splatting replay (live window)
bash scripts/run_step5.sh --mode 5D   # + Gaussian Splatting + DAVE-2 closed loop
```

Component 6 (validation) runs in `data_extraction`, same pattern as 1-3.

## Design notes / things I changed vs. the bare-metal setup

- **`3C_setup_carla.py` is skipped entirely.** It launches `CarlaUE4.sh` as a
  subprocess expecting a local install; the `carla-server` container already
  **is** the running CARLA instance, so this step doesn't apply.
- **No source-code edits needed for networking.** `carla-server` and
  `dave2-server` join the `pipeline` container's network namespace
  (`network_mode: "service:pipeline"`), so the project's hardcoded
  `CARLA_IP="127.0.0.1"` (in `3_generate_simulation_data/utils/config.py`)
  and DAVE-2's `localhost:5090` (in `communicator.py` /
  `dave2_connection.py`) resolve correctly without modification.
- **`dave2-server` is behind a compose profile** (`--profile dave2`), since
  it's only needed for 5B/5D — `run_step5.sh` starts it automatically when
  the mode requires it.

## Fixes folded in from the Quick Start doc

Two issues documented in the [bare-metal Quick Start writeup](https://chris7462.github.io/docs/mics-setup/cam2sim-quickstart)
are already handled in `docker/pipeline/Dockerfile`, so you shouldn't hit
them in the container:

- **`pyliblzfse`/`fpsample` build failures** — added `python3.10-dev` and
  `liblzfse-dev` system packages before `pip install -r
  data_extraction_requirements.txt` runs.
- **Black GS panel / `Split: none` / `weights_only` warning** — added a
  build-time patch that sets `weights_only=False` in Nerfstudio's
  `eval_load_checkpoint()` (used for replay/inference, not training resume).
  This pin (`torch==2.1.0+cu118`) predates torch's default flip to
  `weights_only=True` (that happened at torch 2.6), so it likely wouldn't
  trigger here anyway — the patch is defensive insurance in case a
  transitive dependency ever bumps torch, since the failure mode is a
  *silent* black panel rather than a crash.

## Things I couldn't verify without the live repo / a test run — check these

- **CARLA version bumped to 0.9.16, ahead of the repo's own `carla==0.9.15`
  pin.** CARLA's docs explicitly changed how the image is run starting
  0.9.16 (no more `cd`-into-install-dir dance — see
  <https://carla.readthedocs.io/en/latest/build_docker/>), which resolves
  the earlier "unverified binary path" concern. The `carla-server` image
  and the `carla` Python client in `docker/pipeline/Dockerfile` are both
  updated to `0.9.16` together, since client/server versions must match.
  This is a deliberate deviation from the original repo's tested
  `carla==0.9.15` pin — 0.9.x patch bumps are usually just fixes, but
  worth watching for API-shape or map-loading differences the first time
  Step 3 (`3F_generate_carla_scenario.py`) and Step 5 run against it.
- **`gsplat`/`tinycudann` build success.** These are compiled CUDA
  extensions; the Dockerfile sets gcc-11 and `TCNN_CUDA_ARCHITECTURES=75`
  (correct for the 2080's Turing/SM 7.5), but the actual build should be
  watched on first `docker compose build` for compiler errors.
- **`communicator.py` bind address.** I'm assuming it binds `0.0.0.0` or
  `localhost` in a way that's reachable at `127.0.0.1:5090` from the shared
  namespace — worth a quick check of that file.
- **VRAM headroom on the 2080 (8GB) for mode 5D.** CARLA + gsplat rendering
  + DAVE-2 inference concurrently is the tightest case. If you hit CUDA OOM:
  lower CARLA's `-quality-level` (already Low), reduce the pygame window /
  render resolution in `5D_dave2.py`, or run `dave2-server` with
  `CUDA_VISIBLE_DEVICES=""` to force it onto CPU (it's a small model, CPU
  inference should still be fast enough for closed-loop control).

## Next step (once this runs)

You mentioned wanting to modernize each stage after confirming the pipeline
runs as-is. Good candidates, roughly in order of how contained the blast
radius is: bump `torch`/`nerfstudio` first (self-contained to the
`nerfstudio` env), then CARLA (bigger — client + server + config.py all move
together), then mmdet3d/mmcv (OpenMMLab's compatibility matrix is the main
constraint), then TensorFlow/DAVE-2 last (smallest, most isolated piece).
