#!/usr/bin/env bash
#
# fetch_assets.sh — Downloads model weights and datasets required by the
# Cam2Sim pipeline. Run this from the project root (cam2sim/).
#
# Usage:
#   bash fetch_assets.sh [OPTIONS]
#
# Options:
#   --weights           FCOS3D + PointPillars + DAVE-2 checkpoints (~1 GB)
#   --raw-bag           Reference ROS bag for Step 1 (~6 GB)
#   --validation-data   Precomputed validation bundle for Step 6 (~size varies)
#   --quickstart        Precomputed data.zip that skips Steps 1-4 entirely
#                        (CARLA scenario + trained Gaussian Splatting models)
#   --all               Everything except --quickstart (default if no flags given)
#   --force             Re-download even if the target file already exists
#   -h, --help          Show this help
#
# Examples:
#   bash fetch_assets.sh --all
#   bash fetch_assets.sh --weights --raw-bag
#   bash fetch_assets.sh --quickstart
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Config: gdrive file IDs, from the project README
# ----------------------------------------------------------------------------
FCOS3D_ID="1JIKRFQQI9CmQARk21Q619TPkdS49Voel"
FCOS3D_OUT="2_process_datasets/utils/fcos3d.pth"

POINTPILLARS_ID="1AGOR8C0tDUsWSSWTEc0fA7kysIE9-iol"
POINTPILLARS_OUT="2_process_datasets/utils/hv_pointpillars_secfpn_6x8_160e_kitti-3d-3class_20220301_150306-37dc2420.pth"

DAVE2_ID="1_pJHuvU4386FOYrF_B0ETIZGmShObhIF"
DAVE2_OUT="system_under_test/final.h5"

RAW_BAG_ID="1ijhejhNO19jvrb3BUkEKRlxw-SkfZRhu"
RAW_BAG_OUT="data/raw_ros_data/reference_bag.bag"

VALIDATION_ID="16iTu0wRpsOU_tOU-lxcPNd9mEVJnlK1C"
VALIDATION_OUT="data_for_validation.zip"          # unzipped into data/

QUICKSTART_ID="1MmAYlxy67F1oxDKADHl3yUZochmifV1Q"
QUICKSTART_OUT="data.zip"                          # unzipped into project root

# ----------------------------------------------------------------------------
# Arg parsing
# ----------------------------------------------------------------------------
DO_WEIGHTS=0
DO_RAW_BAG=0
DO_VALIDATION=0
DO_QUICKSTART=0
FORCE=0

if [ $# -eq 0 ]; then
    DO_WEIGHTS=1
    DO_RAW_BAG=1
    DO_VALIDATION=1
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --weights) DO_WEIGHTS=1 ;;
        --raw-bag) DO_RAW_BAG=1 ;;
        --validation-data) DO_VALIDATION=1 ;;
        --quickstart) DO_QUICKSTART=1 ;;
        --all) DO_WEIGHTS=1; DO_RAW_BAG=1; DO_VALIDATION=1 ;;
        --force) FORCE=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1"
            echo "Run with --help for usage."
            exit 1
            ;;
    esac
    shift
done

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { echo "[fetch_assets] $*"; }
err()  { echo "[fetch_assets][ERROR] $*" >&2; }

ensure_gdown() {
    if ! command -v gdown >/dev/null 2>&1; then
        log "gdown not found, installing..."
        pip install -U gdown
    fi
}

# download_file GDRIVE_ID OUTPUT_PATH LABEL
download_file() {
    local gid="$1" out="$2" label="$3"

    if [ -f "$out" ] && [ "$FORCE" -eq 0 ]; then
        log "SKIP  $label already exists at $out (use --force to re-download)"
        return 0
    fi

    mkdir -p "$(dirname "$out")"
    log "GET   $label -> $out"
    if ! gdown "$gid" -O "$out"; then
        err "Failed to download $label (id=$gid). Check the manual link in the README and retry, or Google Drive may be rate-limiting anonymous downloads."
        return 1
    fi
}

# download_and_unzip GDRIVE_ID ZIP_PATH EXTRACT_DIR LABEL
download_and_unzip() {
    local gid="$1" zip_path="$2" extract_dir="$3" label="$4"

    log "GET   $label -> $zip_path"
    if ! gdown "$gid" -O "$zip_path"; then
        err "Failed to download $label (id=$gid)."
        return 1
    fi

    mkdir -p "$extract_dir"
    log "UNZIP $zip_path -> $extract_dir"
    unzip -o "$zip_path" -d "$extract_dir"
    rm -f "$zip_path"
}

# ----------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------
ensure_gdown

FAILED=0

if [ "$DO_WEIGHTS" -eq 1 ]; then
    log "== Model weights =="
    download_file "$FCOS3D_ID" "$FCOS3D_OUT" "FCOS3D checkpoint (~1GB)" || FAILED=1
    download_file "$POINTPILLARS_ID" "$POINTPILLARS_OUT" "PointPillars checkpoint (~20MB)" || FAILED=1
    download_file "$DAVE2_ID" "$DAVE2_OUT" "DAVE-2 weights (~50MB)" || FAILED=1
fi

if [ "$DO_RAW_BAG" -eq 1 ]; then
    log "== Raw ROS bag (~6GB) =="
    download_file "$RAW_BAG_ID" "$RAW_BAG_OUT" "reference_bag.bag" || FAILED=1
fi

if [ "$DO_VALIDATION" -eq 1 ]; then
    log "== Validation data (Step 6) =="
    if [ -d "data/data_for_validation" ] && [ "$FORCE" -eq 0 ]; then
        log "SKIP  data/data_for_validation already exists (use --force to re-download)"
    else
        rm -rf data/data_for_validation
        download_and_unzip "$VALIDATION_ID" "$VALIDATION_OUT" "data" "validation data bundle" || FAILED=1
    fi
fi

if [ "$DO_QUICKSTART" -eq 1 ]; then
    log "== Quick Start precomputed dataset (skips Steps 1-4) =="
    download_and_unzip "$QUICKSTART_ID" "$QUICKSTART_OUT" "." "precomputed data.zip" || FAILED=1
    log "Remember to run: python 4_gaussian_splatting_preparation/4D_fix_paths.py"
fi

echo
log "== Summary =="
[ -f "$FCOS3D_OUT" ]        && log "OK    $FCOS3D_OUT"
[ -f "$POINTPILLARS_OUT" ]  && log "OK    $POINTPILLARS_OUT"
[ -f "$DAVE2_OUT" ]         && log "OK    $DAVE2_OUT"
[ -f "$RAW_BAG_OUT" ]       && log "OK    $RAW_BAG_OUT"
[ -d "data/data_for_validation" ] && log "OK    data/data_for_validation/"
[ -d "data/data_for_carla" ] && log "OK    data/data_for_carla/ (from quickstart)"

if [ "$FAILED" -eq 1 ]; then
    err "One or more downloads failed. See messages above."
    exit 1
fi

log "Done."
