"""Step 2 & 5: Docker container building (original + optimized)."""

import logging
import shutil
import subprocess
from pathlib import Path

import jinja2

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """Raised when Docker build fails after all retries."""
    pass


def build(repo_path: str | Path, tag: str, config: dict) -> str:
    """Build a Docker image from the repo.

    If the repo has a Dockerfile, uses it. Otherwise, auto-generates one
    from the Jinja2 template. Injects the benchmark entrypoint script.
    On failure, uses Claude Code API to diagnose and fix, retrying up to max_retries.

    Returns the Docker image tag.
    """
    repo_path = Path(repo_path)
    max_retries = config.get("optimization", {}).get("max_retries", 3)
    image_tag = f"ml-bench:{tag}"

    # Ensure benchmark entrypoint is in the repo
    _inject_entrypoint(repo_path, config)

    # Check for existing Dockerfile
    if not _has_dockerfile(repo_path):
        _generate_dockerfile(repo_path, config)

    # Build with retries
    for attempt in range(1, max_retries + 1):
        logger.info("Docker build attempt %d/%d for %s", attempt, max_retries, image_tag)
        success, error_log = _docker_build(repo_path, image_tag)

        if success:
            logger.info("Successfully built image: %s", image_tag)
            return image_tag

        logger.warning("Build attempt %d failed:\n%s", attempt, error_log[-2000:])

        if attempt < max_retries:
            _fix_build(repo_path, error_log, attempt)

    raise BuildError(
        f"Docker build failed after {max_retries} attempts for tag '{tag}'"
    )


def push(image_tag: str, config: dict) -> str:
    """Push a Docker image to the configured container registry.

    Returns the full remote image URI.
    """
    registry_prefix = config["cloud"]["registry_prefix"]
    remote_tag = f"{registry_prefix}/{image_tag}"

    logger.info("Tagging %s as %s", image_tag, remote_tag)
    subprocess.run(
        ["docker", "tag", image_tag, remote_tag],
        check=True,
        capture_output=True,
        text=True,
    )

    logger.info("Pushing %s", remote_tag)
    result = subprocess.run(
        ["docker", "push", remote_tag],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Docker push failed:\n{result.stderr}")

    logger.info("Successfully pushed: %s", remote_tag)
    return remote_tag


def _has_dockerfile(repo_path: Path) -> bool:
    """Check if the repo already has a Dockerfile."""
    candidates = [
        repo_path / "Dockerfile",
        repo_path / "docker" / "Dockerfile",
    ]
    return any(c.exists() for c in candidates)


def _generate_dockerfile(repo_path: Path, config: dict) -> None:
    """Auto-generate a Dockerfile from the Jinja2 template."""
    logger.info("No Dockerfile found, generating from template")

    template_path = Path("templates/Dockerfile.base.j2")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path.parent),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path.name)

    # Detect project setup
    context = _detect_project_setup(repo_path, config)

    dockerfile_content = template.render(**context)
    dockerfile_path = repo_path / "Dockerfile"
    dockerfile_path.write_text(dockerfile_content)
    logger.info("Generated Dockerfile at %s", dockerfile_path)


def _detect_project_setup(repo_path: Path, config: dict) -> dict:
    """Inspect the repo to determine build context for Dockerfile generation."""
    context = {
        "base_image": "nvidia/cuda:11.7.1-devel-ubuntu22.04",
        "setup_commands": [],
        "pip_requirements": None,
        "extra_install": [],
    }

    # Check for requirements.txt
    if (repo_path / "requirements.txt").exists():
        context["pip_requirements"] = "requirements.txt"

    # Check for setup.py or pyproject.toml
    if (repo_path / "setup.py").exists():
        context["setup_commands"].append("pip install -e .[all] 2>/dev/null || pip install -e .")
    elif (repo_path / "pyproject.toml").exists():
        context["setup_commands"].append("pip install -e .[all] 2>/dev/null || pip install -e .")

    # Check for submodules (like ViTPose in HaMeR)
    if (repo_path / ".gitmodules").exists():
        context["extra_install"].append("git submodule update --init --recursive")
        # Check for third-party dirs with their own setup
        for third_party in (repo_path / "third-party").iterdir() if (repo_path / "third-party").exists() else []:
            if (third_party / "setup.py").exists() or (third_party / "pyproject.toml").exists():
                rel = third_party.relative_to(repo_path)
                context["extra_install"].append(f"pip install -v -e {rel}")

    # Check for data fetch scripts
    for script_name in ["fetch_demo_data.sh", "download_data.sh", "setup.sh"]:
        if (repo_path / script_name).exists():
            context["extra_install"].append(f"bash {script_name}")

    return context


def _inject_entrypoint(repo_path: Path, config: dict) -> None:
    """Render and inject the benchmark entrypoint script into the repo."""
    template_path = Path("templates/benchmark_entrypoint.sh.j2")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path.parent),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path.name)

    eval_command = config.get("target", {}).get(
        "eval_command",
        "echo 'No eval command configured'"
    )

    content = template.render(eval_command=eval_command)
    entrypoint_path = repo_path / "benchmark_entrypoint.sh"
    entrypoint_path.write_text(content)
    entrypoint_path.chmod(0o755)
    logger.info("Injected benchmark entrypoint at %s", entrypoint_path)


def _docker_build(repo_path: Path, image_tag: str) -> tuple[bool, str]:
    """Run docker build. Returns (success, error_log)."""
    # Find the Dockerfile
    if (repo_path / "Dockerfile").exists():
        dockerfile = repo_path / "Dockerfile"
    elif (repo_path / "docker" / "Dockerfile").exists():
        dockerfile = repo_path / "docker" / "Dockerfile"
    else:
        return False, "No Dockerfile found in repo"

    cmd = [
        "docker", "build",
        "-t", image_tag,
        "-f", str(dockerfile),
        str(repo_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 minute timeout
    )

    if result.returncode == 0:
        return True, ""

    return False, result.stdout + "\n" + result.stderr


def _fix_build(repo_path: Path, error_log: str, attempt: int) -> None:
    """Use Claude Code API to diagnose and fix Docker build failures."""
    logger.info("Invoking Claude to fix build (attempt %d)", attempt)

    # Save error log for reference
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"build_error_attempt_{attempt}.log").write_text(error_log)

    # Truncate error log to last 3000 chars for the prompt
    error_tail = error_log[-3000:] if len(error_log) > 3000 else error_log

    prompt = (
        f"The Docker build failed on attempt {attempt} with this error:\n"
        f"```\n{error_tail}\n```\n"
        f"Fix the Dockerfile and/or code so it builds successfully. "
        f"Only make minimal, targeted changes to resolve the build error."
    )

    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        logger.warning("Claude fix attempt failed: %s", result.stderr)
    else:
        logger.info("Claude applied fixes for build attempt %d", attempt)
