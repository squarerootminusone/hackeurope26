"""Metrics collection & parsing (nvidia-smi, wall time, accuracy)."""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GPUMetrics:
    avg_utilization_pct: float = 0.0
    max_utilization_pct: float = 0.0
    avg_memory_used_mb: float = 0.0
    max_memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    avg_power_draw_w: float = 0.0
    max_power_draw_w: float = 0.0
    avg_temperature_c: float = 0.0
    max_temperature_c: float = 0.0
    gpu_name: str = ""


@dataclass
class CPUMetrics:
    avg_utilization_pct: float = 0.0
    max_utilization_pct: float = 0.0
    avg_ram_used_mb: float = 0.0
    max_ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0


@dataclass
class RunMetrics:
    wall_time_ms: int = 0
    eval_exit_code: int = 0
    gpu: GPUMetrics = field(default_factory=GPUMetrics)
    cpu: CPUMetrics = field(default_factory=CPUMetrics)
    accuracy: dict[str, float] = field(default_factory=dict)
    raw_eval_output: str = ""


def parse_metrics(logs: str, config: dict) -> RunMetrics:
    """Parse the collected benchmark logs into structured metrics.

    Expects the format produced by benchmark_entrypoint.sh:
    - GPU metrics CSV from nvidia-smi
    - CPU metrics CSV from mpstat/free
    - Wall time and exit code markers
    - Eval command stdout for accuracy extraction
    """
    metrics = RunMetrics()

    # Extract wall time
    wall_time_match = re.search(r"EVAL_WALL_TIME_MS:\s*(\d+)", logs)
    if wall_time_match:
        metrics.wall_time_ms = int(wall_time_match.group(1))

    # Extract exit code
    exit_code_match = re.search(r"EVAL_EXIT_CODE:\s*(\d+)", logs)
    if exit_code_match:
        metrics.eval_exit_code = int(exit_code_match.group(1))

    # Extract eval output (between EVAL COMMAND START and EVAL COMMAND END)
    eval_match = re.search(
        r"=== EVAL COMMAND START ===\n(.*?)\n=== EVAL COMMAND END ===",
        logs,
        re.DOTALL,
    )
    if eval_match:
        metrics.raw_eval_output = eval_match.group(1)

    # Parse GPU metrics
    metrics.gpu = _parse_gpu_metrics(logs)

    # Parse CPU metrics
    metrics.cpu = _parse_cpu_metrics(logs)

    # Extract accuracy metrics
    accuracy_metric_keys = config.get("target", {}).get("accuracy_metrics", [])
    for key in accuracy_metric_keys:
        value = extract_accuracy(metrics.raw_eval_output or logs, key)
        if value is not None:
            metrics.accuracy[key] = value

    return metrics


def extract_accuracy(eval_output: str, metric_key: str) -> float | None:
    """Extract an accuracy metric value from eval command output.

    Looks for patterns like:
    - "PA-MPJPE: 6.0"
    - "PA-MPJPE = 6.0"
    - "PA-MPJPE 6.0"
    - "pa_mpjpe: 6.0"
    """
    # Normalize the key for flexible matching
    key_pattern = re.escape(metric_key).replace(r"\-", r"[-_]")

    patterns = [
        rf"{key_pattern}\s*[:=]\s*([\d.]+)",
        rf"{key_pattern}\s+([\d.]+)",
        rf'"{metric_key}"\s*[:=]\s*([\d.]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, eval_output, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue

    logger.warning("Could not extract metric '%s' from eval output", metric_key)
    return None


def _parse_gpu_metrics(logs: str) -> GPUMetrics:
    """Parse GPU metrics from nvidia-smi CSV output."""
    gpu = GPUMetrics()

    # Extract GPU metrics section
    gpu_section_match = re.search(
        r"=== GPU METRICS ===\n(.*?)(?:=== |$)", logs, re.DOTALL
    )
    if not gpu_section_match:
        logger.warning("No GPU metrics section found in logs")
        return gpu

    csv_text = gpu_section_match.group(1).strip()
    lines = csv_text.strip().split("\n")

    if len(lines) < 2:
        return gpu

    # Skip header line
    utils = []
    mem_utils = []
    mem_used = []
    mem_total = []
    power = []
    temps = []
    gpu_name = ""

    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue

        try:
            # timestamp, gpu_name, util.gpu, util.memory, memory.used, memory.total, power.draw, temperature
            gpu_name = parts[1]
            utils.append(float(parts[2]))
            mem_utils.append(float(parts[3]))
            mem_used.append(float(parts[4]))
            mem_total.append(float(parts[5]))
            power.append(float(parts[6]))
            temps.append(float(parts[7]))
        except (ValueError, IndexError):
            continue

    if utils:
        gpu.gpu_name = gpu_name
        gpu.avg_utilization_pct = sum(utils) / len(utils)
        gpu.max_utilization_pct = max(utils)
        gpu.avg_memory_used_mb = sum(mem_used) / len(mem_used)
        gpu.max_memory_used_mb = max(mem_used)
        gpu.memory_total_mb = mem_total[0] if mem_total else 0
        gpu.avg_power_draw_w = sum(power) / len(power)
        gpu.max_power_draw_w = max(power)
        gpu.avg_temperature_c = sum(temps) / len(temps)
        gpu.max_temperature_c = max(temps)

    return gpu


def _parse_cpu_metrics(logs: str) -> CPUMetrics:
    """Parse CPU metrics from mpstat/free output."""
    cpu = CPUMetrics()

    # Extract CPU metrics section
    cpu_section_match = re.search(
        r"=== CPU METRICS ===\n(.*?)(?:=== |$)", logs, re.DOTALL
    )
    if not cpu_section_match:
        logger.warning("No CPU metrics section found in logs")
        return cpu

    csv_text = cpu_section_match.group(1).strip()
    lines = csv_text.strip().split("\n")

    cpu_utils = []
    ram_used = []
    ram_total = []

    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue

        try:
            # timestamp, cpu_util, ram_used_mb, ram_total_mb
            cpu_utils.append(float(parts[1]))
            ram_used.append(float(parts[2]))
            ram_total.append(float(parts[3]))
        except (ValueError, IndexError):
            continue

    if cpu_utils:
        cpu.avg_utilization_pct = sum(cpu_utils) / len(cpu_utils)
        cpu.max_utilization_pct = max(cpu_utils)
        cpu.avg_ram_used_mb = sum(ram_used) / len(ram_used)
        cpu.max_ram_used_mb = max(ram_used)
        cpu.ram_total_mb = ram_total[0] if ram_total else 0

    return cpu
