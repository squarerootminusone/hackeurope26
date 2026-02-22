"""GreenML Pipeline — Flask frontend server.

Provides a dashboard UI for:
  - Inputting a GitHub repo and SCI parameters
  - Simulating the optimization pipeline (mock, not yet wired to real scripts)
  - Displaying SCI scores for baseline vs optimised models
  - Showing the evaluations database
"""

import threading
import time
import uuid
import random
import math
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOURS_PER_YEAR = 8760

# Hardware embodied carbon (gCO2) by GPU type
GPU_TE_GCO2 = {
    "NVIDIA A100 80GB": 150_000,
    "NVIDIA L40S": 120_000,
    "NVIDIA RTX 4090": 85_000,
    "NVIDIA V100": 100_000,
    "NVIDIA T4": 70_000,
}

# Representative carbon intensities (gCO2eq/kWh) per Electricity Maps zone
ZONE_INTENSITY = {
    "DE": 350.0,    # Germany
    "FR": 85.0,     # France (nuclear)
    "GB": 225.0,    # United Kingdom
    "IE": 310.0,    # Ireland
    "SE": 45.0,     # Sweden (hydro/nuclear)
    "NO": 30.0,     # Norway (hydro)
    "PL": 680.0,    # Poland (coal-heavy)
    "US-CAL-CISO": 250.0,   # California
    "US-MISO": 530.0,       # Midwest US
    "US-NY-NYISO": 280.0,   # New York
    "IN-SO": 760.0,         # India South
    "CN": 620.0,            # China
    "AU-NSW": 750.0,        # Australia NSW
    "WORLD": 475.0,         # World average (fallback)
}

OPTIMISATIONS_APPLIED = [
    "torch.compile() on model forward pass (reduce-overhead mode)",
    "AMP bfloat16 autocast during inference",
    "Early stopping in IEF iteration loop (patience=10, min_rel_improvement=1e-4)",
    "pin_memory=True + persistent_workers on DataLoader",
    "torch.backends.cudnn.benchmark = True",
]

PIPELINE_STEPS = [
    ("clone",           "Cloning repository"),
    ("analyze",         "Analysing for optimisations"),
    ("optimize",        "Applying optimisations"),
    ("build_baseline",  "Building baseline Docker image"),
    ("build_optimized", "Building optimised Docker image"),
    ("eval_baseline",   "Running baseline evaluation"),
    ("eval_optimized",  "Running optimised evaluation"),
    ("sci_calc",        "Computing SCI scores"),
    ("report",          "Generating report"),
]

# Step durations in seconds (simulated)
STEP_DURATIONS = [2, 3, 4, 5, 5, 8, 8, 2, 1]

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

jobs: dict[str, dict] = {}

# Seed evaluations DB with some prior runs so the table isn't empty
# Schema matches infra/schema.sql:
#   id, evaluation_name, model_name, is_optimized, vm_reference,
#   instance_type, create_date, update_date, start_runtime_date, end_runtime_date
_SEED_EVALS = [
    {
        "id": 1,
        "evaluation_name": "hamer-baseline-freihand-v1",
        "model_name": "hamer",
        "is_optimized": False,
        "vm_reference": "gpu-a100-eu-west1-001",
        "instance_type": "NVIDIA A100 80GB",
        "create_date": "2026-02-20 14:00:00",
        "update_date": "2026-02-20 15:42:00",
        "start_runtime_date": "2026-02-20 14:05:00",
        "end_runtime_date": "2026-02-20 15:42:00",
    },
    {
        "id": 2,
        "evaluation_name": "hamer-optimized-freihand-v1",
        "model_name": "hamer",
        "is_optimized": True,
        "vm_reference": "gpu-a100-eu-west1-001",
        "instance_type": "NVIDIA A100 80GB",
        "create_date": "2026-02-20 16:00:00",
        "update_date": "2026-02-20 17:28:00",
        "start_runtime_date": "2026-02-20 16:05:00",
        "end_runtime_date": "2026-02-20 17:28:00",
    },
    {
        "id": 3,
        "evaluation_name": "hamer-baseline-freihand-v2",
        "model_name": "hamer",
        "is_optimized": False,
        "vm_reference": "gpu-l40s-us-west1-003",
        "instance_type": "NVIDIA L40S",
        "create_date": "2026-02-21 09:00:00",
        "update_date": "2026-02-21 10:51:00",
        "start_runtime_date": "2026-02-21 09:10:00",
        "end_runtime_date": "2026-02-21 10:51:00",
    },
    {
        "id": 4,
        "evaluation_name": "hamer-optimized-freihand-v2",
        "model_name": "hamer",
        "is_optimized": True,
        "vm_reference": "gpu-l40s-us-west1-003",
        "instance_type": "NVIDIA L40S",
        "create_date": "2026-02-21 11:00:00",
        "update_date": "2026-02-21 12:18:00",
        "start_runtime_date": "2026-02-21 11:08:00",
        "end_runtime_date": "2026-02-21 12:18:00",
    },
]

