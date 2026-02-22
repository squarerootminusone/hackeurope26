#!/bin/bash
# upload_deps_to_gcs.sh — Run on build-vm to download large HaMeR deps and upload to GCS.
# Usage: bash upload_deps_to_gcs.sh
set -euo pipefail

GCS_BUCKET="${GCS_BUCKET:-gs://bench-test-dependencies-data-platform-dev-486916}"
GCS_PREFIX="${GCS_PREFIX:-hamer}"
WORKDIR="${WORKDIR:-/tmp/hamer-deps}"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "=== HaMeR dependency upload to GCS ==="
echo "Bucket:  $GCS_BUCKET/$GCS_PREFIX"
echo "Workdir: $WORKDIR"
echo ""

# ---- Step 1: HaMeR demo data (checkpoints + ViTPose + mano_mean_params) ----
echo "==> Step 1: HaMeR demo data (_DATA/)"
DEMO_TAR="hamer_demo_data.tar.gz"
if [ ! -f "$DEMO_TAR" ]; then
    echo "    Downloading $DEMO_TAR ..."
    wget --tries=3 --timeout=120 \
        "https://www.cs.utexas.edu/~pavlakos/hamer/data/hamer_demo_data.tar.gz" \
        -O "$DEMO_TAR"
else
    echo "    $DEMO_TAR already exists, skipping download."
fi

if [ ! -d "_DATA" ]; then
    echo "    Extracting $DEMO_TAR ..."
    tar --warning=no-unknown-keyword --exclude=".*" -xzf "$DEMO_TAR"
fi

# Verify key files exist
for f in _DATA/hamer_ckpts/checkpoints/hamer.ckpt \
         _DATA/hamer_ckpts/model_config.yaml \
         _DATA/hamer_ckpts/dataset_config.yaml \
         _DATA/vitpose_ckpts/vitpose+_huge/wholebody.pth \
         _DATA/data/mano_mean_params.npz; do
    if [ ! -f "$f" ]; then
        echo "    ERROR: Expected file $f not found after extraction!"
        exit 1
    fi
done
echo "    Verified key files in _DATA/."

echo "    Uploading _DATA/ to $GCS_BUCKET/$GCS_PREFIX/_DATA/ ..."
gsutil -m cp -r _DATA/ "$GCS_BUCKET/$GCS_PREFIX/"
echo "    Done."
echo ""

# ---- Step 2: FreiHAND evaluation dataset ----
echo "==> Step 2: FreiHAND evaluation dataset"
FREIHAND_ZIP="FreiHAND_pub_v2_eval.zip"
if [ ! -f "$FREIHAND_ZIP" ]; then
    echo "    Downloading $FREIHAND_ZIP ..."
    wget --tries=3 --timeout=120 --user-agent="Mozilla/5.0" \
        "https://lmb.informatik.uni-freiburg.de/data/freihand/FreiHAND_pub_v2_eval.zip" \
        -O "$FREIHAND_ZIP"
else
    echo "    $FREIHAND_ZIP already exists, skipping download."
fi

if [ ! -d "FreiHAND/evaluation" ]; then
    echo "    Extracting $FREIHAND_ZIP ..."
    rm -rf FreiHAND
    mkdir -p FreiHAND
    unzip -q "$FREIHAND_ZIP" -d FreiHAND
fi

if [ ! -d "FreiHAND/evaluation/rgb" ]; then
    echo "    ERROR: FreiHAND/evaluation/rgb/ not found after extraction!"
    exit 1
fi
echo "    Verified FreiHAND extraction."

echo "    Uploading FreiHAND/ to $GCS_BUCKET/$GCS_PREFIX/datasets/FreiHAND/ ..."
gsutil -m cp -r FreiHAND/ "$GCS_BUCKET/$GCS_PREFIX/datasets/"
echo "    Done."
echo ""

# ---- Step 3: HaMeR evaluation metadata ----
echo "==> Step 3: HaMeR evaluation metadata"
EVAL_TAR="hamer_evaluation_data.tar.gz"
if [ ! -f "$EVAL_TAR" ]; then
    echo "    Downloading $EVAL_TAR ..."
    wget --tries=3 --timeout=120 \
        "https://www.dropbox.com/scl/fi/7ip2vnnu355e2kqbyn1bc/hamer_evaluation_data.tar.gz?rlkey=nb4x10uc8mj2qlfq934t5mdlh" \
        -O "$EVAL_TAR"
else
    echo "    $EVAL_TAR already exists, skipping download."
fi

if [ ! -d "hamer_evaluation_data" ]; then
    echo "    Extracting $EVAL_TAR ..."
    tar --warning=no-unknown-keyword --exclude=".*" -xzf "$EVAL_TAR"
fi

if [ ! -d "hamer_evaluation_data" ]; then
    echo "    ERROR: hamer_evaluation_data/ not found after extraction!"
    exit 1
fi
echo "    Verified hamer_evaluation_data extraction."

echo "    Uploading hamer_evaluation_data/ to $GCS_BUCKET/$GCS_PREFIX/hamer_evaluation_data/ ..."
gsutil -m cp -r hamer_evaluation_data/ "$GCS_BUCKET/$GCS_PREFIX/"
echo "    Done."
echo ""

# ---- MANO_RIGHT.pkl reminder ----
echo "============================================================"
echo "NOTE: MANO_RIGHT.pkl requires a manual download due to"
echo "license restrictions."
echo ""
echo "1. Download from: https://mano.is.tue.mpg.de"
echo "2. Upload to GCS:"
echo "   gsutil cp MANO_RIGHT.pkl $GCS_BUCKET/$GCS_PREFIX/_DATA/data/mano/MANO_RIGHT.pkl"
echo "============================================================"
echo ""
echo "=== All uploads complete! ==="
echo "Verify with: gsutil ls $GCS_BUCKET/$GCS_PREFIX/"
