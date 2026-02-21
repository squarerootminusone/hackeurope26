# hackeurope26

## Technologies Used

### Languages

| Language | Usage |
|----------|-------|
| **Python 3** | Primary language for the entire pipeline, ML training, benchmarking, cloud orchestration, and reporting |
| **Bash / Shell** | Benchmark entrypoint scripts (Jinja2-templated), GPU/CPU monitoring loops, system commands |
| **YAML** | Pipeline configuration (`config.yaml`) |
| **Markdown** | Generated benchmark reports, optimization guides |
| **Jinja2 (templating)** | Dockerfile and entrypoint script generation (`templates/Dockerfile.base.j2`, `templates/benchmark_entrypoint.sh.j2`) |

### Frameworks & Libraries

| Technology | Version/Details | Usage |
|------------|----------------|-------|
| **PyTorch** | Core ML framework | Model definition (`nn.Module`, `nn.Sequential`, `nn.Linear`, `nn.ReLU`, `nn.CrossEntropyLoss`), training loops, inference, `torch.compile`, `torch.cuda.amp`, `torch.quantization`, gradient checkpointing, CUDA optimizations (`cudnn.benchmark`, TF32, `inference_mode`) |
| **scikit-learn** | `make_classification`, `train_test_split` | Synthetic dataset generation and train/test splitting in the carbon demo |
| **CodeCarbon** | `EmissionsTracker` | Tracks energy consumption (kWh), CO2 emissions (kg CO2eq), and duration during ML workloads. Writes results to `emissions.csv` |
| **Jinja2** | Template engine | Auto-generates Dockerfiles and benchmark entrypoint shell scripts from `.j2` templates |
| **PyYAML** | `yaml.safe_load` | Parses `config.yaml` pipeline configuration |
| **Requests** | HTTP client | Calls the Electricity Maps API for live grid carbon intensity |
| **Docker SDK for Python** | `docker` package | Programmatic Docker image build, tag, and push operations |
| **RunPod Python SDK** | `runpod` package | Creates, monitors, and terminates GPU cloud instances; executes remote commands |
| **HuggingFace Hub** | `huggingface_hub.snapshot_download` | Downloads model repositories from HuggingFace when git clone is insufficient |

### APIs & External Services

| API / Service | Endpoint / Details | Usage |
|---------------|-------------------|-------|
| **Electricity Maps API** | `GET https://api.electricitymaps.com/v3/carbon-intensity/latest?zone={zone}` with `auth-token` header | Fetches real-time grid carbon intensity (gCO2eq/kWh) for a geographic zone. Used to compute the `I` component of the SCI score. Auth via `ELECTRICITY_MAPS_API_KEY` env var. Falls back to 475 gCO2/kWh (world average) if unavailable |
| **RunPod API** | Via `runpod` Python SDK | Creates GPU pod instances, polls status until RUNNING, executes benchmark commands remotely, fetches logs, and terminates pods. Auth via `RUNPOD_API_KEY` env var |
| **Docker Hub** | Default container registry | Pushes built Docker images (original and optimized) so cloud GPU instances can pull them |
| **HuggingFace Hub** | `huggingface.co` / `hf.co` URLs | Clones or downloads ML model repositories (e.g., model weights, configs) |
| **GitHub** | Git over SSH (`git@github.com:...`) | Source repository hosting, target repo cloning (e.g., `geopavlakos/hamer`) |
| **Claude Code CLI** | `claude -p "<prompt>" --output-format text` (subprocess) | Used in `analyzer.py` to analyze repos and propose optimization plans, in `optimizer.py` to apply code-level optimizations, and in `docker_builder.py` to diagnose and fix Docker build failures |

### Cloud Services & Infrastructure

| Service | Details |
|---------|---------|
| **RunPod** | Serverless GPU cloud provider. Creates pods with 1 GPU, 50GB volume, 50GB container disk. Exposes ports 22/tcp (SSH) and 8888/http. Region configurable (default: US) |
| **Docker / Docker Hub** | Containerization platform. Images built from auto-generated Dockerfiles based on `nvidia/cuda:11.7.1-devel-ubuntu22.04` base image. System packages: git, wget, curl, build-essential, python3, sysstat, procps |
| **GitHub** | Version control and remote repository hosting for both the pipeline itself and target ML repos |

### Hardware & GPU Types

| GPU | Context |
|-----|---------|
| **NVIDIA A100 80GB PCIe** | Primary benchmark GPU; also the reference hardware for SCI embodied emissions calculation (TE = 150,000 gCO2, 4-year lifespan) |
| **NVIDIA L40S** | Secondary benchmark GPU type |
| **NVIDIA RTX 4090** | Consumer-grade benchmark GPU type (mapped to "NVIDIA GeForce RTX 4090" in RunPod) |
| **NVIDIA H100 80GB HBM3** | High-end benchmark GPU option |

### Standards & Specifications

| Standard | Details |
|----------|---------|
| **Green Software Foundation SCI** | Software Carbon Intensity score: `SCI = (E * I + M) / R`. E = energy consumed (kWh), I = carbon intensity (gCO2/kWh), M = embodied emissions (gCO2), R = functional units (inferences). Embodied emissions formula: `M = TE * (TiR / (Lifespan * 8760))` |

### Monitoring & System Tools

| Tool | Usage |
|------|-------|
| **nvidia-smi** | GPU metrics collection every 1 second during benchmarks: utilization %, memory used/total, power draw (W), temperature (C) |
| **mpstat** | CPU utilization monitoring during benchmarks |
| **free** | RAM usage monitoring (used/total MB) |
| **git** | Repository cloning with `--recursive --depth 1` for submodules |

### Target ML Model (configured in `config.yaml`)

| Field | Value |
|-------|-------|
| **Repository** | `https://github.com/geopavlakos/hamer` — HaMeR (Hand Mesh Recovery) |
| **Paper** | `https://arxiv.org/abs/2312.05251` |
| **Dataset** | FREIHAND-VAL |
| **Eval command** | `python eval.py --dataset FREIHAND-VAL --batch_size 16 --num_workers 8` |
| **Accuracy metrics** | PA-MPJPE (6.0 baseline), PA-MPVPE (5.7 baseline), F@5mm, F@15mm (0.990 baseline) |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `ELECTRICITY_MAPS_API_KEY` | Auth token for the Electricity Maps carbon intensity API |
| `RUNPOD_API_KEY` | Auth token for RunPod GPU cloud API |