_eval_counter = len(_SEED_EVALS)
evaluations_db: list[dict] = list(_SEED_EVALS)


# ---------------------------------------------------------------------------
# SCI helpers
# ---------------------------------------------------------------------------

def _embodied_gco2(te_gco2: float, tir_hours: float, lifespan_years: float) -> float:
    """M = TE * (TiR / (Lifespan * 8760))"""
    return te_gco2 * (tir_hours / (lifespan_years * HOURS_PER_YEAR))


def _sci(energy_kwh: float, intensity: float, embodied: float, r: int) -> float:
    """SCI = (E * I + M) / R"""
    return (energy_kwh * intensity + embodied) / r


def _build_sci_results(params: dict) -> dict:
    """Generate mock SCI results using the real formula.

    Baseline: realistic energy for one evaluation run on a GPU.
    Optimised: apply reduction factors from known optimisations.
    """
    gpu_type = params.get("gpu_type", "NVIDIA A100 80GB")
    te_gco2 = GPU_TE_GCO2.get(gpu_type, 150_000)
    lifespan = float(params.get("lifespan_years", 4))
    zone = params.get("zone", "WORLD").upper()
    intensity = ZONE_INTENSITY.get(zone, 475.0)
    r = int(params.get("functional_units", 10_000))
    if r <= 0:
        r = 10_000

    # ---- Baseline (mock energy around 0.42-0.52 kWh per eval run) ----
    rng_seed = abs(hash(params.get("repo_url", "x"))) % 10_000
    rng = random.Random(rng_seed)
    baseline_energy = round(rng.uniform(0.42, 0.52), 6)       # kWh
    baseline_duration_h = round(rng.uniform(0.45, 0.60), 4)   # hours

    # ---- Optimised (reduce energy by 28-42%, duration by 25-38%) ----
    energy_reduction = round(rng.uniform(0.28, 0.42), 4)
    duration_reduction = round(rng.uniform(0.25, 0.38), 4)

    opt_energy = round(baseline_energy * (1 - energy_reduction), 6)
    opt_duration_h = round(baseline_duration_h * (1 - duration_reduction), 4)

    # Embodied
    baseline_M = _embodied_gco2(te_gco2, baseline_duration_h, lifespan)
    opt_M = _embodied_gco2(te_gco2, opt_duration_h, lifespan)

    # SCI
    baseline_sci = _sci(baseline_energy, intensity, baseline_M, r)
    opt_sci = _sci(opt_energy, intensity, opt_M, r)

    sci_reduction_pct = (baseline_sci - opt_sci) / baseline_sci * 100
    co2_baseline_total = baseline_energy * intensity + baseline_M   # gCO2
    co2_opt_total = opt_energy * intensity + opt_M                  # gCO2
    co2_saved = co2_baseline_total - co2_opt_total                  # gCO2

    return {
        "zone": zone,
        "intensity": round(intensity, 2),
        "gpu_type": gpu_type,
        "te_gco2": te_gco2,
        "lifespan_years": lifespan,
        "functional_units": r,
        "optimisations_applied": OPTIMISATIONS_APPLIED,
        "baseline": {
            "energy_kwh": baseline_energy,
            "duration_h": baseline_duration_h,
            "embodied_gco2": round(baseline_M, 4),
            "ei_gco2": round(baseline_energy * intensity, 4),
            "total_gco2": round(co2_baseline_total, 4),
            "sci": round(baseline_sci, 8),
        },
        "optimized": {
            "energy_kwh": opt_energy,
            "duration_h": opt_duration_h,
            "embodied_gco2": round(opt_M, 4),
            "ei_gco2": round(opt_energy * intensity, 4),
            "total_gco2": round(co2_opt_total, 4),
            "sci": round(opt_sci, 8),
        },
        "reduction": {
            "energy_pct": round(energy_reduction * 100, 1),
            "sci_pct": round(sci_reduction_pct, 1),
            "co2_saved_gco2": round(co2_saved, 4),
        },
    }


# ---------------------------------------------------------------------------
# Pipeline simulation (background thread)
# ---------------------------------------------------------------------------

