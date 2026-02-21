"""Step 4: Implements optimizations (via Claude API)."""

import logging
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from src.analyzer import OptimizationPlan

logger = logging.getLogger(__name__)


def optimize(repo_path: str | Path, plan: OptimizationPlan, config: dict) -> Path:
    """Apply each optimization from the plan using Claude Code API.

    Iterates through the optimization plan, sending targeted prompts to Claude
    for each optimization. Uses --continue to maintain context across related changes.

    Returns the path to the optimized repo.
    """
    repo_path = Path(repo_path)
    max_retries = config.get("optimization", {}).get("max_retries", 3)

    # Sort optimizations by priority
    sorted_opts = sorted(plan.optimizations, key=lambda o: o.priority)

    changes_log = []
    conversation_id = None

    for i, opt in enumerate(sorted_opts):
        logger.info(
            "Applying optimization %d/%d: %s (priority %d)",
            i + 1, len(sorted_opts), opt.name, opt.priority,
        )

        success = False
        for attempt in range(1, max_retries + 1):
            try:
                conversation_id = _apply_optimization(
                    repo_path, opt, config, conversation_id,
                )
                changes_log.append({
                    "optimization": opt.name,
                    "description": opt.description,
                    "files": opt.files,
                    "category": opt.category,
                    "expected_speedup": opt.expected_speedup,
                    "expected_accuracy_impact": opt.expected_accuracy_impact,
                    "status": "applied",
                })
                success = True
                break
            except Exception as e:
                logger.warning(
                    "Attempt %d for '%s' failed: %s", attempt, opt.name, e
                )
                if attempt == max_retries:
                    changes_log.append({
                        "optimization": opt.name,
                        "description": opt.description,
                        "status": "failed",
                        "error": str(e),
                    })

        if not success:
            logger.error("Failed to apply optimization: %s", opt.name)

    # Write changes documentation
    _write_changes_doc(repo_path, changes_log, plan)

    logger.info(
        "Optimization complete: %d/%d applied successfully",
        sum(1 for c in changes_log if c["status"] == "applied"),
        len(sorted_opts),
    )

    return repo_path


def _apply_optimization(
    repo_path: Path,
    opt,
    config: dict,
    conversation_id: str | None,
) -> str | None:
    """Apply a single optimization using Claude Code CLI.

    Returns the conversation ID for --continue usage.
    """
    files_str = ", ".join(opt.files) if opt.files else "relevant files"

    prompt = (
        f"Apply this optimization to the codebase:\n\n"
        f"**{opt.name}**: {opt.description}\n\n"
        f"Files to modify: {files_str}\n"
        f"Category: {opt.category}\n"
        f"Expected speedup: {opt.expected_speedup}\n\n"
        f"Constraints:\n"
        f"- Minimal accuracy impact (expected: {opt.expected_accuracy_impact})\n"
        f"- Keep changes focused and well-documented with comments\n"
        f"- Preserve the existing evaluation API and output format\n"
        f"- Ensure the code remains valid Python that will run without errors"
    )

    cmd = ["claude", "-p", prompt, "--output-format", "text"]

    # Use --continue to maintain context if we have a previous conversation
    if conversation_id:
        cmd.extend(["--continue", conversation_id])

    # Unset CLAUDECODE to allow nested invocation
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    result = subprocess.run(
        cmd,
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude optimization failed:\n{result.stderr}")

    # Save Claude's response for debugging
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"optimizer_{opt.name.replace(' ', '_')}.txt").write_text(
        result.stdout
    )

    # Try to extract conversation ID from output for --continue
    # Claude Code outputs the conversation ID that can be used to continue
    return _extract_conversation_id(result.stdout) or conversation_id


def _extract_conversation_id(output: str) -> str | None:
    """Try to extract the conversation ID from Claude's output.

    This is used to pass --continue for subsequent optimizations.
    Returns None if no ID is found (first invocation will start fresh).
    """
    # Claude Code doesn't expose conversation IDs in text output mode,
    # so we use --resume instead by passing the conversation ID from session files.
    # For now, each optimization runs as an independent invocation.
    return None


def _write_changes_doc(
    repo_path: Path,
    changes_log: list[dict],
    plan: OptimizationPlan,
) -> None:
    """Write CHANGES.md documenting all applied optimizations."""
    lines = [
        "# Optimization Changes\n",
        f"\n## Repository: {plan.repo_name}\n",
        f"\n### Summary\n{plan.summary}\n",
        f"\n### Model Architecture: {plan.model_architecture}\n",
        f"\n### Framework: {plan.framework}\n",
        "\n---\n",
        "\n## Applied Optimizations\n",
    ]

    for i, change in enumerate(changes_log, 1):
        status_icon = "+" if change["status"] == "applied" else "x"
        lines.append(f"\n### [{status_icon}] {i}. {change['optimization']}\n")
        lines.append(f"\n**Status**: {change['status']}\n")
        lines.append(f"\n**Description**: {change['description']}\n")

        if change["status"] == "applied":
            files_str = ", ".join(change.get("files", []))
            lines.append(f"\n**Files modified**: {files_str}\n")
            lines.append(f"\n**Category**: {change.get('category', 'N/A')}\n")
            lines.append(
                f"\n**Expected speedup**: {change.get('expected_speedup', 'N/A')}\n"
            )
            lines.append(
                f"\n**Expected accuracy impact**: "
                f"{change.get('expected_accuracy_impact', 'N/A')}\n"
            )
        else:
            lines.append(f"\n**Error**: {change.get('error', 'Unknown')}\n")

    changes_path = repo_path / "CHANGES.md"
    changes_path.write_text("".join(lines))

    # Also save to output logs
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "CHANGES.md").write_text("".join(lines))

    logger.info("Wrote changes documentation to %s", changes_path)
