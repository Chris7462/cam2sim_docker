# Cam2Sim Docker Installation Guide (Ubuntu 26.04)

This is the Docker-based install guide: same full pipeline (Steps 1–6) as
the native guides, but running inside three containers instead of Conda
environments or venvs. No system-wide CUDA toolkit installation, no
gcc-11/glibc header patching on the host — CUDA 11.8 lives inside the
container's Ubuntu 22.04 base image, which still has full package support
for it (unlike your Ubuntu 26.04 host).

## Why Docker, for this pipeline specifically

- Ubuntu 26.04 doesn't have CUDA 11.8 `.deb` packages available at all —
  the container's Ubuntu 22.04 base does.
- The three environments (`data_extraction`, `nerfstudio`, `dave_2`) have
  genuinely incompatible Python/CUDA requirements; containers isolate them
  more cleanly than either Conda or venv, since even system-level
  libraries (gcc, X11/GL, COLMAP) are isolated per-container too.
- `carla-server` and `dave2-server` share the `pipeline` container's
  network namespace, so the project's hardcoded `127.0.0.1` addresses
  (CARLA IP, DAVE-2 socket) work without touching any source files.

## Folder layout

Place these inside your `cam2sim/` clone, alongside the existing
`data_extraction_requirements.txt`, `1_extract_ROS_data/`, etc.:

```
cam2sim/
├── docker-compose.yml
├── fetch_assets.sh
├── docker/
│   ├── README.md
│   ├── pipeline/
│   │   └── Dockerfile
│   └── dave2/
│       └── Dockerfile
├── scripts/
│   └── run_step5.sh
├── data_extraction_requirements.txt   (already in repo)
├── 1_extract_ROS_data/                (already in repo)
├── 2_process_datasets/
├── 3_generate_simulation_data/
├── 4_gaussian_splatting_preparation/
│   └── 4A_CLI_colmap_reconstruction.sh   (added — CLI alternative to the GUI-only 4A_colmap_guide.md)
├── 5_execute_simulation/
└── system_under_test/
```

## What each container is

| Container | Base | Contains | Runs |
|---|---|---|---|
| `pipeline` | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` | `data_extraction` + `nerfstudio` conda envs, COLMAP, gcc-11, X11/GL libs | Components 1–4, Component 5's CARLA/GS scripts, Component 6 |
| `carla-server` | official `carlasim/carla:0.9.16` | CARLA simulator binary | The running CARLA instance |
| `dave2-server` | `python:3.8-slim` | TensorFlow 2.13, Pillow, OpenCV | DAVE-2 TCP server (5B/5D only) |

`carla-server` and `dave2-server` don't need their own network config —
they join `pipeline`'s network namespace (`network_mode: "service:pipeline"`
in `docker-compose.yml`), so `127.0.0.1:2000` (CARLA) and
`127.0.0.1:5090` (DAVE-2) resolve correctly from inside `pipeline`, exactly
as the original scripts expect.

## Prerequisites

- Ubuntu 26.04 (or similar) host
- NVIDIA GPU, compute capability ≥ 7.5 (RTX 20-series or newer)
- Docker + Docker Compose installed
- ~30+ GB free disk space (images + datasets + Gaussian Splatting models)

## Step 1. Install the NVIDIA Container Toolkit

```sh
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify GPU passthrough works before doing anything else:

