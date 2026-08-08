#!/usr/bin/env bash
#
# run_step5.sh — container-native replacement for 5_execute_simulation/step5.sh
#
# The original script opened 4 gnome-terminal windows and used conda
# environment names directly. In the containerized setup:
#   - CARLA no longer needs 3C_setup_carla.py (the carla-server container
#     IS the running CARLA instance already).
#   - "Terminals" become `docker compose exec pipeline ...` /
#     `docker compose --profile dave2 up -d dave2-server` calls.
#   - Conda envs are still selected the same way, just via
#     `conda run -n <env>` inside the pipeline container.
#
# Usage (run from the project root, i.e. next to docker-compose.yml):
#   bash scripts/run_step5.sh --mode 5A
#   bash scripts/run_step5.sh --mode 5B
#   bash scripts/run_step5.sh --mode 5C   (default)
#   bash scripts/run_step5.sh --mode 5D
#
# Prerequisites:
#   docker compose up -d pipeline carla-server
#   (dave2-server is started automatically for 5B/5D via --profile dave2)
#
set -euo pipefail

MODE="5C"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--mode) MODE="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *) echo "[ERROR] Unknown argument: $1"; exit 1 ;;
    esac
done

PIPELINE_ENV_CARLA="data_extraction"
PIPELINE_ENV_GS="nerfstudio"

case "$MODE" in
    5A|5a)
        STEP5_SCRIPT="5_execute_simulation/5A_trajectory_only_carla.py"
        STEP5_ENV="$PIPELINE_ENV_CARLA"
        NEED_DAVE=0
        ;;
    5B|5b)
        STEP5_SCRIPT="5_execute_simulation/5B_dave2_only_carla.py"
        STEP5_ENV="$PIPELINE_ENV_CARLA"
        NEED_DAVE=1
        ;;
    5C|5c)
        STEP5_SCRIPT="5_execute_simulation/5C_trajectory_replay.py"
        STEP5_ENV="$PIPELINE_ENV_GS"
        NEED_DAVE=0
        ;;
    5D|5d)
        STEP5_SCRIPT="5_execute_simulation/5D_dave2.py"
        STEP5_ENV="$PIPELINE_ENV_GS"
        NEED_DAVE=1
        ;;
    *)
        echo "[ERROR] Invalid --mode '$MODE'. Use 5A | 5B | 5C | 5D."
        exit 1
        ;;
esac

log() { echo "[run_step5] $*"; }

exec_in_pipeline() {
    local env_name="$1" cmd="$2"
    docker compose exec -T pipeline bash -lc \
        "source /opt/conda/etc/profile.d/conda.sh && conda activate '$env_name' && cd /workspace/cam2sim && $cmd"
}

log "Mode: $MODE  (env: $STEP5_ENV, script: $STEP5_SCRIPT, dave2 needed: $NEED_DAVE)"

log "Checking that pipeline + carla-server containers are up..."
docker compose up -d pipeline carla-server

log "Waiting for CARLA RPC port (127.0.0.1:2000 inside pipeline's network ns)..."
for i in $(seq 1 60); do
    if exec_in_pipeline "$PIPELINE_ENV_CARLA" "python -c \"import socket; s=socket.create_connection(('127.0.0.1',2000),2); s.close()\"" 2>/dev/null; then
        log "CARLA is up."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "[ERROR] Timed out waiting for CARLA after 120s. Check: docker compose logs carla-server"
        exit 1
    fi
    sleep 2
done

log "Loading map + spawning hero/parked vehicles (3F_generate_carla_scenario.py)..."
exec_in_pipeline "$PIPELINE_ENV_CARLA" "python 3_generate_simulation_data/3F_generate_carla_scenario.py"

if [ "$NEED_DAVE" -eq 1 ]; then
    log "Starting dave2-server (this profile is normally off)..."
    docker compose --profile dave2 up -d dave2-server

    log "Waiting for DAVE-2 server (127.0.0.1:5090)..."
    for i in $(seq 1 60); do
        if exec_in_pipeline "$PIPELINE_ENV_CARLA" "python -c \"import socket; s=socket.create_connection(('127.0.0.1',5090),2); s.close()\"" 2>/dev/null; then
            log "DAVE-2 server is up."
            break
        fi
        if [ "$i" -eq 60 ]; then
            echo "[ERROR] Timed out waiting for DAVE-2 server after 120s. Check: docker compose logs dave2-server"
            exit 1
        fi
        sleep 2
    done
fi

log "Running $STEP5_SCRIPT in env '$STEP5_ENV' (this opens the live pygame window)..."
exec_in_pipeline "$STEP5_ENV" "python $STEP5_SCRIPT"

log "Done."
if [ "$NEED_DAVE" -eq 1 ]; then
    log "Note: dave2-server is still running. Stop it with: docker compose --profile dave2 down"
fi
