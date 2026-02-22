# Reproducing ML Benchmark Results

Step-by-step instructions for reproducing evaluation benchmarks from ML research repositories on GKE Autopilot with L4 GPUs.

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI authenticated with project access
- `kubectl` configured to your GKE Autopilot cluster
- A build VM (e2-standard-16 recommended) for Docker builds
- Artifact Registry repository for container images
- GCS bucket for large dependencies (model weights, datasets)

## 1. Clone and Understand the Target Repo

```bash
git clone <repo-url> /tmp/<repo-name>
```

Key things to identify:
- **Evaluation entry point**: look for `evaluate.py`, `eval.py`, `test.py`, or similar
- **Model checkpoints**: what `.pth`/`.ckpt` files are needed and where to download them
- **Datasets**: what evaluation datasets are required, their download URLs, and expected directory structure
- **Python dependencies**: check `requirements.txt`, `setup.py`, `pyproject.toml`
- **CUDA version requirements**: check PyTorch version constraints and CUDA compatibility

## 2. Download and Upload Dependencies to GCS

### Model Checkpoints

Download checkpoints locally first, then upload:
```bash
# Download
wget <checkpoint-url> -O /tmp/model.pth

# Upload — use gsutil -m for parallel, avoid cp -r (causes nesting issues)
gsutil -m cp /tmp/model.pth gs://<bucket>/<module>/models/
```

### Datasets

**CRITICAL**: `gsutil cp -r` nests the source directory inside the target. This is the #1 cause of broken benchmarks.

```bash
# BAD — creates gs://bucket/module/datasets/Sintel/Sintel/training/...
gsutil -m cp -r /tmp/Sintel gs://<bucket>/<module>/datasets/

# GOOD — use rsync to preserve structure without nesting
gsutil -m rsync -r /tmp/Sintel/ gs://<bucket>/<module>/datasets/Sintel/
```

Always verify the GCS structure after upload:
```bash
gsutil ls gs://<bucket>/<module>/datasets/Sintel/training/
# Should show: clean/  final/  flow/  (not Sintel/)
```

If the structure is wrong, delete and re-upload rather than trying to `gsutil mv` (which can flatten subdirectories):
```bash
gsutil -m rm -r gs://<bucket>/<module>/datasets/Sintel/
gsutil -m rsync -r /tmp/Sintel/ gs://<bucket>/<module>/datasets/Sintel/
```

### Downloading from Remote VMs

For large datasets that are slow to download locally, use the build VM:
```bash
gcloud compute ssh build-vm --zone=europe-west1-b --command="
  mkdir -p /tmp/data && cd /tmp/data
  wget <dataset-url> -O dataset.zip
  unzip dataset.zip
"
# Then upload from the VM
gcloud compute ssh build-vm --zone=europe-west1-b --command="
  gsutil -m rsync -r /tmp/data/Sintel/ gs://<bucket>/<module>/datasets/Sintel/
"
```

## 3. Create Dockerfiles

Create two Dockerfiles — `Dockerfile.baseline.slim` (vanilla) and `Dockerfile.optimized.slim` (with optimizations).

### Base Image Selection
- Use `nvidia/cuda:11.8.0-devel-ubuntu22.04` for PyTorch cu118 compatibility
- The `devel` variant is needed for `torch.compile` (includes nvcc/ptxas)

### Common Pattern
```dockerfile
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive

# System deps
RUN apt-get update && apt-get install -y \
    python3.10 python3.10-venv python3.10-dev python3-pip \
    git wget unzip curl \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.10 1
RUN pip install --upgrade pip setuptools wheel

# Clone the repo
RUN git clone <repo-url> /app/<name>
WORKDIR /app/<name>

# Install PyTorch (CUDA 11.8)
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# Install remaining deps
RUN pip install <deps from requirements.txt>

# Copy evaluation script
COPY eval_bench.py /app/<name>/eval_bench.py
```

For the optimized Dockerfile, additionally copy patched source files:
```dockerfile
COPY optimized/module_a.py /app/<name>/path/to/module_a.py
COPY optimized/eval_optimized.py /app/<name>/eval_optimized.py
```

### Evaluation Scripts

Write wrapper scripts (`eval_bench.py` / `eval_optimized.py`) that:
1. Load the model and checkpoint
2. Run a few warm-up iterations (important for accurate GPU timing)
3. Evaluate on the dataset
4. Output JSON with both accuracy metrics and timing:
```json
{
  "metric_name": 1.234,
  "pairs_per_sec": 56.7,
  "avg_ms_per_pair": 17.6,
  "total_time_sec": 120.5,
  "gpu": "NVIDIA L4"
}
```

## 4. Build and Push Containers

Build on the build VM (faster than local, avoids pushing large contexts):
```bash
# Copy build context
gcloud compute ssh build-vm --zone=europe-west1-b --command="mkdir -p /tmp/<module>-build"
gcloud compute scp --recurse modules/<module>/* build-vm:/tmp/<module>-build/ --zone=europe-west1-b

# Build and push
gcloud compute ssh build-vm --zone=europe-west1-b --command="
  cd /tmp/<module>-build
  docker build -f Dockerfile.baseline.slim -t <region>-docker.pkg.dev/<project>/<repo>/<name>-baseline-slim:latest .
  docker push <region>-docker.pkg.dev/<project>/<repo>/<name>-baseline-slim:latest
  docker build -f Dockerfile.optimized.slim -t <region>-docker.pkg.dev/<project>/<repo>/<name>-optimized-slim:latest .
  docker push <region>-docker.pkg.dev/<project>/<repo>/<name>-optimized-slim:latest
"
```