```sh
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

This should print your GPU. If it doesn't, stop here — nothing downstream
works without this.

## Step 2. Allow X11 access for the live pygame window

Steps 5C/5D open a live CARLA + Gaussian Splatting side-by-side window.
Run this once per login session, before `docker compose up`:

```sh
xhost +local:docker
```

Scoped to local Docker containers only, not the whole network.

## Step 3. Clone the repo and add the Docker files

```sh
git clone git@github.com:ast-fortiss-tum/cam2sim.git cam2sim
cd cam2sim
```

Place `docker-compose.yml`, `fetch_assets.sh`, `docker/`, and `scripts/`
into this directory per the [folder layout](#folder-layout) above.

## Step 4. Build the images

```sh
docker compose build
```

Builds `pipeline` (CUDA 11.8, both conda envs, COLMAP — this is the slow
one, expect a while for `gsplat`/`tiny-cuda-nn` to compile) and `dave2`
(fast, just TensorFlow + Python 3.8).

**Update: verified working.** `tiny-cuda-nn`'s CUDA extension compile
succeeded cleanly against this image's CUDA 11.8 + gcc-11 setup — no
gcc/CUDA header mismatch ever showed up (unlike the native-install guide's
experience). The real issues hit during the actual build were unrelated:
Anaconda's Terms-of-Service gate blocking non-interactive `conda create`,
and `setuptools` 81+ removing `pkg_resources` (which `tiny-cuda-nn`'s
legacy `setup.py` still imports) combined with pip's build isolation
fetching its own unpinned setuptools regardless of what's installed in the
env. Both are already fixed in `docker/pipeline/Dockerfile` — see
"Fixes already baked in" below.

## Step 5. Download model weights and datasets

Doesn't need Docker or GPU — runs on the host with just `gdown`:

```sh
bash fetch_assets.sh --all
```

Downloads:
- FCOS3D + PointPillars → `2_process_datasets/utils/`
- DAVE-2 weights → `system_under_test/final.h5`
- Reference ROS bag → `data/raw_ros_data/reference_bag.bag`
- Validation data → `data/`

You can run this in parallel with Step 4's build to save time.

## Step 6. Start the core containers

```sh
docker compose up -d pipeline carla-server
```

Check CARLA came up cleanly:

```sh
docker compose logs -f carla-server
```

**Update: verified working.** You should see the Unreal Engine boot banner
(`4.26.2-0+++UE4+Release-4.26 ...`) followed by a quiet stretch while the
engine initializes — that's normal, not stuck. Confirm the RPC listener is
actually reachable from `pipeline`'s network namespace:

```sh
docker compose exec pipeline bash -lc "source /opt/conda/etc/profile.d/conda.sh && conda activate data_extraction && python -c \"import socket; s=socket.create_connection(('127.0.0.1',2000),5); s.close(); print('CARLA is up')\""
```

`CARLA is up` confirms GPU passthrough, the engine boot, and the RPC
listener are all working end to end.

## Step 7. Run the pipeline components

Following the paper's own terminology: the pipeline is organized into
five **components** — data extraction, dataset processing, simulation
data generation, Gaussian Splatting preparation, and driving simulation
(Section 2 of the paper). Each component is made up of individual
**modules** — the lettered scripts inside each folder (1A, 1B, 1C, etc.).
Validation (`6_validation/`) is included below as a sixth component for
practical purposes, but note it isn't one of the paper's formal five —
the paper covers it separately as "Preliminary Validation" (Section 3),
not as a numbered pipeline component.

Components 1–4 and Component 6 (validation) don't need CARLA running continuously —
exec directly into the `pipeline` container:

```sh
docker compose exec pipeline bash
conda activate data_extraction
```

### Component 1: Extract ROS Data (`1_extract_ROS_data`)

Modules 1A–1E, run in sequence by `step1.sh`:

```sh
bash 1_extract_ROS_data/step1.sh
```

#### Component 1 architecture / data flow

```
                         data/raw_ros_data/reference_bag.bag
                    (ROS bag: camera, lidar, /odom, steering topics)
                                       │
        ┌──────────────┬──────────────┼──────────────┬──────────────┐
        │              │              │              │              │
   /gmsl_camera/   /velodyne_    /odom         /vehicle/       /cmd/steering_
   front_narrow/    points     (9224 msgs)    steering_pct      target
   image_raw                                  (9205 msgs)      (912 msgs)
        │              │              │              │              │
        ▼              ▼              ▼              ▼              ▼
  ┌───────────┐  ┌───────────┐ ┌─────────────┐ ┌───────────┐ ┌───────────┐
  │ Module 1A │  │ Module 1B │ │  Module 1C  │ │ Module 1D │ │ Module 1E │
  │  camera_  │  │  lidar_   │ │  poses_and_ │ │ steering_ │ │  model_   │
  │  with_    │  │  with_    │ │  trajectory │ │  status   │ │  output   │
  │  odometry │  │  odometry │ │             │ │           │ │           │
  └─────┬─────┘  └─────┬─────┘ └──────┬──────┘ └─────┬─────┘ └─────┬─────┘
        │              │              │              │              │
   reads /odom    reads /odom    reads /odom     (no odom      (no odom
   too, then       too, then     directly,        needed)       needed)
   INTERPOLATES    INTERPOLATES  no interp -
   pose at each    pose at each  just sorted
   camera          lidar         + reformatted
   timestamp       timestamp
        │              │              │              │              │
        ▼              ▼              ▼              ▼              ▼
  images/         point_clouds/  odometry.csv   steering_pct.txt  steering_
  frame_*.png     point_cloud_   trajectory.csv                   predictions.txt
                  *.bin
        │              │
        ▼              ▼
  images_          lidar_
  positions.txt    positions.txt
  (interpolated    (interpolated
   pose per         pose per
   frame)           scan)

        └──────────────┴──────────────┴──────────────┴──────────────┘
                                       │
                                       ▼
                data/raw_dataset/reference_bag/
                ├── images/                   (from 1A)
                ├── images_positions.txt      (from 1A)
                ├── point_clouds/             (from 1B)
                ├── lidar_positions.txt       (from 1B)
                ├── odometry.csv              (from 1C)
                ├── trajectory.csv            (from 1C)
                ├── steering_pct.txt          (from 1D)
                └── steering_predictions.txt  (from 1E)
```

`/odom` is a shared input consumed by three modules (1A, 1B, 1C), used two
different ways — Modules 1A/1B interpolate it to match a *different*
sensor's timestamps, while Module 1C just passes it through sorted,
unmodified. The steering module (1D) and steering-target module (1E)
don't touch odometry at all; their outputs run at different sample rates
than the pose data and will need their own timestamp alignment if
compared against trajectory data later (e.g. in Component 6 validation).

#### Component 1 output files explained

| Output | Module | Format | What it contains |
|---|---|---|---|
| `images/frame_*.png` | 1A | PNG per frame | Raw RGB camera frames from `/gmsl_camera/front_narrow/image_raw`, decoded from whatever the bag's image encoding was (mono8/rgb8/bgr8/Bayer) into standard PNGs. |
| `images_positions.txt` | 1A | `FrameID, Timestamp_Sec, Odom_X, Odom_Y, Odom_Z, Qx, Qy, Qz, Qw, Odom_Yaw, ImageFile` | One row per camera frame — the vehicle's UTM pose at that *exact* frame timestamp, linearly interpolated from the surrounding `/odom` messages (quaternion components interpolated then re-normalized before yaw is computed). This is what lets later components know precisely where the camera was for each image. |
| `point_clouds/point_cloud_*.bin` | 1B | float32 binary, `x, y, z, intensity` per point | Raw LiDAR scans from `/velodyne_points`. Intensity is written as `0.0` if the message has no intensity field. |
| `lidar_positions.txt` | 1B | `FrameID, Timestamp_Sec, Odom_X, Odom_Y, Odom_Yaw, PointCloudFile` | Same interpolation idea as `images_positions.txt`, but one row per LiDAR scan instead of per camera frame — needed so Component 2's parked-vehicle detection can place detected objects in world coordinates. |
| `odometry.csv` | 1C | `timestamp, tx, ty, tz, qx, qy, qz, qw, yaw` | Every raw `/odom` message from the bag, sorted by timestamp, quaternion converted to yaw — **not interpolated**, just reformatted. This is the raw pose stream the other scripts interpolate *from*. |
| `trajectory.csv` | 1C | `timestamp, x, y, z, yaw` | Same data as `odometry.csv`, compact form without the raw quaternion — a simplified trajectory view. |
| `steering_pct.txt` | 1D | `timestamp, value` | The vehicle's actual recorded steering input from `/vehicle/steering_pct` — ground truth for later comparison against model predictions. |
| `steering_predictions.txt` | 1E | `timestamp, steering_target` | Steering *target*/model-output values from `/cmd/steering_target` — typically a much lower message rate than the sensor topics (e.g. ~912 vs. ~9200 messages), since this is usually a planning/prediction signal rather than raw sensor data. Compared against `steering_pct.txt` to evaluate driving behavior. |

### Component 2: Process Datasets (`2_process_datasets`)

```sh
bash 2_process_datasets/step2.sh
```

Modules 2A–2G, run in sequence by `step2.sh`. Detects parked vehicles
(FCOS3D + PointPillars), builds the OSM map, generates sky masks, and
produces the overlapping Gaussian Splatting image splits.

| Module | What it does | Console banner |
|---|---|---|
| 2A | Camera-based parked-vehicle detection (FCOS3D) | `UNIFIED PARKED CAR DETECTION PIPELINE` |
| 2B | LiDAR-based parked-vehicle detection (PointPillars) | `LIDAR PARKED CAR DETECTION PIPELINE` |
| 2C | OSM map generation for the recorded area | `MAP DATA GENERATION` |
| 2E | Image cropping, sky masks, GS training splits | `GAUSSIAN SPLATTING IMAGE PREPARATION (thesis-replicating, WITH sky masks)` |
| 2F | Semantic segmentation (SegFormer) for Component 6 validation | `REDUCED SEMANTIC MAP GENERATION` |
| 2G | Optional sidewalk fix on the generated map | *(none — just a one-line `sed` patch, no console output at all)* |

#### Component 2 architecture / data flow

```
        Component 1 outputs                    External services
   ┌───────────────┬──────────────┐          ┌─────────────────────┐
   │               │              │          │  OSM Overpass API   │
