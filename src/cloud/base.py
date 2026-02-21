"""Abstract cloud provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Instance:
    id: str
    gpu_type: str
    image_uri: str
    status: str
    ip_address: str | None = None
    ssh_port: int | None = None


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class CloudProvider(ABC):
    """Abstract base class for cloud GPU providers."""

    @abstractmethod
    def create_instance(self, gpu_type: str, image_uri: str) -> Instance:
        """Create a cloud GPU instance with the given image.

        Returns an Instance with connection details.
        """
        ...

    @abstractmethod
    def run_command(self, instance: Instance, command: str) -> CommandResult:
        """Execute a command on the remote instance.

        Returns the command result with stdout/stderr.
        """
        ...

    @abstractmethod
    def get_logs(self, instance: Instance) -> str:
        """Fetch all logs from the instance (stdout + stderr)."""
        ...

    @abstractmethod
    def terminate_instance(self, instance: Instance) -> None:
        """Stop and delete the cloud instance."""
        ...

    @abstractmethod
    def wait_until_ready(self, instance: Instance, timeout: int = 300) -> Instance:
        """Wait until the instance is ready to accept commands.

        Returns an updated Instance with connection details.
        """
        ...
