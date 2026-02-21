"""RunPod cloud provider implementation."""

import logging
import time

import runpod

from src.cloud.base import CloudProvider, CommandResult, Instance

logger = logging.getLogger(__name__)

# Mapping from friendly GPU names to RunPod GPU IDs
GPU_TYPE_MAP = {
    "NVIDIA A100 80GB": "NVIDIA A100 80GB PCIe",
    "NVIDIA L40S": "NVIDIA L40S",
    "NVIDIA RTX 4090": "NVIDIA GeForce RTX 4090",
    "NVIDIA A100": "NVIDIA A100 80GB PCIe",
    "NVIDIA H100": "NVIDIA H100 80GB HBM3",
}


class RunPodProvider(CloudProvider):
    """RunPod cloud GPU provider.

    Uses the RunPod Python SDK to create and manage GPU pods.
    Requires RUNPOD_API_KEY environment variable to be set.
    """

    def __init__(self, config: dict):
        self.config = config
        self.region = config.get("cloud", {}).get("region", "US")

    def create_instance(self, gpu_type: str, image_uri: str) -> Instance:
        """Create a RunPod pod with the specified GPU and Docker image."""
        gpu_id = GPU_TYPE_MAP.get(gpu_type, gpu_type)
        logger.info("Creating RunPod pod: GPU=%s, image=%s", gpu_id, image_uri)

        pod = runpod.create_pod(
            name=f"ml-bench-{gpu_type.replace(' ', '-').lower()}",
            image_name=image_uri,
            gpu_type_id=gpu_id,
            gpu_count=1,
            volume_in_gb=50,
            container_disk_in_gb=50,
            ports="22/tcp,8888/http",
            docker_args="",
        )

        instance = Instance(
            id=pod["id"],
            gpu_type=gpu_type,
            image_uri=image_uri,
            status="starting",
        )

        logger.info("Created pod: %s", instance.id)
        return instance

    def run_command(self, instance: Instance, command: str) -> CommandResult:
        """Execute a command on the pod via RunPod's exec API."""
        logger.info("Running command on pod %s: %s", instance.id, command[:100])

        try:
            result = runpod.run_pod_command(instance.id, command)
            return CommandResult(
                exit_code=result.get("exit_code", 0),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
            )
        except Exception as e:
            logger.error("Command execution failed: %s", e)
            return CommandResult(exit_code=1, stdout="", stderr=str(e))

    def get_logs(self, instance: Instance) -> str:
        """Fetch logs from the RunPod pod."""
        try:
            pod = runpod.get_pod(instance.id)
            return pod.get("runtime", {}).get("logs", "")
        except Exception as e:
            logger.error("Failed to get logs: %s", e)
            return f"Error fetching logs: {e}"

    def terminate_instance(self, instance: Instance) -> None:
        """Terminate and delete the RunPod pod."""
        logger.info("Terminating pod: %s", instance.id)
        try:
            runpod.terminate_pod(instance.id)
            logger.info("Pod terminated: %s", instance.id)
        except Exception as e:
            logger.error("Failed to terminate pod %s: %s", instance.id, e)

    def wait_until_ready(self, instance: Instance, timeout: int = 300) -> Instance:
        """Wait until the pod is running and ready."""
        logger.info("Waiting for pod %s to be ready (timeout=%ds)", instance.id, timeout)
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                pod = runpod.get_pod(instance.id)
                status = pod.get("desiredStatus", "")
                runtime = pod.get("runtime", {})

                if status == "RUNNING" and runtime:
                    # Extract connection details
                    ports = runtime.get("ports", [])
                    ssh_port = None
                    ip_address = None
                    for port in ports:
                        if port.get("privatePort") == 22:
                            ip_address = port.get("ip")
                            ssh_port = port.get("publicPort")

                    instance.status = "running"
                    instance.ip_address = ip_address
                    instance.ssh_port = ssh_port
                    logger.info("Pod %s is ready (ip=%s)", instance.id, ip_address)
                    return instance

                logger.debug("Pod %s status: %s", instance.id, status)

            except Exception as e:
                logger.debug("Error checking pod status: %s", e)

            time.sleep(10)

        raise TimeoutError(
            f"Pod {instance.id} did not become ready within {timeout}s"
        )
