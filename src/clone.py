"""Step 1: Repo downloading/cloning."""

import logging
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _is_huggingface_url(url: str) -> bool:
    parsed = urlparse(url)
    return "huggingface.co" in parsed.netloc or "hf.co" in parsed.netloc


def download_repo(url: str, dest: str, branch: str = "main") -> Path:
    """Clone a git repo to the destination directory.

    Supports GitHub and HuggingFace URLs. For HuggingFace model repos,
    falls back to huggingface_hub if git clone fails.

    Returns the path to the cloned repo.
    """
    dest_path = Path(dest)

    # Clean destination if it already exists
    if dest_path.exists():
        logger.info("Removing existing directory: %s", dest_path)
        shutil.rmtree(dest_path)

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_huggingface_url(url):
        return _clone_huggingface(url, dest_path)

    return _clone_git(url, dest_path, branch)


def _clone_git(url: str, dest_path: Path, branch: str = "main") -> Path:
    """Clone a git repository with submodules."""
    logger.info("Cloning %s (branch: %s) to %s", url, branch, dest_path)

    cmd = [
        "git", "clone",
        "--recursive",
        "--branch", branch,
        "--depth", "1",
        url,
        str(dest_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}):\n{result.stderr}"
        )

    logger.info("Successfully cloned to %s", dest_path)
    return dest_path


def _clone_huggingface(url: str, dest_path: Path) -> Path:
    """Clone a HuggingFace repo using huggingface_hub."""
    from huggingface_hub import snapshot_download

    # Extract repo_id from URL (e.g., "username/model-name")
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) >= 2:
        repo_id = "/".join(path_parts[:2])
    else:
        repo_id = parsed.path.strip("/")

    logger.info("Downloading HuggingFace repo %s to %s", repo_id, dest_path)

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest_path),
    )

    logger.info("Successfully downloaded to %s", dest_path)
    return dest_path


def copy_repo(src: Path, dest: Path) -> Path:
    """Copy a cloned repo to another directory for modification."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    logger.info("Copied repo from %s to %s", src, dest)
    return dest
