"""Step 7: Report generation."""

import json
import logging
from dataclasses import asdict
from pathlib import Path

from src.analyzer import OptimizationPlan
from src.benchmark import BenchmarkResults, GPUBenchmarkResult
from src.metrics import RunMetrics

logger = logging.getLogger(__name__)


def generate(
    results: BenchmarkResults,
    plan: OptimizationPlan,
    config: dict,
) -> Path:
    """Generate a comparison report from benchmark results.

    Writes output/report.md with summary tables, per-optimization breakdown,
    speedup ratios, accuracy deltas, and raw metrics appendix.
    """
    lines = []

    # Header
    lines.append("# ML Model Optimization Benchmark Report\n")
    lines.append(f"\n**Repository**: {plan.repo_name}\n")
    lines.append(f"**Architecture**: {plan.model_architecture}\n")
    lines.append(f"**Framework**: {plan.framework}\n")
    lines.append(f"**Original Image**: `{results.original_image}`\n")
    lines.append(f"**Optimized Image**: `{results.optimized_image}`\n")

    # Summary table
    lines.append("\n---\n")
    lines.append("\n## Summary\n")
    lines.append(_build_summary_table(results, config))

    # Accuracy comparison
    lines.append("\n## Accuracy Comparison\n")
    lines.append(_build_accuracy_table(results, config))

    # Optimization breakdown
    lines.append("\n## Optimizations Applied\n")
    lines.append(_build_optimization_breakdown(plan))

    # Detailed metrics per GPU
    lines.append("\n## Detailed Metrics\n")
    for gpu_result in results.gpu_results:
        lines.append(f"\n### {gpu_result.gpu_type}\n")
        lines.append(_build_detailed_table(gpu_result))

    # Raw metrics appendix
    lines.append("\n---\n")
    lines.append("\n## Appendix: Raw Metrics\n")
    lines.append(_build_raw_metrics(results))

    report_content = "".join(lines)

    # Write report
    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.md"
    report_path.write_text(report_content)
    logger.info("Report written to %s", report_path)

    # Also save structured results as JSON
    json_path = output_dir / "results.json"
    json_path.write_text(json.dumps(asdict(results), indent=2, default=str))
    logger.info("Raw results saved to %s", json_path)

    return report_path


