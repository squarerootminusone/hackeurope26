#!/bin/bash
set -e

VM=build-vm
ZONE=europe-west1-b
PROJECT=$(gcloud config get-value project)
BASELINE=europe-west1-docker.pkg.dev/$PROJECT/hamer/hamer-baseline:latest
OPTIMIZED=europe-west1-docker.pkg.dev/$PROJECT/hamer/hamer-optimized:latest

echo "Project: $PROJECT"
echo "Building baseline: $BASELINE"
echo "Building optimized: $OPTIMIZED"
echo ""

# Copy all build context to VM
echo "==> Copying build context to $VM..."
gcloud compute scp --zone=$ZONE --recurse \
  modules/hamer/ $VM:/tmp/hamer-build/

# Build both images on VM
echo "==> Building baseline image on $VM..."
gcloud compute ssh $VM --zone=$ZONE --command="
  cd /tmp/hamer-build
  sudo docker build -t $BASELINE -f Dockerfile.baseline .
"

echo "==> Building optimized image on $VM..."
gcloud compute ssh $VM --zone=$ZONE --command="
  cd /tmp/hamer-build
  sudo docker build -t $OPTIMIZED -f Dockerfile.optimized .
"

# Push both to Artifact Registry
echo "==> Pushing images to Artifact Registry..."
gcloud compute ssh $VM --zone=$ZONE --command="
  gcloud auth configure-docker europe-west1-docker.pkg.dev --quiet
  sudo docker push $BASELINE
  sudo docker push $OPTIMIZED
"

echo ""
echo "Done!"
echo "Baseline:  $BASELINE"
echo "Optimized: $OPTIMIZED"