images/    point_clouds/   trajectory.csv     │  (reverse geocode +  │
images_    lidar_                             │   map data fetch)   │
positions  positions.txt                      └──────────┬──────────┘
  .txt                                                    │
   │               │              └───────────────┐       │
   ▼               ▼                               ▼       ▼
┌───────────┐ ┌───────────┐                    ┌─────────────┐
│    2A     │ │    2B     │                    │     2C      │
│  camera_  │ │  lidar_   │                    │  create_map_│
│  parked_  │ │  parked_  │                    │  from_      │
│  cars_    │ │  cars_    │                    │  coordinates│
│  detection│ │  detection│                    │  _auto      │
│ (FCOS3D)  │ │(PointPillars)                  │             │
└─────┬─────┘ └─────┬─────┘                    └──────┬──────┘
      │              │                                 │
      ▼              ▼                                 ▼
camera_          lidar_                          map.xodr, map.osm,
detections.json  detections.json,                vehicle_data.json
                 unified_clusters.txt             (placeholder hero)
                       │                                 │
                       │         ┌───────────────────────┘
                       │         │  (2G, optional)
                       │         ▼
                       │   sed-patch map.xodr
                       │   (sidewalk → parking)
                       │
   images/, images_positions.txt (from Component 1)
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   ┌───────────┐                ┌───────────┐
   │    2E     │                │    2F     │
   │  prepare_ │                │  extract_ │
   │  dataset_ │                │  semantic_│
   │  for_gs   │                │  maps     │
   │(SegFormer-│                │(SegFormer-│
   │    b1)    │                │    b5)    │
   └─────┬─────┘                └─────┬─────┘
         │                            │
         ▼                            ▼
   images_gs_split_*/            semantic_maps/
   sky_masks_gs_split_*/         (per-frame road/
   frame_positions_split_*.txt    car/background,
   (3 overlapping splits,         used by Component 6
    → feeds Component 4)          validation only)
```

`2A`/`2B` both consume Component 1's pose-interpolated sensor data but
detect independently (camera vs. LiDAR) — their outputs stay separate
(`camera_detections.json` vs. `lidar_detections.json`/
`unified_clusters.txt`); nothing in this component merges them. `2B`'s
`unified_clusters.txt` is what Component 3's `3B` later reads to place
parked vehicles in CARLA. `2C` is the only module that talks to the
network (OSM), and produces the map `3A`/`3B`/`3F` all depend on. `2E`'s
GS splits are Component 4's Phase 1 input; `2F`'s semantic maps aren't
used anywhere else in the pipeline except Component 6.

If Open3D complains about Wayland/GLEW:

```sh
export XDG_SESSION_TYPE=x11
export GDK_BACKEND=x11
bash 2_process_datasets/step2.sh
```

### Component 3: Generate Simulation Data (`3_generate_simulation_data`)

```sh
bash 3_generate_simulation_data/step3.sh   # 3C prints a harmless traceback (see "Fixes" below) — carla-server is already up
```

#### Component 3 architecture / data flow

```
  Component 1 output         Component 2 outputs           Live CARLA
  images_positions.txt   unified_clusters.txt   map.xodr   (carla-server,
        │                (from 2B)         (from 2C)        RPC :2000)
        │                       │                │                │
        ▼                       │                │                │
  ┌───────────┐                 │                │                │
  │    3A     │                 │                │                │
  │ transform_│                 │                │                │
  │coordinates│                 │                │                │
  │ _to_carla │                 │                │                │
  └─────┬─────┘                 │                │                │
        │                       ▼                ▼                │
        │                 ┌───────────────────────────┐            │
        │                 │            3B             │            │
        │                 │ transform_parked_vehicles_ │            │
        │                 │        to_carla            │            │
        │                 └─────────────┬─────────────┘            │
        ▼                               ▼                          │
  trajectory_positions*.json     vehicle_data.json                 │
  (UTM → CARLA coordinate            (updated with                 │
   conversion, 4 variants:           86 parked-car                 │
   center/rear × raw/odom-yaw)       spawn_positions)               │
        │                               │                          │
        │          3C (no-op in Docker — see "Fixes")               │
        │                               │                          │
        └───────────────┬───────────────┘                          │
                         ▼                                         │
                   ┌───────────┐                                   │
                   │    3F     │◄──────────────────────────────────┘
                   │ generate_ │      connects live, spawns
                   │  carla_   │      hero + parked vehicles
                   │ scenario  │      into the running world
                   └─────┬─────┘
                         ▼
              Live CARLA world state
              (hero + 84-86 parked cars,
               actors left alive —
               no file output, this
               is what Component 5
               replays against)
