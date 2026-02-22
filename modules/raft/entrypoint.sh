#!/bin/bash
# entrypoint.sh — Mount GCS via gcsfuse and set up symlinks before running RAFT.
set -euo pipefail

GCS_BUCKET="${GCS_BUCKET:-bench-test-dependencies-data-platform-dev-486916}"
GCS_SUBDIR="${GCS_SUBDIR:-raft}"
MOUNT_POINT="${MOUNT_POINT:-/mnt/gcs}"

echo "[entrypoint] Mounting gs://$GCS_BUCKET (subdir: $GCS_SUBDIR) at $MOUNT_POINT ..."
mkdir -p "$MOUNT_POINT"
gcsfuse --only-dir "$GCS_SUBDIR" \
        --implicit-dirs \
        --log-severity warning \
        "$GCS_BUCKET" "$MOUNT_POINT"
echo "[entrypoint] GCS mounted at $MOUNT_POINT"

# Symlink models directory
if [ ! -e /app/raft/models ]; then
    ln -s "$MOUNT_POINT/models" /app/raft/models
    echo "[entrypoint] Symlinked /app/raft/models -> $MOUNT_POINT/models"
else
    echo "[entrypoint] /app/raft/models already exists, skipping symlink"
fi

# Symlink Sintel dataset
if [ ! -e /app/raft/datasets/Sintel ]; then
    mkdir -p /app/raft/datasets
    ln -s "$MOUNT_POINT/datasets/Sintel" /app/raft/datasets/Sintel
    echo "[entrypoint] Symlinked /app/raft/datasets/Sintel -> $MOUNT_POINT/datasets/Sintel"
else
    echo "[entrypoint] /app/raft/datasets/Sintel already exists, skipping symlink"
fi

# Verify critical files
echo "[entrypoint] Verifying critical files ..."
CRITICAL_FILES=(
    "/app/raft/models/raft-sintel.pth"
)
for f in "${CRITICAL_FILES[@]}"; do
    if [ -f "$f" ]; then
        echo "  OK: $f"
    else
        echo "  MISSING: $f"
    fi
done

OPTIONAL_FILES=(
    "/app/raft/datasets/Sintel/training/clean"
    "/app/raft/datasets/Sintel/training/final"
    "/app/raft/datasets/Sintel/training/flow"
)
for f in "${OPTIONAL_FILES[@]}"; do
    if [ -e "$f" ]; then
        echo "  OK: $f"
    else
        echo "  WARNING (optional): $f not found"
    fi
done

echo "[entrypoint] Starting: $*"
exec "$@"
