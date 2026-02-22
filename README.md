# Infrastructure
- GKE Autopilot compute cluster `bench-test-cluster`
- Orchestration VM with the dashboard `build-vm`
- Artifact Registry repository `bench-test-images`
- GCS bucket `bench-test-dependencies`
- Carbon-aware request router (`./carbon-router/`, prod-ready)

# Workflow

1. In the dashboard input the repo and SCI parameters
2. Upon clicking "Evaluation" the backend process starts
  - the input repo is downloaded to the `build-vm`
  - Claude Code is called to evaluate large dependencies (e.g. model weights)
  - large dependencies are downloaded to a `bench-test-dependencies`
  - unoptimized image is created and pushed to `bench-test-images`
  - Claude Code is called in plan mode to evaluate repo's weak perofrmance spots
  - the optimized version is created and pushed to `bench-test-images`
  - GKE jobs for the selected GPUs for both optimized and unoptimized versions are created, alongside their evaluation table entries
3. When a GKE job finishes it updates the database entry with its runtime
4. The dashboard pulls information about finished jobs and shows the SCI

# HaMeR

[HaMeR (Hand Mesh Recovery)](https://github.com/geopavlakos/hamer) is a transformer-based model for reconstructing 3D hand meshes from single RGB images.

## Dependencies
- **Model checkpoints**: `hamer.ckpt` (~5.7 GB), ViTPose `wholebody.pth` (~700 MB)
- **MANO hand model**: `MANO_RIGHT.pkl` (~3.8 MB, license-restricted from [mano.is.tue.mpg.de](https://mano.is.tue.mpg.de))
- **FreiHAND eval dataset**: 3,960 evaluation images (~724 MB) from [Uni Freiburg](https://lmb.informatik.uni-freiburg.de/resources/datasets/FreihandDataset.en.html)
- **Evaluation metadata**: per-dataset `.npz` annotations (~300 MB) from [Dropbox](https://www.dropbox.com/scl/fi/7ip2vnnu355e2kqbyn1bc/hamer_evaluation_data.tar.gz?rlkey=nb4x10uc8mj2qlfq934t5mdlh)

All large dependencies are stored in `gs://bench-test-dependencies-data-platform-dev-486916/hamer/` and mounted at runtime via gcsfuse.

## Optimizations Applied
1. **`torch.compile()`** on MANO forward pass (reduce-overhead mode)
2. **AMP (bfloat16 autocast)** in HaMeR `forward_step`
3. **Early stopping** in IEF iteration loop (eval only, patience=10, min_rel_improvement=1e-4)

# RAFT

[RAFT (Recurrent All-Pairs Field Transforms)](https://github.com/princeton-vl/RAFT) is a deep learning model for optical flow estimation that uses iterative refinement over a 4D correlation volume.

## Dependencies
- **Model checkpoint**: `raft-sintel.pth` (~20 MB) — pre-trained on FlyingChairs → FlyingThings3D → Sintel
- **MPI-Sintel dataset**: training split with `clean`, `final`, and `flow` directories (~5.5 GB) from [MPI Sintel](http://sintel.is.tue.mpg.de/)
- **Python packages**: PyTorch (cu118), torchvision, scipy, opencv-python-headless, Pillow, matplotlib, tensorboard

All large dependencies are stored in `gs://bench-test-dependencies-data-platform-dev-486916/raft/` and mounted at runtime via GCS Fuse CSI driver.

## Optimizations Applied
1. **`torch.compile()`** on the full RAFT model (reduce-overhead mode) with CUDA graph warm-up
2. **Skip intermediate upsampling** — only compute the convex upsampling mask and 8× upsample on the final iteration during inference (saves ~11 redundant upsample passes)
3. **Cache correlation delta grid** — pre-compute the bilinear sampling offset grid once in `CorrBlock.__init__` instead of rebuilding `torch.linspace` + `meshgrid` on every forward call (12× per frame)
4. **`channels_last` memory format** — convert model and inputs to NHWC layout for optimized Conv2d kernels on NVIDIA GPUs
