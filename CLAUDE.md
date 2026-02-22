# HackEurope26

## Project Overview
ML optimization benchmarking pipeline that takes a target model (HaMeR - Hand Mesh Recovery), analyzes it for optimizations, applies them, and benchmarks both versions on cloud GPUs.

## Build Process
Docker container images are built on a GCP VM (`build-vm`, e2-standard-16, europe-west1-b). The `modules/hamer/deploy.sh` script copies the build context to the VM via `gcloud compute scp`, builds both baseline and optimized Docker images there, and pushes them to Artifact Registry (`europe-west1-docker.pkg.dev`). Inference/eval runs on separate GPU VMs, not on the build VM.

## Key Paths
- `modules/hamer/` — Dockerfiles, deploy script, and optimized modules for HaMeR
- `src/` — Pipeline orchestration code (clone, analyze, optimize, benchmark, report)
- `output/original/hamer/` — Cloned vanilla HaMeR repo
- `output/optimized/hamer/` — Optimized HaMeR copy
- `config.yaml` — Target repo and cloud config

## Optimizations Applied (HaMeR)
1. `torch.compile()` on MANO forward pass (reduce-overhead mode)
2. AMP (bfloat16 autocast) in HaMeR forward_step
3. Early stopping in IEF iteration loop (eval only, patience=10, min_rel_improvement=1e-4)
