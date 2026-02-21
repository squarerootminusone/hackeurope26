"""Container registry push (Docker Hub / cloud registry)."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def push_image(local_tag: str, config: dict) -> str:
    """Tag and push a Docker image to the configured container registry.

    Returns the full remote image URI (e.g., "username/ml-bench:original").
    """
    registry_type = config.get("cloud", {}).get("container_registry", "dockerhub")
    registry_prefix = config.get("cloud", {}).get("registry_prefix", "")

    if not registry_prefix:
        raise ValueError("registry_prefix must be set in config.cloud")

    remote_tag = f"{registry_prefix}/{local_tag}"

    logger.info("Pushing %s -> %s (registry: %s)", local_tag, remote_tag, registry_type)

    # Tag the image
    _run_docker(["docker", "tag", local_tag, remote_tag])

    # Push to registry
    _run_docker(["docker", "push", remote_tag])

    logger.info("Successfully pushed: %s", remote_tag)
    return remote_tag


def _run_docker(cmd: list[str]) -> str:
    """Run a docker command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Docker command failed: {' '.join(cmd)}\n{result.stderr}"
        )

    return result.stdout
