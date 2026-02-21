"""Step 2 & 5: Docker container building (original + optimized)."""

import logging
import os
import subprocess
from pathlib import Path

import jinja2

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """Raised when Docker build fails after all retries."""
    pass


def build(repo_path: str | Path, tag: str, config: dict) -> str:
    """Build a Docker image from the repo.

    If the repo has a Dockerfile, uses it (with entrypoint injection).
    Otherwise, auto-generates one from the Jinja2 template.
    On failure, uses Claude Code API to diagnose and fix, retrying up to max_retries.

    Returns the Docker image tag.
    """
    repo_path = Path(repo_path)
    max_retries = config.get("optimization", {}).get("max_retries", 3)
    image_tag = f"ml-bench:{tag}"

    # Ensure benchmark entrypoint is in the repo
    _inject_entrypoint(repo_path, config)

    # Use existing Dockerfile if available, otherwise generate one
    dockerfile_path = _find_dockerfile(repo_path)
    if dockerfile_path:
        logger.info("Using existing Dockerfile: %s", dockerfile_path)
        _adapt_existing_dockerfile(repo_path, dockerfile_path, config)
    else:
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


def _find_dockerfile(repo_path: Path) -> Path | None:
    """Find an existing Dockerfile in the repo."""
    # Check common locations
    candidates = [
        repo_path / "Dockerfile",
        repo_path / "docker" / "Dockerfile",
    ]

    # Also check for named Dockerfiles in docker/ dir
    docker_dir = repo_path / "docker"
    if docker_dir.is_dir():
        for f in docker_dir.iterdir():
            if f.name.endswith(".Dockerfile") or f.name.startswith("Dockerfile"):
                candidates.append(f)

    for c in candidates:
        if c.exists() and c.is_file():
            return c

    return None


