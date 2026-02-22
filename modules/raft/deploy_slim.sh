#!/bin/bash
# deploy_slim.sh — Build and push slim RAFT images from build-vm.
set -e

VM=build-vm
ZONE=europe-west1-b
PROJECT=$(gcloud config get-value project)
AR_REPO="europe-west1-docker.pkg.dev/$PROJECT/bench-test-images"
BASELINE="$AR_REPO/raft-baseline-slim:latest"
OPTIMIZED="$AR_REPO/raft-optimized-slim:latest"

echo "Project:   $PROJECT"
echo "AR repo:   $AR_REPO"
echo "Baseline:  $BASELINE"
echo "Optimized: $OPTIMIZED"
echo ""

# Copy build context to VM
echo "==> Copying build context to $VM..."
gcloud compute scp --zone=$ZONE --tunnel-through-iap --recurse \
    modules/raft/ "$VM:/tmp/raft-build-slim/"

# Build baseline slim image on VM
echo "==> Building baseline slim image on $VM..."
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap --command="
  cd /tmp/raft-build-slim
  sudo docker build -t $BASELINE -f Dockerfile.baseline.slim .
"

# Build optimized slim image on VM
echo "==> Building optimized slim image on $VM..."
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap --command="
  cd /tmp/raft-build-slim
  sudo docker build -t $OPTIMIZED -f Dockerfile.optimized.slim .
"

# Push both to Artifact Registry
echo "==> Pushing slim images to Artifact Registry..."
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap --command="
  sudo bash -c 'gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet'
  sudo docker push $BASELINE
  sudo docker push $OPTIMIZED
"

echo ""
echo "Done!"
echo "Baseline:  $BASELINE"
echo "Optimized: $OPTIMIZED"
echo ""
echo "Run with:  docker run --gpus all --cap-add SYS_ADMIN --device /dev/fuse <image>"