def _avg_metric(runs: list[RunMetrics], getter) -> float:
    """Compute the average of a metric across multiple runs."""
    values = [getter(r) for r in runs if getter(r) is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _build_summary_table(results: BenchmarkResults, config: dict) -> str:
    """Build the main summary comparison table."""
    lines = [
        "\n| GPU | Version | Wall Time (s) | GPU Util (%) | GPU Mem (MB) | Power (W) | Speedup |\n",
        "| --- | ------- | ------------- | ------------ | ------------ | --------- | ------- |\n",
    ]

    for gpu_result in results.gpu_results:
        orig_time = _avg_metric(
            gpu_result.original_runs, lambda r: r.wall_time_ms
        ) / 1000
        opt_time = _avg_metric(
            gpu_result.optimized_runs, lambda r: r.wall_time_ms
        ) / 1000
        orig_util = _avg_metric(
            gpu_result.original_runs, lambda r: r.gpu.avg_utilization_pct
        )
        opt_util = _avg_metric(
            gpu_result.optimized_runs, lambda r: r.gpu.avg_utilization_pct
        )
        orig_mem = _avg_metric(
            gpu_result.original_runs, lambda r: r.gpu.max_memory_used_mb
        )
        opt_mem = _avg_metric(
            gpu_result.optimized_runs, lambda r: r.gpu.max_memory_used_mb
        )
        orig_power = _avg_metric(
            gpu_result.original_runs, lambda r: r.gpu.avg_power_draw_w
        )
        opt_power = _avg_metric(
            gpu_result.optimized_runs, lambda r: r.gpu.avg_power_draw_w
        )

        speedup = orig_time / opt_time if opt_time > 0 else 0

        lines.append(
            f"| {gpu_result.gpu_type} | Original | {orig_time:.1f} | "
            f"{orig_util:.1f} | {orig_mem:.0f} | {orig_power:.0f} | - |\n"
        )
        lines.append(
            f"| | Optimized | {opt_time:.1f} | "
            f"{opt_util:.1f} | {opt_mem:.0f} | {opt_power:.0f} | "
            f"**{speedup:.2f}x** |\n"
        )

    return "".join(lines)


def _build_accuracy_table(results: BenchmarkResults, config: dict) -> str:
    """Build the accuracy comparison table."""
    accuracy_metrics = config.get("target", {}).get("accuracy_metrics", [])
    paper_baselines = config.get("target", {}).get("paper_baselines", {})

    if not accuracy_metrics:
        return "\n*No accuracy metrics configured.*\n"

    # Header
    header = "| Metric | Paper Baseline | Original | Optimized | Delta |\n"
    sep = "| ------ | -------------- | -------- | --------- | ----- |\n"
    lines = ["\n", header, sep]

    # Use the first GPU result (or average across all)
    for metric_key in accuracy_metrics:
        baseline = paper_baselines.get(metric_key, "-")

        # Average across all GPU results and runs
        orig_values = []
        opt_values = []
        for gpu_result in results.gpu_results:
            for run in gpu_result.original_runs:
                if metric_key in run.accuracy:
                    orig_values.append(run.accuracy[metric_key])
            for run in gpu_result.optimized_runs:
                if metric_key in run.accuracy:
                    opt_values.append(run.accuracy[metric_key])

        orig_avg = sum(orig_values) / len(orig_values) if orig_values else None
        opt_avg = sum(opt_values) / len(opt_values) if opt_values else None

        orig_str = f"{orig_avg:.4f}" if orig_avg is not None else "N/A"
        opt_str = f"{opt_avg:.4f}" if opt_avg is not None else "N/A"

        if orig_avg is not None and opt_avg is not None:
            delta = opt_avg - orig_avg
            delta_str = f"{delta:+.4f}"
        else:
            delta_str = "N/A"

        lines.append(
            f"| {metric_key} | {baseline} | {orig_str} | {opt_str} | {delta_str} |\n"
        )

    return "".join(lines)


def _build_optimization_breakdown(plan: OptimizationPlan) -> str:
    """Build the optimization breakdown section."""
    lines = ["\n"]

    for i, opt in enumerate(plan.optimizations, 1):
        lines.append(f"\n### {i}. {opt.name}\n")
        lines.append(f"\n- **Category**: {opt.category}\n")
        lines.append(f"- **Description**: {opt.description}\n")
        lines.append(f"- **Files**: {', '.join(opt.files)}\n")
        lines.append(f"- **Expected Speedup**: {opt.expected_speedup}\n")
        lines.append(f"- **Expected Accuracy Impact**: {opt.expected_accuracy_impact}\n")

    return "".join(lines)


def _build_detailed_table(gpu_result: GPUBenchmarkResult) -> str:
    """Build detailed per-run metrics table for a GPU type."""
    lines = [
        "\n| Run | Version | Wall Time (s) | GPU Util (%) | GPU Mem Peak (MB) | "
        "CPU Util (%) | RAM Peak (MB) | Power (W) | Temp (C) |\n",
        "| --- | ------- | ------------- | ------------ | ----------------- | "
        "----------- | ------------- | --------- | -------- |\n",
    ]

    for i, run in enumerate(gpu_result.original_runs, 1):
        lines.append(
            f"| {i} | Original | {run.wall_time_ms / 1000:.1f} | "
            f"{run.gpu.avg_utilization_pct:.1f} | {run.gpu.max_memory_used_mb:.0f} | "
            f"{run.cpu.avg_utilization_pct:.1f} | {run.cpu.max_ram_used_mb:.0f} | "
            f"{run.gpu.avg_power_draw_w:.0f} | {run.gpu.max_temperature_c:.0f} |\n"
        )

    for i, run in enumerate(gpu_result.optimized_runs, 1):
        lines.append(
            f"| {i} | Optimized | {run.wall_time_ms / 1000:.1f} | "
            f"{run.gpu.avg_utilization_pct:.1f} | {run.gpu.max_memory_used_mb:.0f} | "
            f"{run.cpu.avg_utilization_pct:.1f} | {run.cpu.max_ram_used_mb:.0f} | "
            f"{run.gpu.avg_power_draw_w:.0f} | {run.gpu.max_temperature_c:.0f} |\n"
        )

    return "".join(lines)


def _build_raw_metrics(results: BenchmarkResults) -> str:
    """Build the raw metrics appendix."""
    lines = ["\n```json\n"]
    lines.append(json.dumps(asdict(results), indent=2, default=str))
    lines.append("\n```\n")
    return "".join(lines)