def _adapt_existing_dockerfile(repo_path: Path, dockerfile_path: Path, config: dict) -> None:
    """Adapt an existing Dockerfile by appending benchmark entrypoint setup."""
    content = dockerfile_path.read_text()

    # Override base image if configured
    base_image = config.get("docker", {}).get("base_image")
    if base_image:
        import re
        content = re.sub(
            r'ARG BASE=\S+',
            f'ARG BASE={base_image}',
            content,
        )
        logger.info("Overriding base image to: %s", base_image)

        # If using a pre-built base image, skip torch/numpy/setuptools/gdown
        # installs since they're already present. Also skip venv creation.
        # Handle multiline RUN commands (with --mount and line continuations)
        skip_patterns = [
            # Multiline RUN with --mount (e.g., RUN --mount=... \\\n    pip install ...)
            r'RUN\s+--mount=\S+\s*\\\n\s*pip install[^\n]*(?:wheel|setuptools)[^\n]*\n',
            r'RUN\s+--mount=\S+\s*\\\n\s*pip install[^\n]*torch==[^\n]*\n',
            r'RUN\s+--mount=\S+\s*\\\n\s*pip install numpy\n',
            r'RUN\s+--mount=\S+\s*\\\n\s*pip install gdown\n',
            # Single-line RUN
            r'RUN\s+pip install[^\n]*(?:wheel|setuptools)[^\n]*\n',
            r'RUN\s+pip install[^\n]*torch==[^\n]*\n',
            r'RUN\s+pip install numpy\n',
            r'RUN\s+pip install gdown\n',
            # Venv setup
            r'RUN python3 -m venv /opt/venv\n',
            r'ENV PATH="/opt/venv/bin:\$PATH"\n',
            # Comment lines for removed steps
            r'# Create virtual environment:\n',
            r'# Add virtual environment to PATH\n',
            r'# REVIEW:.*\n',
            r'# Install torch and torchvision:\n',
            r'# Activate virtual environment and install dependencies:\n',
            r'# Install gdown[^\n]*:\n',
        ]
        for pattern in skip_patterns:
            content = re.sub(pattern, '', content)

        # Add --no-build-isolation to pip install -e commands so
        # detectron2's setup.py can find torch from the base image
        content = re.sub(
            r'pip install -e \.\[all\]',
            'pip install --no-build-isolation -e .[all]',
            content,
        )
        content = re.sub(
            r'pip install -v -e third-party/',
            'pip install --no-build-isolation -v -e third-party/',
            content,
        )

        # Clean up excessive blank lines
        content = re.sub(r'\n{3,}', '\n\n', content)

        logger.info("Stripped redundant install steps for pre-built base")

    # Append entrypoint injection if not already present
    if "benchmark_entrypoint" not in content:
        content += "\n\n# Benchmark entrypoint (injected by pipeline)\n"
        content += "RUN apt-get update && apt-get install -y --no-install-recommends sysstat procps && rm -rf /var/lib/apt/lists/*\n"
        content += "COPY benchmark_entrypoint.sh /workspace/benchmark_entrypoint.sh\n"
        content += "RUN chmod +x /workspace/benchmark_entrypoint.sh\n"
        content += 'ENTRYPOINT ["/workspace/benchmark_entrypoint.sh"]\n'

    # Write the adapted Dockerfile to repo root
    target = repo_path / "Dockerfile"
    target.write_text(content)
    logger.info("Adapted Dockerfile written to %s", target)


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
        "base_image": "nvidia/cuda:12.6.2-devel-ubuntu22.04",
        "torch_install": None,
        "pip_requirements": None,
        "pre_install_commands": [],
        "setup_commands": [],
        "submodule_installs": [],
        "post_install_commands": [],
    }

    # Detect if torch is needed (check setup.py, requirements.txt)
    needs_torch = _needs_torch(repo_path)
    if needs_torch:
        context["torch_install"] = (
            "pip install --no-cache-dir "
            "torch==2.2.0 torchvision==0.17.0 "
            "--index-url https://download.pytorch.org/whl/cu118"
        )

    # Check for requirements.txt
    if (repo_path / "requirements.txt").exists():
        context["pip_requirements"] = "requirements.txt"

    # Check for submodules and third-party deps (must install before main package)
    if (repo_path / ".gitmodules").exists():
        third_party_dir = repo_path / "third-party"
        if third_party_dir.exists() and third_party_dir.is_dir():
            for entry in sorted(third_party_dir.iterdir()):
                if entry.is_dir() and (
                    (entry / "setup.py").exists()
                    or (entry / "pyproject.toml").exists()
                ):
                    rel = entry.relative_to(repo_path)
                    context["submodule_installs"].append(
                        f"pip install --no-cache-dir -v -e {rel}"
                    )

    # Install gdown if fetch scripts exist (needed for Google Drive downloads)
    for script_name in ["fetch_demo_data.sh", "download_data.sh"]:
        if (repo_path / script_name).exists():
            context["pre_install_commands"].append(
                "pip install --no-cache-dir gdown"
            )
            break

    # Check for setup.py or pyproject.toml
    if (repo_path / "setup.py").exists():
        context["setup_commands"].append(
            "pip install --no-cache-dir -e .[all] 2>/dev/null || pip install --no-cache-dir -e ."
        )
    elif (repo_path / "pyproject.toml").exists():
        context["setup_commands"].append(
            "pip install --no-cache-dir -e .[all] 2>/dev/null || pip install --no-cache-dir -e ."
        )

    # Data fetch scripts (run after install)
    for script_name in ["fetch_demo_data.sh", "download_data.sh", "setup.sh"]:
        if (repo_path / script_name).exists():
            context["post_install_commands"].append(f"bash {script_name}")

    return context


def _needs_torch(repo_path: Path) -> bool:
    """Check if the repo requires PyTorch."""
    # Check setup.py
    setup_py = repo_path / "setup.py"
    if setup_py.exists():
        content = setup_py.read_text()
        if "torch" in content or "detectron2" in content:
            return True

    # Check requirements.txt
    req_txt = repo_path / "requirements.txt"
    if req_txt.exists():
        content = req_txt.read_text()
        if "torch" in content:
            return True

    # Check pyproject.toml
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "torch" in content:
            return True

    return False


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
    dockerfile = repo_path / "Dockerfile"
    if not dockerfile.exists():
        return False, "No Dockerfile found in repo"

    cmd = [
        "docker", "build",
        "--platform", "linux/amd64",
        "-t", image_tag,
        "-f", str(dockerfile),
        str(repo_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,  # 60 minute timeout for large ML images
    )

    if result.returncode == 0:
        return True, ""

    return False, result.stdout + "\n" + result.stderr


def _get_claude_env() -> dict:
    """Get environment for Claude subprocess with CLAUDECODE unset."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    return env


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

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=300,
            env=_get_claude_env(),
        )

        if result.returncode != 0:
            logger.warning("Claude fix attempt failed: %s", result.stderr)
        else:
            logger.info("Claude applied fixes for build attempt %d", attempt)
    except subprocess.TimeoutExpired:
        logger.warning("Claude fix attempt timed out for attempt %d", attempt)
