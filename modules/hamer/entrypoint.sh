#!/bin/bash
# entrypoint.sh — Mount GCS via gcsfuse and set up symlinks before running the app.
set -euo pipefail

GCS_BUCKET="${GCS_BUCKET:-bench-test-dependencies-data-platform-dev-486916}"
GCS_SUBDIR="${GCS_SUBDIR:-hamer}"
MOUNT_POINT="${MOUNT_POINT:-/mnt/gcs}"

# If MOUNT_POINT already has data (e.g. GKE GCS FUSE CSI), skip gcsfuse
if [ -d "$MOUNT_POINT/_DATA" ] || [ -d "$MOUNT_POINT/datasets" ]; then
    echo "[entrypoint] Data already present at $MOUNT_POINT, skipping gcsfuse mount"
else
    echo "[entrypoint] Mounting gs://$GCS_BUCKET (subdir: $GCS_SUBDIR) at $MOUNT_POINT ..."
    mkdir -p "$MOUNT_POINT"
    gcsfuse --only-dir "$GCS_SUBDIR" \
            --implicit-dirs \
            --log-severity warning \
            "$GCS_BUCKET" "$MOUNT_POINT"
    echo "[entrypoint] GCS mounted at $MOUNT_POINT"
fi

# Symlink _DATA → GCS
if [ ! -e /app/hamer/_DATA ]; then
    ln -s "$MOUNT_POINT/_DATA" /app/hamer/_DATA
    echo "[entrypoint] Symlinked /app/hamer/_DATA -> $MOUNT_POINT/_DATA"
else
    echo "[entrypoint] /app/hamer/_DATA already exists, skipping symlink"
fi

# Symlink FreiHAND dataset
if [ ! -e /datasets/FreiHAND ]; then
    mkdir -p /datasets
    ln -s "$MOUNT_POINT/datasets/FreiHAND" /datasets/FreiHAND
    echo "[entrypoint] Symlinked /datasets/FreiHAND -> $MOUNT_POINT/datasets/FreiHAND"
else
    echo "[entrypoint] /datasets/FreiHAND already exists, skipping symlink"
fi

# Symlink hamer_evaluation_data
if [ ! -e /app/hamer/hamer_evaluation_data ]; then
    ln -s "$MOUNT_POINT/hamer_evaluation_data" /app/hamer/hamer_evaluation_data
    echo "[entrypoint] Symlinked /app/hamer/hamer_evaluation_data -> $MOUNT_POINT/hamer_evaluation_data"
else
    echo "[entrypoint] /app/hamer/hamer_evaluation_data already exists, skipping symlink"
fi

# Verify critical files
echo "[entrypoint] Verifying critical files ..."
CRITICAL_FILES=(
    "/app/hamer/_DATA/hamer_ckpts/checkpoints/hamer.ckpt"
    "/app/hamer/_DATA/vitpose_ckpts/vitpose+_huge/wholebody.pth"
    "/app/hamer/_DATA/data/mano_mean_params.npz"
)
for f in "${CRITICAL_FILES[@]}"; do
    if [ -f "$f" ]; then
        echo "  OK: $f"
    else
        echo "  MISSING: $f"
    fi
done

# Warn on optional files
OPTIONAL_FILES=(
    "/app/hamer/_DATA/data/mano/MANO_RIGHT.pkl"
    "/datasets/FreiHAND/evaluation/rgb"
    "/app/hamer/hamer_evaluation_data"
)
for f in "${OPTIONAL_FILES[@]}"; do
    if [ -e "$f" ]; then
        echo "  OK: $f"
    else
        echo "  WARNING (optional): $f not found"
    fi
done

# Ensure results directory exists
mkdir -p /app/hamer/results

echo "[entrypoint] Starting: $*"
exec "$@"