def _simulate_pipeline(job_id: str):
    """Run through each pipeline step with a realistic delay."""
    global _eval_counter
    job = jobs[job_id]

    for idx, ((step_key, step_label), duration) in enumerate(
        zip(PIPELINE_STEPS, STEP_DURATIONS)
    ):
        if job.get("cancelled"):
            job["status"] = "cancelled"
            return

        job["current_step"] = idx
        job["steps"][idx]["status"] = "running"
        job["steps"][idx]["started_at"] = datetime.utcnow().isoformat()

        # Simulate work
        time.sleep(duration)

        job["steps"][idx]["status"] = "done"
        job["steps"][idx]["finished_at"] = datetime.utcnow().isoformat()

        # After eval steps, add records to evaluations DB (matches schema.sql)
        if step_key == "eval_baseline":
            _eval_counter += 1
            repo_name = job["params"].get("repo_url", "").rstrip("/").split("/")[-1]
            gpu = job["params"].get("gpu_type", "NVIDIA A100 80GB")
            now = datetime.utcnow()
            end = now + timedelta(hours=1, minutes=30)
            evaluations_db.append({
                "id": _eval_counter,
                "evaluation_name": f"{repo_name}-baseline-run{_eval_counter}",
                "model_name": repo_name,
                "is_optimized": False,
                "vm_reference": f"gpu-{gpu.lower().replace(' ', '-').replace('nvidia-', '')}-001",
                "instance_type": gpu,
                "create_date": now.strftime("%Y-%m-%d %H:%M:%S"),
                "update_date": end.strftime("%Y-%m-%d %H:%M:%S"),
                "start_runtime_date": (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "end_runtime_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            })

        elif step_key == "eval_optimized":
            _eval_counter += 1
            repo_name = job["params"].get("repo_url", "").rstrip("/").split("/")[-1]
            gpu = job["params"].get("gpu_type", "NVIDIA A100 80GB")
            now = datetime.utcnow()
            end = now + timedelta(hours=1, minutes=10)
            evaluations_db.append({
                "id": _eval_counter,
                "evaluation_name": f"{repo_name}-optimized-run{_eval_counter}",
                "model_name": repo_name,
                "is_optimized": True,
                "vm_reference": f"gpu-{gpu.lower().replace(' ', '-').replace('nvidia-', '')}-001",
                "instance_type": gpu,
                "create_date": now.strftime("%Y-%m-%d %H:%M:%S"),
                "update_date": end.strftime("%Y-%m-%d %H:%M:%S"),
                "start_runtime_date": (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "end_runtime_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            })

        elif step_key == "sci_calc":
            # Compute SCI results now
            job["sci_results"] = _build_sci_results(job["params"])

    job["status"] = "completed"
    job["finished_at"] = datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        gpu_types=list(GPU_TE_GCO2.keys()),
        zones=sorted(ZONE_INTENSITY.keys()),
    )


@app.route("/api/run", methods=["POST"])
def api_run():
    """Start a new pipeline job."""
    data = request.get_json(force=True)

    repo_url = data.get("repo_url", "").strip()
    if not repo_url:
        return jsonify({"error": "repo_url is required"}), 400

    job_id = str(uuid.uuid4())
    params = {
        "repo_url": repo_url,
        "branch": data.get("branch", "main").strip() or "main",
        "zone": (data.get("zone") or "WORLD").upper(),
        "gpu_type": data.get("gpu_type", "NVIDIA A100 80GB"),
        "lifespan_years": data.get("lifespan_years", 4),
        "functional_units": data.get("functional_units", 10_000),
        "api_key": data.get("api_key", ""),
    }

    steps = [
        {"key": key, "label": label, "status": "pending",
         "started_at": None, "finished_at": None}
        for key, label in PIPELINE_STEPS
    ]

    jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "params": params,
        "steps": steps,
        "current_step": -1,
        "sci_results": None,
        "created_at": datetime.utcnow().isoformat(),
        "finished_at": None,
        "cancelled": False,
    }

    thread = threading.Thread(target=_simulate_pipeline, args=(job_id,), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id: str):
    """Poll job status and results."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel(job_id: str):
    """Cancel a running job."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    job["cancelled"] = True
    return jsonify({"ok": True})


@app.route("/api/evaluations")
def api_evaluations():
    """Return the evaluations database (latest first)."""
    return jsonify(list(reversed(evaluations_db)))


@app.route("/api/jobs")
def api_jobs_list():
    """Return all jobs (for history)."""
    return jsonify(
        [
            {
                "id": j["id"],
                "repo_url": j["params"]["repo_url"],
                "status": j["status"],
                "created_at": j["created_at"],
            }
            for j in reversed(list(jobs.values()))
        ]
    )


if __name__ == "__main__":
    app.run(debug=True, port=5050, threaded=True)
