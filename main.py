"""ML Model Optimization Pipeline — Entry point / orchestrator."""

import argparse
import logging
import sys
from pathlib import Path

import yaml

from src import clone
from src import docker_builder
from src import analyzer
from src import optimizer
from src import benchmark
from src import report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/logs/pipeline.log"),
    ],
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load and validate the pipeline configuration."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    # Validate required fields
    required_keys = ["target", "optimization", "cloud", "benchmark"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing required config section: {key}")

    if "repo_url" not in config["target"]:
        raise ValueError("Missing required config: target.repo_url")

    return config


def main(config_path: str = "config.yaml"):
    """Run the full ML optimization pipeline."""
    # Ensure output directories exist
    for d in ["output/original", "output/optimized", "output/logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    config = load_config(config_path)
    target = config["target"]

    logger.info("=" * 60)
    logger.info("ML Model Optimization Pipeline")
    logger.info("Target repo: %s", target["repo_url"])
    logger.info("=" * 60)

    # Step 1: Clone the repo
    logger.info("Step 1: Cloning repository")
    repo_path = clone.download_repo(
        target["repo_url"],
        "output/original",
        branch=target.get("branch", "main"),
    )
    # Copy to optimized directory for modification
    optimized_path = clone.copy_repo(repo_path, Path("output/optimized"))
    logger.info("Step 1 complete: repo cloned to %s", repo_path)

    # Step 2: Build original container
    logger.info("Step 2: Building original Docker image")
    original_image = docker_builder.build(repo_path, tag="original", config=config)
    logger.info("Step 2 complete: %s", original_image)

    # Step 3: Analyze repo for optimizations
    logger.info("Step 3: Analyzing repository for optimizations")
    optimization_plan = analyzer.analyze(repo_path, config)
    logger.info(
        "Step 3 complete: %d optimizations identified",
        len(optimization_plan.optimizations),
    )

    # Step 4: Apply optimizations
    logger.info("Step 4: Applying optimizations")
    optimized_path = optimizer.optimize(optimized_path, optimization_plan, config)
    logger.info("Step 4 complete: optimizations applied to %s", optimized_path)

    # Step 5: Build optimized container
    logger.info("Step 5: Building optimized Docker image")
    optimized_image = docker_builder.build(
        optimized_path, tag="optimized", config=config
    )
    logger.info("Step 5 complete: %s", optimized_image)

    # Step 6: Push images & benchmark on cloud GPUs
    logger.info("Step 6: Running benchmarks on cloud GPUs")
    results = benchmark.run(original_image, optimized_image, config)
    logger.info("Step 6 complete: benchmarks finished")

    # Step 7: Generate report
    logger.info("Step 7: Generating comparison report")
    report_path = report.generate(results, optimization_plan, config)
    logger.info("Step 7 complete: report at %s", report_path)

    logger.info("=" * 60)
    logger.info("Pipeline complete!")
    logger.info("Report: %s", report_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ML Model Optimization Pipeline"
    )
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="Path to config YAML file (default: config.yaml)",
    )
    args = parser.parse_args()

    try:
        main(args.config)
    except Exception as e:
        logger.exception("Pipeline failed: %s", e)
        sys.exit(1)