## 5. Set Up Workload Identity for GCS Access

The K8s pods need access to GCS for model weights and datasets. Use Workload Identity:

```bash
PROJECT_ID=$(gcloud config get-value project)
GSA_NAME=<module>-bench

# Create GCP service account
gcloud iam service-accounts create $GSA_NAME --display-name="<Module> benchmark WI SA"

# Grant GCS read access
gsutil iam ch serviceAccount:$GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com:objectViewer \
  gs://<bucket-name>

# Create K8s service account
kubectl create serviceaccount $GSA_NAME

# Annotate KSA with GSA
kubectl annotate serviceaccount $GSA_NAME \
  iam.gke.io/gcp-service-account=$GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com

# Allow KSA to impersonate GSA
gcloud iam service-accounts add-iam-policy-binding \
  $GSA_NAME@$PROJECT_ID.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:$PROJECT_ID.svc.id.goog[default/$GSA_NAME]"
```

## 6. Create and Submit K8s Jobs

### Job Manifest Pattern

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: <module>-baseline
  namespace: default
spec:
  backoffLimit: 0
  template:
    metadata:
      annotations:
        gke-gcsfuse/volumes: "true"   # Enable GCS Fuse CSI driver
    spec:
      serviceAccountName: <module>-bench
      nodeSelector:
        cloud.google.com/gke-accelerator: nvidia-l4
      restartPolicy: Never
      containers:
      - name: <module>
        image: <registry>/<module>-baseline-slim:latest
        command: ["/bin/bash", "-c"]
        args:
        - |
          set -ex

          # Verify GCS mount
          ls -la /mnt/gcs/

          # Create symlinks from CSI-mounted volume
          ln -sf /mnt/gcs/models /app/<module>/models
          mkdir -p /app/<module>/datasets
          ln -sf /mnt/gcs/datasets/<Dataset> /app/<module>/datasets/<Dataset>

          # Run benchmark
          cd /app/<module>
          python eval_bench.py --model models/<checkpoint> --dataset <dataset>
        resources:
          limits:
            nvidia.com/gpu: "1"
          requests:
            cpu: "4"
            memory: "16Gi"
        volumeMounts:
        - name: gcs-data
          mountPath: /mnt/gcs
          readOnly: true
      volumes:
      - name: gcs-data
        csi:
          driver: gcsfuse.csi.storage.gke.io
          readOnly: true
          volumeAttributes:
            bucketName: <bucket-name>
            mountOptions: "only-dir=<module>,implicit-dirs"
```

Key points:
- `gke-gcsfuse/volumes: "true"` annotation is **required** for the CSI driver to inject the sidecar
- `only-dir=<module>` scopes the mount to only the module's subdirectory in the bucket
- `implicit-dirs` is needed for GCS (which doesn't have real directories)
- Override the container entrypoint with `command`/`args` to use CSI-mounted data instead of in-container gcsfuse
- Use `set -ex` in the bash script for verbose output (critical for debugging)

### Submitting Jobs

```bash
# Delete any previous runs
kubectl delete job <module>-baseline --ignore-not-found
kubectl delete job <module>-optimized --ignore-not-found

# Submit
kubectl apply -f modules/<module>/k8s-baseline-job.yaml
kubectl apply -f modules/<module>/k8s-optimized-job.yaml
```

## 7. Monitor and Collect Results

```bash
# Watch pod status
kubectl get pods -l job-name=<module>-baseline -w
kubectl get pods -l job-name=<module>-optimized -w

# Stream logs (once pod is Running)
kubectl logs -f <pod-name> -c <module>

# Check job completion
kubectl get jobs <module>-baseline <module>-optimized
```

**Important**: GKE Autopilot tears down nodes quickly after pod failure. If a job fails, check logs immediately — they may become inaccessible once the node scales down.

## Common Pitfalls

| Problem | Cause | Fix |
|---------|-------|-----|
| `gsutil cp -r` nests directories | gsutil copies the dir *into* the target | Use `gsutil -m rsync -r src/ dst/` instead |
| `gsutil mv` flattens subdirectories | Glob expansion strips intermediate paths | Delete and re-upload with rsync |
| Pod exit code 2 immediately | Missing files/dirs at expected paths | Check GCS structure with `gsutil ls` |
| Empty metric lists / `ValueError` | Dataset dirs present but empty or misstructured | Verify scene subdirs exist (e.g., `alley_1/`) |
| `torch.compile` slow first run | CUDA graph capture + Triton compilation | Add warm-up passes with dummy data before timed eval |
| Logs unavailable after failure | Autopilot node scaled down | Use `set -ex` in scripts, check logs fast |
| CSI mount empty | Missing `gke-gcsfuse/volumes` annotation | Must be on `template.metadata.annotations`, not job metadata |
| Permission denied on GCS | Workload Identity not configured | Check KSA annotation + GSA IAM binding |
