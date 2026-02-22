#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# run-eval.sh — Create a GCE VM, pull a Docker image, track in evaluations DB
#
# Usage:
#   bash infra/run-eval.sh \
#     --name "hamer-baseline-l4" \
#     --image "europe-west1-docker.pkg.dev/PROJECT/hamer/hamer-baseline:latest" \
#     --machine-type g2-standard-8 \
#     --accelerator "type=nvidia-l4,count=1"
#
# Required env vars: DB_HOST, DB_USER, DB_PASS, DB_NAME, SQL_INSTANCE
# ---------------------------------------------------------------------------

EVAL_NAME=""
DOCKER_IMAGE=""
MACHINE_TYPE="g2-standard-8"
ACCELERATOR=""
ZONE="europe-west1-b"
PROJECT=$(gcloud config get-value project)

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--name)         EVAL_NAME="$2";    shift 2 ;;
    -i|--image)        DOCKER_IMAGE="$2"; shift 2 ;;
    -m|--machine-type) MACHINE_TYPE="$2"; shift 2 ;;
    -a|--accelerator)  ACCELERATOR="$2";  shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

[[ -z "$EVAL_NAME" ]]    && { echo "Error: --name is required"; exit 1; }
[[ -z "$DOCKER_IMAGE" ]] && { echo "Error: --image is required"; exit 1; }

: "${DB_HOST:?DB_HOST env var required}"
: "${DB_USER:?DB_USER env var required}"
: "${DB_PASS:?DB_PASS env var required}"
: "${DB_NAME:?DB_NAME env var required}"
: "${SQL_INSTANCE:?SQL_INSTANCE env var required}"

# Sanitize eval name into a valid VM name
VM_NAME="eval-$(echo "$EVAL_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9-' '-' | sed 's/^-//;s/-$//' | head -c 50)"

echo "=== Starting Evaluation ==="
echo "Name:     $EVAL_NAME"
echo "Image:    $DOCKER_IMAGE"
echo "Machine:  $MACHINE_TYPE"
echo "Accel:    ${ACCELERATOR:-none}"
echo "VM:       $VM_NAME"
echo ""

# --- Build accelerator flags ---
ACCEL_FLAGS=""
if [[ -n "$ACCELERATOR" ]]; then
  ACCEL_FLAGS="--accelerator=$ACCELERATOR --maintenance-policy=TERMINATE"
fi

# --- Startup script (runs on the VM) ---
read -r -d '' STARTUP_SCRIPT << 'EOSTARTUP' || true
#!/bin/bash
set -euo pipefail
exec > /var/log/eval-startup.log 2>&1

METADATA_URL="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
MH="Metadata-Flavor: Google"

DB_HOST=$(curl -sf -H "$MH" "$METADATA_URL/db-host")
DB_USER=$(curl -sf -H "$MH" "$METADATA_URL/db-user")
DB_PASS=$(curl -sf -H "$MH" "$METADATA_URL/db-pass")
DB_NAME=$(curl -sf -H "$MH" "$METADATA_URL/db-name")
DOCKER_IMAGE=$(curl -sf -H "$MH" "$METADATA_URL/docker-image")

# Wait for eval-id metadata (set after VM creation)
EVAL_ID=""
for i in $(seq 1 60); do
  EVAL_ID=$(curl -sf -H "$MH" "$METADATA_URL/eval-id" 2>/dev/null || true)
  if [[ -n "$EVAL_ID" ]]; then
    echo "Got eval-id=$EVAL_ID on attempt $i"
    break
  fi
  echo "Waiting for eval-id metadata... (attempt $i/60)"
  sleep 5
done
if [[ -z "$EVAL_ID" ]]; then
  echo "ERROR: eval-id metadata not set after 5 minutes"
  exit 1
fi

# Install mysql client
apt-get update -qq && apt-get install -y -qq default-mysql-client

# Configure docker for Artifact Registry
gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet 2>/dev/null || true

# Mark start
mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" \
  -e "UPDATE evaluations SET start_runtime_date = NOW() WHERE id = $EVAL_ID;"

echo "==> Pulling image: $DOCKER_IMAGE"
docker pull "$DOCKER_IMAGE"

echo "==> Running container"
docker run --gpus all "$DOCKER_IMAGE"
EXIT_CODE=$?

# Mark end
mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" \
  -e "UPDATE evaluations SET end_runtime_date = NOW() WHERE id = $EVAL_ID;"

echo "Container exited with code $EXIT_CODE"
EOSTARTUP

# --- Step 1: Create VM ---
echo "==> Creating VM $VM_NAME..."
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  $ACCEL_FLAGS \
  --image-family=pytorch-latest-gpu \
  --image-project=deeplearning-platform-release \
  --boot-disk-size=200GB \
  --scopes=cloud-platform \
  --metadata=db-host="$DB_HOST",db-user="$DB_USER",db-pass="$DB_PASS",db-name="$DB_NAME",docker-image="$DOCKER_IMAGE" \
  --metadata-from-file=startup-script=<(echo "$STARTUP_SCRIPT") \
  --project="$PROJECT"

echo "VM created."

# --- Step 2: Authorize VM IP on Cloud SQL ---
VM_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)" \
  --project="$PROJECT")

echo "==> Authorizing VM IP $VM_IP on Cloud SQL..."

EXISTING=$(gcloud sql instances describe "$SQL_INSTANCE" \
  --project="$PROJECT" \
  --format="value(settings.ipConfiguration.authorizedNetworks[].value)" \
  | tr '\n' ',' | sed 's/,$//')

if [[ -n "$EXISTING" ]]; then
  ALL_NETWORKS="${EXISTING},${VM_IP}/32"
else
  ALL_NETWORKS="${VM_IP}/32"
fi

gcloud sql instances patch "$SQL_INSTANCE" \
  --authorized-networks="$ALL_NETWORKS" \
  --project="$PROJECT" \
  --quiet

# --- Step 3: Insert evaluation row ---
echo "==> Inserting evaluation row..."
EVAL_ID=$(mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" -N -B \
  -e "INSERT INTO evaluations (evaluation_name, vm_reference) VALUES ('$EVAL_NAME', '$VM_NAME'); SELECT LAST_INSERT_ID();")

echo "Evaluation ID: $EVAL_ID"

# --- Step 4: Push eval-id to VM metadata ---
gcloud compute instances add-metadata "$VM_NAME" \
  --zone="$ZONE" \
  --metadata=eval-id="$EVAL_ID" \
  --project="$PROJECT"

echo ""
echo "============================================"
echo "  Evaluation launched"
echo "============================================"
echo "  ID:   $EVAL_ID"
echo "  VM:   $VM_NAME"
echo "  SSH:  gcloud compute ssh $VM_NAME --zone=$ZONE"
echo "  Logs: gcloud compute ssh $VM_NAME --zone=$ZONE --command='tail -f /var/log/eval-startup.log'"
echo "============================================"
