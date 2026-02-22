# Reproducing Results from ML Repos

Hard-won lessons from containerizing and benchmarking ML models (HaMeR, etc.) on cloud GPUs.

---

## 1. Inventory Dependencies Before You Start

ML repos rarely list everything you need. Expect three categories:

| Category | Examples | Typical size | Where to find |
|----------|----------|-------------|---------------|
| **Model checkpoints** | `.ckpt`, `.pth`, `.bin` | 1-10 GB each | README, `fetch_data.sh` scripts, Google Drive links |
| **Evaluation datasets** | Images, annotations, `.npz` | 500 MB - 50 GB | Paper references, separate download pages |
| **Licensed models** | SMPL, MANO, FLAME | 1-50 MB | Requires registration, manual download |

**Action:** Read the repo's README, any `fetch_*.sh` or `download_*.sh` scripts, and the eval config files to build a complete dependency list before writing a single Dockerfile.

## 2. Download URLs Go Stale

Academic hosting is unreliable. URLs break when:
- Professors move institutions (UT Austin link died, data moved to Dropbox)
- University servers get decommissioned
- Google Drive links hit download quotas

**Action:**
- Mirror all dependencies to a durable location (GCS bucket, S3, etc.) immediately after first successful download
- Record the original URL, the mirrored URL, and the date in a manifest
- For large files, use `gcloud storage cp` (not `gsutil`) — it handles many small files 10-100x faster

## 3. Separate Code from Data in Docker Images

Baking checkpoints into Docker images creates 30-40 GB images that are:
- Slow to build (re-downloads on every layer cache miss)
- Slow to push/pull
- Wasteful (identical data duplicated across baseline/optimized variants)

**Better pattern:**
- Store large deps in a GCS bucket
- Mount at runtime via gcsfuse (or download in entrypoint)
- Keep Docker images to code + pip packages only (~15-17 GB for CUDA + PyTorch + deps)

```dockerfile
# Install gcsfuse in the image
RUN apt-get install -y fuse lsb-release gnupg2 libosmesa6 && \
    export GCSFUSE_REPO="gcsfuse-$(lsb_release -cs)" && \
    echo "deb https://packages.cloud.google.com/apt $GCSFUSE_REPO main" > /etc/apt/sources.list.d/gcsfuse.list && \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add - && \
    apt-get update && apt-get install -y gcsfuse
```

**Runtime:** `docker run --gpus all --cap-add SYS_ADMIN --device /dev/fuse <image>`

## 4. Headless Rendering is a Minefield

Most 3D vision repos (hand/body mesh recovery, NeRF, etc.) use `pyrender`, `trimesh`, or `Open3D` for mesh visualization. These need OpenGL, which doesn't exist on headless servers.

**The problem chain:**
1. `pyrender.OffscreenRenderer` needs EGL or OSMesa
2. EGL needs `libEGL_nvidia.so` which requires the `graphics` NVIDIA driver capability
3. GCP Deep Learning VM images ship NVIDIA compute drivers but **not** EGL/graphics libraries
4. `--privileged` in Docker bypasses nvidia-container-toolkit's library injection, making EGL unavailable even if the host had it
5. `NVIDIA_DRIVER_CAPABILITIES=all` only works if the host actually has `libnvidia-gl`

**The fix:** Use OSMesa (CPU software rendering). It's slower but works everywhere:
```dockerfile
ENV PYOPENGL_PLATFORM=osmesa
RUN apt-get install -y libosmesa6
```

This is fine for eval — the renderer is only used for visualization, not metric computation. GPU inference is unaffected.

## 5. GCS Service Account Permissions

When running scripts on GCP VMs, the VM's service account needs explicit permissions:

| Operation | Required role |
|-----------|-------------|
| Read from GCS bucket | `roles/storage.objectViewer` |
| Write to GCS bucket | `roles/storage.objectAdmin` |
| Pull from Artifact Registry | `roles/artifactregistry.reader` |
| Push to Artifact Registry | `roles/artifactregistry.writer` |

```bash
gcloud storage buckets add-iam-policy-binding gs://BUCKET \
  --member="serviceAccount:COMPUTE_SA@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

## 6. Build on a VM, Not Your Laptop

ML Docker builds compile C++ extensions (detectron2, mmcv, custom CUDA kernels). This is:
- Slow on laptops (30-60 min)
- Architecture-dependent (ARM Mac builds won't run on x86 cloud VMs)

**Pattern:**
```bash
# SCP build context to a beefy VM
gcloud compute scp --recurse modules/hamer/ build-vm:/tmp/build/ --tunnel-through-iap
# Build there
gcloud compute ssh build-vm --command="cd /tmp/build && sudo docker build -t IMAGE -f Dockerfile ."
# Push from VM (fast, same region as AR)
gcloud compute ssh build-vm --command="sudo docker push IMAGE"
```

Use `e2-standard-16` or bigger. Docker layer caching means subsequent builds are fast (~30s if only the entrypoint changed).

## 7. Make Downloads Idempotent

Download scripts will fail partway through. Design for re-runs:
```bash
# Skip if file exists
if [ ! -f "model.tar.gz" ]; then
    wget "$URL" -O "model.tar.gz"
fi

# Check extraction result, not just directory existence
if [ ! -d "data/evaluation/rgb" ]; then
    rm -rf data  # Clean partial extraction
    tar -xzf model.tar.gz
fi
```

Don't create the target directory before extraction — if extraction fails, the directory exists but is empty, and the idempotent check skips it on re-run.

## 8. Licensed Dependencies

Some dependencies (MANO, SMPL, FLAME body models) require:
- Registering on the provider's website
- Agreeing to a license
- Manual download

**You cannot automate this.** Instead:
- Document the exact URL and file path in your scripts
- Print a reminder at the end of automated setup
- Upload manually to your GCS bucket once obtained
- Never commit these files to git

## 9. Eval Config Path Mismatches

ML repos hardcode dataset paths from the author's machine:
```yaml
# Original config
img_dir: /home/pavlakos/datasets/FreiHAND_pub_v2_eval/evaluation/rgb/
```

**Fix in Dockerfile:**
```dockerfile
RUN sed -i 's|/home/pavlakos/datasets/FreiHAND_pub_v2_eval/evaluation/rgb/|/datasets/FreiHAND/evaluation/rgb/|g' \
    configs/datasets_eval.yaml
```

Check all YAML/JSON config files for hardcoded paths before running.

## 10. Verify Before You Run

After setup, verify every critical file exists before launching the expensive GPU eval:
```bash
for f in checkpoint.ckpt dataset/images model.pkl; do
    [ -f "$f" ] && echo "OK: $f" || echo "MISSING: $f"
done
```

A 5-second verification saves 10 minutes of GPU time waiting for a FileNotFoundError.

---

## Quick Reference: Full Reproduction Checklist

1. [ ] Read repo README, download scripts, and eval configs
2. [ ] List all dependencies with URLs and sizes
3. [ ] Download deps to a cloud VM (not laptop)
4. [ ] Mirror to durable storage (GCS/S3)
5. [ ] Handle licensed deps manually
6. [ ] Write Dockerfile with code only (no data baked in)
7. [ ] Add `libosmesa6` + `ENV PYOPENGL_PLATFORM=osmesa` for repos with mesh rendering
8. [ ] Fix hardcoded dataset paths via `sed` in Dockerfile
9. [ ] Write entrypoint that mounts/symlinks data and verifies critical files
10. [ ] Build on a cloud VM, push to AR
11. [ ] Test with `--gpus all --cap-add SYS_ADMIN --device /dev/fuse`
12. [ ] Verify eval output matches expected metrics
