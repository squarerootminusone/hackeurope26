"""Step 6: Orchestrates remote benchmarking."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.cloud.base import CloudProvider, Instance
from src.cloud.registry import push_image
from src.cloud.runpod import RunPodProvider
from src.metrics import RunMetrics, parse_metrics

logger = logging.getLogger(__name__)


@dataclass
class GPUBenchmarkResult:
    gpu_type: str
    original_runs: list[RunMetrics] = field(default_factory=list)
    optimized_runs: list[RunMetrics] = field(default_factory=list)


@dataclass
class BenchmarkResults:
    gpu_results: list[GPUBenchmarkResult] = field(default_factory=list)
    original_image: str = ""
    optimized_image: str = ""


def run(original_image: str, optimized_image: str, config: dict) -> BenchmarkResults:
    """Run benchmarks on cloud GPUs for both original and optimized images.

    Pushes both images to the registry, then for each GPU type:
    1. Creates an instance with the original image, runs benchmarks
    2. Creates an instance with the optimized image, runs benchmarks
    3. Collects and returns all metrics
    """
    cloud_config = config.get("cloud", {})
    bench_config = config.get("benchmark", {})
    gpu_types = cloud_config.get("gpu_types", [])
    runs_per_config = bench_config.get("runs_per_config", 3)
    warmup_runs = bench_config.get("warmup_runs", 1)
    timeout_minutes = bench_config.get("timeout_minutes", 60)

    # Push images to registry
    logger.info("Pushing images to container registry")
    original_uri = push_image(original_image, config)
    optimized_uri = push_image(optimized_image, config)

    # Create cloud provider
    provider = _create_provider(config)

    results = BenchmarkResults(
        original_image=original_uri,
        optimized_image=optimized_uri,
    )

    for gpu_type in gpu_types:
        logger.info("Starting benchmark on %s", gpu_type)
        gpu_result = GPUBenchmarkResult(gpu_type=gpu_type)

        # Benchmark original
        try:
            original_metrics = _benchmark_image(
                provider, gpu_type, original_uri,
                runs_per_config, warmup_runs, timeout_minutes, config,
            )
            gpu_result.original_runs = original_metrics
        except Exception as e:
            logger.error("Original benchmark failed on %s: %s", gpu_type, e)

        # Benchmark optimized
        try:
            optimized_metrics = _benchmark_image(
                provider, gpu_type, optimized_uri,
                runs_per_config, warmup_runs, timeout_minutes, config,
            )
            gpu_result.optimized_runs = optimized_metrics
        except Exception as e:
            logger.error("Optimized benchmark failed on %s: %s", gpu_type, e)

        results.gpu_results.append(gpu_result)

    return results


def _create_provider(config: dict) -> CloudProvider:
    """Create the cloud provider based on config."""
    provider_name = config.get("cloud", {}).get("provider", "runpod")

    if provider_name == "runpod":
        return RunPodProvider(config)

    raise ValueError(f"Unknown cloud provider: {provider_name}")


def _benchmark_image(
    provider: CloudProvider,
    gpu_type: str,
    image_uri: str,
    runs_per_config: int,
    warmup_runs: int,
    timeout_minutes: int,
    config: dict,
) -> list[RunMetrics]:
    """Run benchmarks for a single image on a specific GPU type.

    Creates an instance, runs warmup + benchmark runs, collects metrics.
    """
    instance = None
    try:
        # Create and wait for instance
        instance = provider.create_instance(gpu_type, image_uri)
        instance = provider.wait_until_ready(instance, timeout=timeout_minutes * 60)

        all_metrics = []

        total_runs = warmup_runs + runs_per_config
        for run_idx in range(total_runs):
            is_warmup = run_idx < warmup_runs
            run_type = "warmup" if is_warmup else f"benchmark {run_idx - warmup_runs + 1}"
            logger.info("Running %s on %s (%s)", run_type, gpu_type, image_uri)

            # The entrypoint script handles the eval command and metrics collection
            # We just need to run the container and collect logs
            result = provider.run_command(
                instance,
                "/workspace/benchmark_entrypoint.sh",
            )

            if result.exit_code != 0:
                logger.warning(
                    "Run %s failed (exit %d): %s",
                    run_type, result.exit_code, result.stderr[:500],
                )

            # Parse metrics from the output
            logs = result.stdout + "\n" + result.stderr
            metrics = parse_metrics(logs, config)

            # Only keep non-warmup runs
            if not is_warmup:
                all_metrics.append(metrics)

                # Save raw logs
                _save_run_logs(gpu_type, image_uri, run_idx - warmup_runs, logs)

        return all_metrics

    finally:
        if instance:
            try:
                provider.terminate_instance(instance)
            except Exception as e:
                logger.error("Failed to terminate instance: %s", e)


def _save_run_logs(
    gpu_type: str,
    image_uri: str,
    run_idx: int,
    logs: str,
) -> None:
    """Save raw benchmark logs for debugging."""
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    gpu_slug = gpu_type.replace(" ", "_").lower()
    image_slug = "original" if "original" in image_uri else "optimized"
    filename = f"benchmark_{gpu_slug}_{image_slug}_run{run_idx}.log"

    (log_dir / filename).write_text(logs)
    logger.info("Saved benchmark logs to %s", filename)
