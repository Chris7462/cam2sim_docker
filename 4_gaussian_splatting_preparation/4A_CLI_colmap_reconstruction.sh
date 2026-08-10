#!/usr/bin/env bash
#
# 4A_CLI_colmap_reconstruction.sh
#
# Non-interactive alternative to the manual COLMAP GUI procedure in
# 4A_colmap_guide.md. Runs the same steps (feature extraction, sequential
# matching, mapping/reconstruction) via COLMAP's CLI, with the same
# settings the GUI guide specifies for reference_bag's front narrow
# camera. Produces the exact same output layout Phase 2
# (4B_train_gaussian_splatting.sh) expects.
#
# Run with the data_extraction conda env active (same env the GUI guide
# uses), from the project root:
#   conda activate data_extraction
#   bash 4_gaussian_splatting_preparation/4A_CLI_colmap_reconstruction.sh
#
# Idempotent: skips any split that already has a complete sparse model
# (cameras.bin + images.bin + points3D.bin). Use --force to redo
# everything regardless.
#
set -euo pipefail

# -----------------------------------------------------------------------------
# Configuration - must match 2E_prepare_dataset_for_gaussian_splatting.py's
# settings (same requirement as 4B_train_gaussian_splatting.sh's config).
# -----------------------------------------------------------------------------
BAG_NAME="reference_bag"
NUM_SPLITS=3
FRAME_SKIP=2

# Front narrow camera intrinsics for reference_bag, from
# 4_gaussian_splatting_preparation/4A_colmap_guide.md:
#   fx, fy, cx, cy, k1, k2, p1, p2
# If you're running this against your own ROS bag instead, replace this
# with your own camera's calibration (see 4A_colmap_guide.md's notes on
# reading K/distortion from your data/data_for_carla/<bag>/camera.json).
CAMERA_MODEL="OPENCV"
CAMERA_PARAMS="785.34926249,784.07587341,406.50794975,249.45341029,-0.42020115,0.64296938,-0.00531934,-0.00215015"

SEQUENTIAL_OVERLAP=10

DATA_ROOT="data/data_for_gaussian_splatting/${BAG_NAME}"
COLMAP_ROOT="${DATA_ROOT}/colmap"

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

log() { echo "[4A_CLI] $*"; }

for N in $(seq 1 "$NUM_SPLITS"); do
    IMAGE_DIR="${DATA_ROOT}/images_gs_split_${N}_1_of_${FRAME_SKIP}"
    MASK_DIR="${DATA_ROOT}/sky_masks_gs_split_${N}_1_of_${FRAME_SKIP}"
    SPLIT_DIR="${COLMAP_ROOT}/split_${N}"
    DB_PATH="${SPLIT_DIR}/db.db"
    SPARSE_DIR="${SPLIT_DIR}/sparse"
    EXPECTED_MODEL="${SPARSE_DIR}/0"

    log "== Split ${N}/${NUM_SPLITS} =="

    if [[ ! -d "$IMAGE_DIR" ]]; then
        log "SKIP  image folder not found: $IMAGE_DIR (did Component 2's 2E step run with NUM_SPLITS=${NUM_SPLITS}, FRAME_SKIP=${FRAME_SKIP}?)"
        continue
    fi

    if [[ "$FORCE" -eq 0 ]] && [[ -f "${EXPECTED_MODEL}/cameras.bin" ]] \
       && [[ -f "${EXPECTED_MODEL}/images.bin" ]] \
       && [[ -f "${EXPECTED_MODEL}/points3D.bin" ]]; then
        log "SKIP  split ${N} already has a complete sparse model at ${EXPECTED_MODEL} (use --force to redo)"
        continue
    fi

    log "Resetting split ${N} output (removing any stale/partial data)..."
    rm -rf "$SPLIT_DIR"
    mkdir -p "$SPLIT_DIR"

    MASK_ARGS=()
    if [[ -d "$MASK_DIR" ]]; then
        MASK_ARGS=(--ImageReader.mask_path "$MASK_DIR")
    else
        log "NOTE  no sky-mask folder found at $MASK_DIR - continuing without masks"
    fi

    log "Feature extraction..."
    colmap feature_extractor \
        --database_path "$DB_PATH" \
        --image_path "$IMAGE_DIR" \
        --ImageReader.camera_model "$CAMERA_MODEL" \
        --ImageReader.single_camera 1 \
        --ImageReader.camera_params "$CAMERA_PARAMS" \
        "${MASK_ARGS[@]}"

    log "Sequential matching (overlap=${SEQUENTIAL_OVERLAP})..."
    colmap sequential_matcher \
        --database_path "$DB_PATH" \
        --SequentialMatching.overlap "$SEQUENTIAL_OVERLAP"

    log "Reconstruction (intrinsics refinement disabled)..."
    mkdir -p "$SPARSE_DIR"
    colmap mapper \
        --database_path "$DB_PATH" \
        --image_path "$IMAGE_DIR" \
        --output_path "$SPARSE_DIR" \
        --Mapper.ba_refine_focal_length 0 \
        --Mapper.ba_refine_principal_point 0 \
        --Mapper.ba_refine_extra_params 0

    if [[ -f "${EXPECTED_MODEL}/cameras.bin" ]] \
       && [[ -f "${EXPECTED_MODEL}/images.bin" ]] \
       && [[ -f "${EXPECTED_MODEL}/points3D.bin" ]]; then
        log "OK    split ${N} reconstructed successfully -> ${EXPECTED_MODEL}"
    else
        log "WARN  split ${N} finished but expected output files are missing at ${EXPECTED_MODEL} - check colmap's own output above for errors (e.g. insufficient feature matches)"
    fi
done

log "Done."
log "Verify all splits before moving to Phase 2:"
log "  find ${COLMAP_ROOT} -name '*.bin'"
