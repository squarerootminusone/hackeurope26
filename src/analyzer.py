"""Step 3: Repo inspection & optimization planning (via Claude API)."""

import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Optimization:
    name: str
    description: str
    files: list[str]
    category: str  # e.g., "mixed_precision", "torch_compile", "data_loading"
    expected_speedup: str  # e.g., "1.5-2x"
    expected_accuracy_impact: str  # e.g., "<0.1% loss"
    priority: int  # 1 = highest


@dataclass
class OptimizationPlan:
    repo_name: str
    optimizations: list[Optimization] = field(default_factory=list)
    summary: str = ""
    model_architecture: str = ""
    framework: str = ""


def analyze(repo_path: str | Path, config: dict) -> OptimizationPlan:
    """Use Claude Code API to inspect the repo and produce an optimization plan.

    Reads the generic optimization guide and optional custom guide, then
    sends a prompt to Claude to analyze the codebase and produce a structured plan.
    """
    repo_path = Path(repo_path)
    max_retries = config.get("optimization", {}).get("max_retries", 3)

    # Load optimization guides
    guide_content = _load_guides(config)

    # Build the analysis prompt
    prompt = _build_analysis_prompt(guide_content, config)

    # Call Claude Code API with retries
    for attempt in range(1, max_retries + 1):
        logger.info("Analysis attempt %d/%d", attempt, max_retries)
        try:
            raw_response = _call_claude(prompt, repo_path)
            plan = _parse_response(raw_response, repo_path.name)

            # Save the plan
            _save_plan(plan)

            logger.info(
                "Analysis complete: %d optimizations identified",
                len(plan.optimizations),
            )
            return plan

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Attempt %d failed to parse response: %s", attempt, e)
            if attempt == max_retries:
                raise RuntimeError(
                    f"Failed to get valid optimization plan after {max_retries} attempts"
                ) from e

    # Should not reach here, but just in case
    raise RuntimeError("Analysis failed")


def _load_guides(config: dict) -> str:
    """Load the default and optional custom optimization guides."""
    guide_path = config.get("optimization", {}).get("guide", "guides/default_optimizations.md")
    custom_guide_path = config.get("optimization", {}).get("custom_guide", "")

    content = ""

    if Path(guide_path).exists():
        content += Path(guide_path).read_text()
        logger.info("Loaded optimization guide: %s", guide_path)

    if custom_guide_path and Path(custom_guide_path).exists():
        content += "\n\n# Custom Optimizations\n\n"
        content += Path(custom_guide_path).read_text()
        logger.info("Loaded custom guide: %s", custom_guide_path)

    return content


def _build_analysis_prompt(guide_content: str, config: dict) -> str:
    """Build the prompt for Claude to analyze the repo."""
    target = config.get("target", {})
    accuracy_metrics = target.get("accuracy_metrics", [])
    paper_baselines = target.get("paper_baselines", {})

    return f"""You are analyzing this ML repository for performance optimizations.

Follow this optimization guide:
{guide_content}

Repository details:
- Eval command: {target.get("eval_command", "N/A")}
- Accuracy metrics to preserve: {", ".join(accuracy_metrics)}
- Paper baseline values: {json.dumps(paper_baselines)}

Inspect the code thoroughly and produce a JSON optimization plan. Return ONLY valid JSON with this exact structure:
{{
  "summary": "Brief summary of the repo and optimization strategy",
  "model_architecture": "Description of the model architecture",
  "framework": "PyTorch/TensorFlow/etc",
  "optimizations": [
    {{
      "name": "Short name for the optimization",
      "description": "Detailed description of what to change and why",
      "files": ["path/to/file1.py", "path/to/file2.py"],
      "category": "mixed_precision|torch_compile|data_loading|batch_size|quantization|cuda_opts|memory|other",
      "expected_speedup": "e.g., 1.5-2x",
      "expected_accuracy_impact": "e.g., <0.1% loss or 0%",
      "priority": 1
    }}
  ]
}}

Focus on optimizations that:
1. Give the most speedup with minimal accuracy impact
2. Are safe and well-tested techniques
3. Are applicable to this specific codebase
4. Can be validated by running the eval command

Sort optimizations by priority (1 = highest impact, apply first)."""


def _call_claude(prompt: str, repo_path: Path) -> str:
    """Call Claude Code CLI to analyze the repo."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=600,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude API call failed:\n{result.stderr}")

    # Save raw response for debugging
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "analyzer_raw_response.txt").write_text(result.stdout)

    return result.stdout


def _parse_response(raw_response: str, repo_name: str) -> OptimizationPlan:
    """Parse Claude's JSON response into an OptimizationPlan."""
    # Extract JSON from the response (Claude might wrap it in markdown code blocks)
    json_str = raw_response.strip()

    # Try to find JSON block in markdown
    if "```json" in json_str:
        start = json_str.index("```json") + 7
        end = json_str.index("```", start)
        json_str = json_str[start:end].strip()
    elif "```" in json_str:
        start = json_str.index("```") + 3
        end = json_str.index("```", start)
        json_str = json_str[start:end].strip()

    data = json.loads(json_str)

    optimizations = []
    for opt_data in data.get("optimizations", []):
        optimizations.append(Optimization(
            name=opt_data["name"],
            description=opt_data["description"],
            files=opt_data["files"],
            category=opt_data["category"],
            expected_speedup=opt_data["expected_speedup"],
            expected_accuracy_impact=opt_data["expected_accuracy_impact"],
            priority=opt_data["priority"],
        ))

    return OptimizationPlan(
        repo_name=repo_name,
        optimizations=optimizations,
        summary=data.get("summary", ""),
        model_architecture=data.get("model_architecture", ""),
        framework=data.get("framework", ""),
    )


def _save_plan(plan: OptimizationPlan) -> None:
    """Save the optimization plan to a JSON file."""
    log_dir = Path("output/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    plan_dict = asdict(plan)
    plan_path = log_dir / "optimization_plan.json"
    plan_path.write_text(json.dumps(plan_dict, indent=2))
    logger.info("Saved optimization plan to %s", plan_path)