```

`3A` and `3B` both read `map.xodr` (for the XODR/UTM projection
parameters) but transform different things — `3A` the ego trajectory,
`3B` the parked-vehicle centroids — and both write into the same
`vehicle_data.json`, which accumulates fields across the whole component
(hero pose from `3A`, `spawn_positions` from `3B`). `3F` is the only
module that actually talks to CARLA over the network; everything before
it is pure coordinate-transformation math with no simulator connection
needed.

### Component 4: Gaussian Splatting Preparation (`4_gaussian_splatting_preparation`)

**Phase 1 (COLMAP reconstruction, once per split)** — the project's own
docs describe this as GUI-only, but that's just how the authors happened
to do it, not a real requirement. COLMAP has a full CLI that does the
same steps non-interactively. **`4A_CLI_colmap_reconstruction.sh`**
(added to this project, not part of the original repo — place it in
`4_gaussian_splatting_preparation/`) automates all 3 splits with the
same settings as the manual procedure below, and is confirmed working
end-to-end. Recommended over the manual GUI steps unless you have a
specific reason to inspect/tune the reconstruction interactively.

```sh
docker compose exec pipeline bash
conda activate data_extraction
bash 4_gaussian_splatting_preparation/4A_CLI_colmap_reconstruction.sh
```

- **Idempotent by file existence, not freshness** — skips any split that
  already has a complete `cameras.bin`/`images.bin`/`points3D.bin`,
  regardless of how old those files are. If you're not sure whether
  existing files are genuinely from this bag/setup (e.g. leftover from an
  unrelated earlier session — this happened once already, see
  "Fixes"/stale-data notes elsewhere in this guide), check timestamps
  first:
  ```sh
  stat -c '%y  %n' data/data_for_gaussian_splatting/reference_bag/colmap/split_1/sparse/0/*.bin
  ```
  Pass `--force` to redo every split regardless of what's already there,
  or rename/move the whole `colmap/` folder aside first if you'd rather
  keep old data than overwrite it:
  ```sh
  mv data/data_for_gaussian_splatting/reference_bag/colmap \
     data/data_for_gaussian_splatting/reference_bag/colmap_stale_<date>
  ```
- **Cosmetic `libGL`/MESA errors are expected and harmless** — COLMAP's
  feature extractor tries to initialize a GPU/GL context for SIFT and
  falls back to CPU in this headless container setup. Doesn't block
  anything, just means feature extraction is somewhat slower than it
  would be with working GPU-accelerated SIFT.
- **What to watch for**: feature extraction should report a reasonable,
  consistent feature count per frame (thousands, not near-zero) with no
  processing errors; the `mapper` stage (reconstruction) should register
  close to the full image count, not just a handful — a low registration
  count signals a bad reconstruction worth investigating (wrong
  intrinsics, insufficient overlap, blurry/dark frames) before trusting
  it into Phase 2.

Camera intrinsics (`fx, fy, cx, cy, k1, k2, p1, p2`) for `reference_bag`'s
front narrow camera, confirmed directly from this project's own
`4_gaussian_splatting_preparation/4A_colmap_guide.md`:
```
785.34926249, 784.07587341, 406.50794975, 249.45341029, -0.42020115, 0.64296938, -0.00531934, -0.00215015
```
(A different set of numbers appears elsewhere in the project's docs —
this is the one confirmed from the actual guide file, use this one.)

At the end of Phase 1, you should have all three:
```
data/data_for_gaussian_splatting/reference_bag/colmap/
├── split_1/sparse/0/{cameras,images,points3D}.bin
├── split_2/sparse/0/{cameras,images,points3D}.bin
└── split_3/sparse/0/{cameras,images,points3D}.bin
```

<details>
<summary>Manual GUI alternative (click to expand)</summary>

Since this needs a GUI, it also needs X11 forwarding into the `pipeline`
container (already set up via the `DISPLAY`/X11 socket mount in
`docker-compose.yml`):

```sh
docker compose exec pipeline bash
conda activate data_extraction
colmap gui
```

You must run this full procedure **once per split** (3 splits for
`reference_bag` by default). Repeat steps 1-6 below for each one before
moving to Phase 2.

1. **New project.** `File → New Project`. Create a new database per split,
   e.g. `data/data_for_gaussian_splatting/reference_bag/colmap/database_split_1.db`.
   Select that split's image folder as the image path, e.g.
   `data/data_for_gaussian_splatting/reference_bag/images_gs_split_1_1_of_2`.
2. **Feature extraction.** `Processing → Feature Extraction`. Camera model
   `OPENCV`, enable `Single camera`. Set the camera intrinsics to the
   confirmed values above. Optionally select that split's sky-mask folder
   too, e.g.
   `data/data_for_gaussian_splatting/reference_bag/sky_masks_gs_split_1_1_of_2`.
   Run extraction.
3. **Feature matching.** `Processing → Feature Matching`. Select
   `Sequential matching`, set `Sequential overlap` to `10`. Run matching.
4. **Reconstruction options.** `Reconstruction → Reconstruction Options` →
   `Bundle Adjustment`. Uncheck `Refine focal length`, `Refine principal
   point`, and `Refine extra parameters` — keeps the calibrated intrinsics
   fixed rather than letting COLMAP re-estimate them.
5. **Start reconstruction.** `Reconstruction → Start Reconstruction`. Wait
   for it to finish.
6. **Export the sparse model** into the matching split folder:
   `data/data_for_gaussian_splatting/reference_bag/colmap/split_<N>/sparse/0/`
   — must contain `cameras.bin`, `images.bin`, `points3D.bin`.

</details>


If you used the manual GUI path, **close the COLMAP window** before
continuing — Phase 2 only reads the saved `.bin`/`.db` files from disk, it
doesn't need COLMAP itself running. Sequential, not concurrent either way:
finish Phase 1 entirely (script or GUI) before starting Phase 2 below.

**Phase 2 (automatic Splatfacto training)** — new shell/session, after
Phase 1 is fully done:

```sh
docker compose exec pipeline bash
conda activate nerfstudio
bash 4_gaussian_splatting_preparation/4B_train_gaussian_splatting.sh
```

#### Component 4 architecture / data flow

```
        Component 2 (2E) output, per split (1, 2, 3)
   images_gs_split_N/    sky_masks_gs_split_N/
        │                       │
        ▼                       ▼
  ┌─────────────────────────────────────┐
  │       Phase 1: COLMAP (per split)     │
  │  feature_extractor → sequential_      │
  │  matcher → mapper (bundle adjustment) │
  │  (4A_CLI_colmap_reconstruction.sh,    │
  │   or manual GUI per 4A_colmap_guide)  │
  └───────────────────┬───────────────────┘
                       ▼
        colmap/split_N/sparse/0/
        {cameras,images,points3D}.bin
                       │
                       ▼
  ┌─────────────────────────────────────┐
  │     Phase 2: 4B_train_gaussian_       │
  │            splatting.sh (per split)   │
  │  ns-train splatfacto  →  4C_utm_yaw_  │
  │  (COLMAP poses          to_nerfstudio │
  │   + images + masks)     (alignment)   │
  └───────────────────┬───────────────────┘
                       ▼
   outputs/splatfacto_split_N/splatfacto/<timestamp>/
   ├── config.yml, checkpoint (trained GS model)
   └── utm_to_nerfstudio_transform.json
       (UTM ↔ Nerfstudio coordinate alignment —
        this is what lets Component 5 render GS
        images from a CARLA camera pose)
```

Fully independent per split — three separate COLMAP reconstructions,
three separate Splatfacto training runs, no data shared between splits
until Component 5 picks whichever split's model covers the trajectory
segment being replayed. The `utm_to_nerfstudio_transform.json` alignment
step is what actually connects this component back to the rest of the
pipeline: without it, the trained GS model exists in an arbitrary
Nerfstudio-internal coordinate frame with no relationship to CARLA/UTM
world coordinates.

## Step 8. Run Component 5: Driving Simulation (`5_execute_simulation`)

Use `scripts/run_step5.sh` — the container-native replacement for the
original `gnome-terminal`-based `step5.sh`. It calls `3F_generate_carla_scenario.py`
directly (never runs `step3.sh` or `3C_setup_carla.py` at all) to load the
map and spawn hero + parked vehicles fresh before every mode, and starts
`dave2-server` automatically when the mode needs it:

```sh
bash scripts/run_step5.sh --mode 5A   # start here — simplest, no GS, no DAVE-2
bash scripts/run_step5.sh --mode 5C   # + Gaussian Splatting replay (live window)
bash scripts/run_step5.sh --mode 5B   # CARLA-only + DAVE-2 closed loop
bash scripts/run_step5.sh --mode 5D   # + Gaussian Splatting + DAVE-2 closed loop
```

Recommended order: **5A first** (validates CARLA + scenario setup alone),
**then 5C** (adds Gaussian Splatting rendering), **then 5B/5D** (adds
DAVE-2) — this isolates which layer is responsible if something breaks,
rather than debugging all three at once. This order also respects the
RTX 2080's 8GB VRAM limit: 5D is the tightest case (CARLA + GS rendering +
DAVE-2 inference concurrently), so confirming the pieces work individually
first reduces guesswork if you hit CUDA OOM there. Confirmed working
end-to-end in this order on real hardware — see notes below each mode.

Every mode re-runs `3F_generate_carla_scenario.py` fresh at startup
(spawns hero + parked cars again, even if a previous mode already did) —
this is `run_step5.sh`'s own behavior, not something inherited from
`step3.sh`.

Output paths differ per mode — this is the actual layout, not a shared
folder:

```text
# 5A
data/processed_dataset/reference_bag/carla_replay_dataset/
├── rgb/
├── semantic/
├── instance/
└── data/all_frame_data.json

# 5B
data/processed_dataset/reference_bag/dave2_runs/only_carla_run<N>/
├── rgb/
├── semantic/
├── instance/
├── depth/
└── data/trajectory.json

# 5C
data/data_for_carla/reference_bag/replay_results/reference_bag_replay/
├── carla/
├── gs/
└── combined/

# 5D
data/results/splatfacto_run<N>/
├── rgb_gt/         (currently disabled — see below)
├── generated_gs/   (currently disabled — see below)
└── trajectory.json
```

**Known bug, confirmed on real hardware: `5C`'s frame-saving code is
commented out in the shipped script.** `carla/`, `gs/`, `combined/` get
created (empty) but nothing writes into them — the actual `.save()` calls
around line 1208-1215 of `5C_trajectory_replay.py` are commented out,
likely a leftover from a performance test that never got reverted. Fix by
uncommenting those exact lines (verify with `cat -n` first — the comment
indentation isn't perfectly uniform, so a blind `sed` pattern-match can
leave a broken mix of commented/uncommented lines; replacing each line by
exact line number is safer):
```sh
cat -n 5_execute_simulation/5C_trajectory_replay.py | sed -n '1205,1216p'
```
Expected clean result once fixed:
```python
            if save_flag:
                carla_pil.save(os.path.join(
                    save_dir_carla, f"frame_{frame_id:06d}.png"))
                if gs_pil:
                    gs_pil.save(os.path.join(
                        save_dir_gs, f"frame_{frame_id:06d}.png"))
                combined.save(os.path.join(
                    save_dir_combined, f"frame_{frame_id:06d}.jpg"), quality=95)
```
Verify with `python3 -m py_compile 5_execute_simulation/5C_trajectory_replay.py`
before re-running. `5D`'s `rgb_gt`/`generated_gs` outputs are *documented*
as disabled (not a bug to fix, per the README) — only `trajectory.json` is
expected from `5D`.

**`5B` stopping early with `[ERROR] Car appears stuck. Stopping.` is
expected, not a bug.** This reproduces the paper's own documented finding
— Table 1 reports the CARLA-only baseline's completion rate as 21-23-20%
across their 3 runs, versus 100-100-100% for both the real-world drives
and the GS-augmented (`5D`) runs. DAVE-2 was trained on real camera
footage; raw CARLA rendering's visual gap from that is severe enough that
it reliably drives into something early. Getting stuck early on `5B` is
the entire motivating problem the paper's GS approach exists to solve —
the real test is whether `5D` completes where `5B` didn't.

#### Component 5 architecture / data flow

```
              Component 3 output              Component 4 output
     map.xodr, vehicle_data.json      outputs/splatfacto_split_N/...
     trajectory_positions*.json       (trained GS model + UTM↔NS
              │                        alignment transform)
              │                                   │
              ▼                                   │
        ┌───────────┐                             │
        │    3F     │   (run_step5.sh calls this   │
        │ (re-run   │    directly — never touches   │
        │  fresh)   │    step3.sh or 3C)             │
        └─────┬─────┘                             │
              │                                   │
              ▼                                   │
      Live CARLA world                            │
      (hero + parked cars                         │
       spawned, static)                           │
              │                                   │
   ┌──────────┴──────────┐                        │
   ▼                      ▼                        │
┌──────┐            ┌──────────┐                   │
│  5A  │            │    5C    │◄──────────────────┘
│traj- │            │trajectory│   loads matching split's
│only_ │            │_replay   │   GS model for each frame,
│carla │            │          │   renders alongside CARLA
└──┬───┘            └────┬─────┘
   │                     │
   │              ┌──────┴──────┐
   │              ▼              ▼
   │         carla/          gs/            + combined/
   │         (raw CARLA      (GS-rendered    (side-by-side)
   │          frames)         frames)
   │                     │
   ▼                     │
carla_replay_dataset/     │
(rgb/semantic/instance/    │
 + all_frame_data.json)    │
                            │
        ┌───────────────────┴───────────────────┐
        │            DAVE-2 server                │
        │   (system_under_test/communicator.py,    │
        │    separate container, :5090)            │
        └───────────────────┬───────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                              ▼
           ┌──────┐                      ┌──────┐
           │  5B  │                      │  5D  │
           │ DAVE-2│                     │ DAVE-2│
           │ + raw │                     │ + GS  │
           │ CARLA │                     │ frames│
           └───┬───┘                     └───┬───┘
               ▼                              ▼
      dave2_runs/only_carla_run<N>/    results/splatfacto_run<N>/
      (rgb/semantic/instance/depth      trajectory.json
       + trajectory.json)               (closed-loop,
      (closed-loop, simulation-only      the paper's main
       baseline — expected to stop       experiment — should
       early, "Car appears stuck")       complete all frames)
```

`5A`/`5C` are trajectory-**replay** modes — the recorded human driving is
played back frame-by-frame regardless of what any model predicts. `5B`/
`5D` are **closed-loop** modes — DAVE-2 actually controls steering based
on what it sees (raw CARLA pixels for `5B`, GS-rendered pixels for `5D`),
so the resulting trajectory can diverge from the original recording. This
is the comparison the paper's evaluation is built on: `5B` (simulation-only
baseline) vs. `5D` (GS-augmented) vs. the real-world recording, per
Section 3 of the paper.

**Confirmed on real hardware (`lambda-11037`, RTX 2080):** `5A` completed
all 3,144 frames cleanly. `5C` completed all 3,144 frames with correct
split-switching at both boundaries (frame 1147: split_1→split_2, frame
2195: split_2→split_3) once the save-code bug above was fixed. `5B`
stopped early (~frame 389) with the expected "Car appears stuck" —
consistent with the paper's own 21-23-20% completion rate for this mode.
`5D` was still running at last check; if it completes the full trajectory
where `5B` didn't, that's a genuine end-to-end reproduction of the paper's
core result using this Docker setup.

## Fixes already baked into `docker/pipeline/Dockerfile`

- **`liblzfse` (built from source) + `python3.10-dev`** — `liblzfse-dev`
  isn't packaged for Ubuntu 22.04 (jammy), the container's base — it only
  exists starting Ubuntu 24.04 (this was caught during the actual build,
  not anticipated in advance). Built from source instead (`cmake && make
  install` to `/usr/local`) right after the apt install, so
  `pyliblzfse`/`fpsample` (pulled in by `data_extraction_requirements.txt`)
  can compile against it.
- **`weights_only=False` patch** applied to Nerfstudio's
  `eval_load_checkpoint()` at build time — defends against a black GS
  panel / `Split: none` if a transitive dependency ever bumps torch past
  the version where `torch.load()`'s default changed. The pinned
  `torch==2.1.0+cu118` predates that change, so this is precautionary,
  not currently expected to trigger.
- **gcc-11/g++-11** set as the active compiler for `tiny-cuda-nn`/`gsplat`
  compilation — CUDA 11.8's `nvcc` rejects newer gcc versions. Confirmed
  working: no header conflicts, unlike the native-install guide's
  experience on bare metal.
- **`conda tos accept`** run right after the Miniconda install — Anaconda
  now requires explicit Terms-of-Service acceptance for the default
  channels before any non-interactive `conda create`/`conda install` will
  run; without this, builds fail with `CondaToSNonInteractiveError`.
- **`setuptools<81` + `--no-build-isolation`** for the `tiny-cuda-nn`
  install — setuptools 81+ (Feb 2026) removed `pkg_resources` entirely,
  which `tiny-cuda-nn`'s legacy `setup.py` still imports directly. Pinning
  setuptools in the env alone isn't enough, since pip's build isolation
  creates a separate temporary venv that fetches its own (unpinned, newer)
  setuptools; `--no-build-isolation` forces it to use the env's pinned
  version instead.
- **Multi-architecture CUDA builds for `mmcv`/`mmdet3d`/`tiny-cuda-nn`** —
  found when testing on a second workstation with an H100 (Hopper,
  compute capability 9.0): `mim install mmcv==2.1.0` pulls a prebuilt
  wheel with kernels only for whatever architectures OpenMMLab targeted at
  build time, and the original `TCNN_CUDA_ARCHITECTURES=75` was hardcoded
  for the RTX 2080 alone. Both failed with `CUDA error: no kernel image is
  available for execution on the device` on the H100. Fixed by building
  `mmcv`/`mmdet3d` from source with `TORCH_CUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0+PTX"`
  and widening `TCNN_CUDA_ARCHITECTURES` to `"75;80;86;89;90"` — covers
  Turing through Hopper in one image, at the cost of a noticeably longer
  build (compiling every op for every listed architecture instead of
  pulling a wheel). Extend either list if you test on a GPU generation not
  yet covered.
- **`setuptools<81` for `mmcv`/`mmdet3d` too, via a global `PIP_CONSTRAINT`
  — not just `--no-build-isolation`.** Forcing `mmcv`/`mmdet3d` to build
  from source (above) means they hit the exact same `pkg_resources`-
  removal issue already fixed for `tiny-cuda-nn`. The `--no-build-
  isolation` approach that fixed `tiny-cuda-nn`, though, turned out
  *not* to reliably fix these two — pip still spun up an isolated build
  environment anyway (visible as a `/tmp/pip-build-env-.../overlay/...`
  path in the traceback) that fetched its own fresh, unpinned setuptools
  regardless of the flag. The actual fix: a global `PIP_CONSTRAINT` file
  (`setuptools<81`) set once, early in the Dockerfile, before either conda
  env is created. Pip explicitly documents that constraints files apply
  even to a package's *build* dependencies inside an isolated build
  environment — more reliable than per-install flags, and it covers every
  pip invocation for the rest of the build automatically.
- **`matplotlib.use("TkAgg")` crashing `2C_create_map_from_coordinates_auto.py`
  outright** (not just an optional visualization step, unlike 2B's crash)
  when no real interactive display is reachable — e.g. connecting over
  `ssh -X`, where `DISPLAY` is set but points to a TCP-forwarded proxy on
  the *host's* loopback, unreachable from the container's own network
  namespace. **Not baked into the image** — `2_process_datasets/utils/plotting.py`
  is project source bind-mounted at runtime (`docker-compose.yml`'s
  `.:/workspace/cam2sim`), not part of the built image, so a Dockerfile
  patch would just get silently overwritten by the mounted host files.
  If you hit this, patch it manually once per checkout:
  ```sh
  sed -i 's/TkAgg/Agg/' 2_process_datasets/utils/plotting.py
  ```
  Forces the headless `Agg` backend instead of the interactive `TkAgg`
  one — safe for an automated pipeline that doesn't need a real GUI
  window; any `plt.show()` calls become harmless no-ops.
- **`carla==0.9.16` needed to match in *both* places `carla` gets
  installed, not just the `nerfstudio` env's pip install.** **Not a
  Dockerfile fix** — same situation as the `matplotlib` fix above, but
  this one's a one-line pin in a file the pipeline itself owns.
  `data_extraction_requirements.txt` (from the original repo) pins its own
  `carla==0.9.15` directly — completely independent of the version set
  later in `nerfstudio`. Since Component 3's scripts
  (`3A`/`3B`/`3C`/`3F`) all run under `data_extraction`, they were
  silently using that env's 0.9.15 client against the 0.9.16
  `carla-server` container, causing a version-mismatch crash
  (`std::bad_alloc`) at the `3F` scenario-setup step. This looked at first
  like a stale-image problem (a rebuild not having taken effect, the same
  class of issue as the earlier `mmcv`/H100 case) and cost real time
  chasing that before the actual cause — a second, independent `carla`
  pin — was found. **Fix: edit `data_extraction_requirements.txt` directly**
  and change `carla==0.9.15` to `carla==0.9.16` — one source of truth for
  the pinned version instead of two places that can drift out of sync.
- **`3C_setup_carla.py` crashing with a hardcoded, author-specific path**
  (`/media/davidejannussi/ssd space/davide/CARLA_0.9.15` — one of the
  paper's authors' own local dev machine) every time Component 3 runs.
  **Not actually blocking** — `step3.sh` launches `3C` as a *background*
  job (`python3 "$SCRIPT" &`) and never checks its exit code, so the
  traceback prints but the rest of the script (`3A`/`3B`/`3F`) continues
  normally regardless. Still worth removing the noise: `3C`'s whole job is
  launching a local CARLA install, which we never need since
  `carla-server` is already running as its own container. **Not a
  Dockerfile fix** — same as the other two fixes above, `step3.sh` is
  project source, bind-mounted at runtime. Remove the `3C_setup_carla.py`
  entry from `step3.sh`'s `SCRIPTS` array (the surrounding `if`/`else`
  background-launch logic can stay — it just never triggers once `3C`
  isn't in the array):
  ```sh
  sed -i '/^\s*"3_generate_simulation_data\/3C_setup_carla\.py"\s*$/d' 3_generate_simulation_data/step3.sh
  ```

## Known hardware limitation: Blackwell GPUs are not supported

**RTX 5090 and RTX PRO 6000 Blackwell (both compute capability 12.0,
`sm_120`) cannot run this image at all — this is a hard boundary, not
something fixable by widening the architecture lists above.**

This is a different class of problem than the H100 fix:

- The H100 case was a *missing kernel* — CUDA 11.8 supports Hopper
  (compute capability 9.0) at the toolchain level, the prebuilt `mmcv`
  wheel just didn't happen to include those kernels. Building from source
  with the right architecture flag fixed it.
- Blackwell is an *unknown architecture* to CUDA 11.8 — `nvcc` 11.8
  predates Blackwell's instruction set entirely and has no concept of
  `sm_120` as a valid compile target. No flag or source rebuild changes
  that; the compiler itself doesn't recognize the architecture.
- It goes deeper than our custom-compiled ops, too: the pinned
  `torch==2.1.0+cu118` (2023-era) wouldn't have Blackwell-compatible
  precompiled kernels for its own core ops either — even PyTorch builds as
  of early 2026 were still generally limited to sm_90 (Hopper) and below.

**What this means practically:** use the RTX 2080 / H100 workstations for
this pipeline as-is. Getting it running on the RTX 5090 / RTX PRO 6000
Blackwell machines isn't a Dockerfile patch — it's the "upgrade the
pinned dependency stack" work from your original modernization plan
(newer CUDA toolkit, newer torch, and everything downstream of those —
`nerfstudio`, `gsplat`, `mmcv`/`mmdet3d` — rebuilt against them), pulled
forward and scoped specifically to Blackwell support. Worth treating as
its own task once the as-is pipeline is fully validated on the GPUs that
do work, rather than something to squeeze in now.

## Things not yet verified — check these on first run

| Item | Where it might break | What to do if it does |
|---|---|---|
| CARLA bumped to 0.9.16 (repo's own tested pin is 0.9.15) — client/server version now match in both envs, but 0.9.16 itself is still a deviation from the repo's tested version | Component 3/Component 5 scenario setup, replay/closed-loop scripts | Watch for API-shape or map-loading differences; if something breaks that didn't in the repo's own testing, try reverting `docker-compose.yml`'s image tag and both Dockerfile `carla==` pins (`data_extraction` and `nerfstudio`) back to `0.9.15` together |
| Multi-architecture `mmcv`/`mmdet3d`/`tiny-cuda-nn` build actually fixes the H100 case | Component 2 (2A/2B), Component 4 (`gsplat` training) on the H100 workstation | Was diagnosed and fixed based on the error signature, but the rebuilt image hasn't been re-tested end-to-end on the H100 yet — re-run Component 2 there once rebuilt and confirm 2A/2B complete without the "no kernel image" error |
| `communicator.py` bind address reachable at `127.0.0.1:5090` from the shared namespace | `run_step5.sh` modes 5B/5D hang waiting for DAVE-2 | Check `system_under_test/communicator.py`'s bind host |
| VRAM headroom on the RTX 2080 (8GB) for mode 5D | CUDA OOM during closed-loop DAVE-2 + GS rendering | Lower CARLA's `-quality-level` (already `Low`), reduce pygame window resolution in `5D_dave2.py`, or force `dave2-server` to CPU with `CUDA_VISIBLE_DEVICES=""` |
| Intermittent DNS resolution failures inside the `pipeline` container | Any module making an outbound HTTPS call mid-run (hit during Component 2's OSM reverse-geocoding and a Hugging Face model download) | So far confirmed as host-side connectivity drops, not a Docker networking issue — just retry the failed module once connectivity is back. If it keeps recurring, consider adding explicit `dns:` servers to the `pipeline` service in `docker-compose.yml` |
| Resumable modules (e.g. `2F_extract_semantic_maps.py`) silently skipping stale leftover output from unrelated earlier runs | Any module documented as "resumable" that checks for existing output files before processing | Compare `stat -c '%y'` timestamps between input and output files if a run finishes suspiciously fast; delete the stale output folder and re-run if timestamps don't line up |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker run --gpus all ... nvidia-smi` fails | NVIDIA Container Toolkit not installed/configured correctly — redo Step 1 |
| pygame window doesn't appear | Re-run `xhost +local:docker` (Step 2) — it doesn't persist across reboots/logins |
| `carla-server` container exits immediately | Check `docker compose logs carla-server` — if `CarlaUE4.sh` isn't found, `find / -iname CarlaUE4.sh` inside the container and update `command:` |
| `gsplat`/`tiny-cuda-nn` fail to build | Shouldn't happen — confirmed working in this image. If it does anyway (e.g. after a dependency version bump), check for a gcc/CUDA header mismatch first, then check whether pip's build isolation is fetching an incompatible `setuptools` again |
| `CUDA error: no kernel image is available for execution on the device` | GPU architecture not covered by `TORCH_CUDA_ARCH_LIST`/`TCNN_CUDA_ARCHITECTURES` in the Dockerfile — check `nvidia-smi` for the GPU model, look up its compute capability, and add it to both lists if missing, then rebuild |
| `pyliblzfse`/`fpsample` build errors, or `liblzfse.h`/`liblzfse.a` not found | Shouldn't happen — `lzfse` is built from source and installed to `/usr/local` in the Dockerfile. If it does, check the `git clone`/`cmake`/`make install` step's output in the build log for a network or compile failure |
| Black GS panel, `Split: none` | Shouldn't happen given the pinned torch version, but the `weights_only=False` patch is already applied defensively — check it actually landed with `docker compose exec pipeline grep -n weights_only <path-to-eval_utils.py>` |
| CUDA OOM during 5D | See VRAM row in the table above |

## Next steps

Once the full pipeline runs end-to-end on `reference_bag`: run it against
your own ROS bag (needs a matching `camera.json`), and — per your original
plan — upgrade individual components one at a time (torch/nerfstudio
first, then CARLA, then mmdet3d/mmcv, then TensorFlow/DAVE-2 last) once
the as-is pipeline is confirmed working. Docker actually makes this phase
easier too: each component upgrade can be tested by rebuilding just the
one affected image/service rather than touching a shared host environment.

**If you bump the CARLA version again later:** the current `command:` for
`carla-server` in `docker-compose.yml` is specific to 0.9.16's documented
layout. A future version might change it again, the same way 0.9.16
changed it from 0.9.15. If `carla-server` fails to start after a version
bump:

```sh
docker compose run --rm carla-server bash
find / -iname "CarlaUE4.sh" 2>/dev/null
```

then update the `command:` line to match whatever you find, and re-check
CARLA's own Docker docs for that version:
<https://carla.readthedocs.io/en/latest/build_docker/>.
