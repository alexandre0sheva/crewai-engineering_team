#!/usr/bin/env python
"""Command-line and CrewAI entry points."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from engineering_team.crew import EngineeringTeam
from engineering_team.tools.workspace_tools import ProjectWorkspace, configure_workspace

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REQUEST_FILE = PROJECT_ROOT / "PROJECT_REQUEST.md"
DEFAULT_WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
TEMPLATE_MARKER = "<!-- ENGINEERING_TEAM_REQUEST_TEMPLATE -->"
SMOKE_PROFILE_MARKER = "<!-- ENGINEERING_TEAM_PROFILE: smoke -->"
VALID_RUN_PROFILES = {"standard", "smoke"}


def slugify_project_name(value: str) -> str:
    """Convert a display name into a safe workspace directory name."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Project name must contain at least one letter or number.")
    return slug[:80]


def load_requirements(
    *,
    inline_request: str | None = None,
    request_file: str | Path | None = None,
) -> str:
    """Load a non-placeholder product request from CLI, environment, or file."""

    requirements = inline_request or os.getenv("ENGINEERING_PROJECT_REQUEST")
    if requirements:
        requirements = requirements.strip()
    else:
        configured_file = (
            Path(request_file).expanduser()
            if request_file
            else Path(os.getenv("ENGINEERING_REQUEST_FILE", DEFAULT_REQUEST_FILE)).expanduser()
        )
        if not configured_file.is_file():
            raise ValueError(
                f"Request file not found: {configured_file}. Pass --request or --request-file."
            )
        requirements = configured_file.read_text(encoding="utf-8").strip()

    if not requirements or TEMPLATE_MARKER in requirements:
        raise ValueError(
            "The project request is still a template. Edit PROJECT_REQUEST.md or pass "
            "--request/--request-file with concrete MVP requirements."
        )
    return requirements


def resolve_run_profile(requirements: str, explicit_profile: str | None = None) -> str:
    """Resolve standard versus low-cost smoke execution."""

    profile = (
        explicit_profile
        or os.getenv("ENGINEERING_RUN_PROFILE")
        or ("smoke" if SMOKE_PROFILE_MARKER in requirements else "standard")
    )
    normalized = profile.strip().lower()
    if normalized not in VALID_RUN_PROFILES:
        choices = ", ".join(sorted(VALID_RUN_PROFILES))
        raise ValueError(f"Unknown engineering run profile '{profile}'. Choose one of: {choices}.")
    return normalized


def prepare_workspace(
    project_name: str,
    workspace_root: str | Path = DEFAULT_WORKSPACE_ROOT,
    *,
    reset: bool = False,
) -> ProjectWorkspace:
    """Create or resume one persistent project directory."""

    root = Path(workspace_root).expanduser()
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    if root == Path(root.anchor):
        raise ValueError("The filesystem root cannot be used as ENGINEERING_WORKSPACE_ROOT.")
    project_path = root / slugify_project_name(project_name)

    if reset and project_path.exists():
        shutil.rmtree(project_path)

    workspace = configure_workspace(project_path)
    (workspace.root / ".engineering-team").mkdir(parents=True, exist_ok=True)
    return workspace


def build_inputs(
    *,
    project_name: str,
    requirements: str,
    workspace: ProjectWorkspace,
    run_profile: str,
) -> dict[str, str]:
    """Create the interpolation inputs shared by every agent and task."""

    metadata_dir = workspace.root / ".engineering-team"
    (metadata_dir / "request.md").write_text(requirements.rstrip() + "\n", encoding="utf-8")
    (metadata_dir / "run.json").write_text(
        json.dumps(
            {
                "project_name": project_name,
                "workspace_path": str(workspace.root),
                "date": date.today().isoformat(),
                "run_profile": run_profile,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "project_name": project_name,
        "requirements": requirements,
        "workspace_path": str(workspace.root),
        "current_date": date.today().isoformat(),
        "run_profile": run_profile,
    }


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engineering-team",
        description="Build or continue an MVP with the CrewAI engineering team.",
    )
    request_source = parser.add_mutually_exclusive_group()
    request_source.add_argument("--request", help="Inline product requirements.")
    request_source.add_argument(
        "--request-file",
        help="Markdown or text file containing product requirements.",
    )
    parser.add_argument(
        "--project-name",
        default=os.getenv("ENGINEERING_PROJECT_NAME", "mvp-app"),
        help="Name used for the persistent workspace directory.",
    )
    parser.add_argument(
        "--workspace-root",
        default=os.getenv("ENGINEERING_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT)),
        help="Parent directory for generated projects.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete this project's existing workspace before starting.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Validate inputs and prepare the workspace without calling an LLM.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(VALID_RUN_PROFILES),
        help="Use standard quality routing or the lower-cost smoke routing.",
    )
    return parser


def _prepare_from_args(args: argparse.Namespace) -> tuple[dict[str, str], ProjectWorkspace]:
    requirements = load_requirements(
        inline_request=args.request,
        request_file=args.request_file,
    )
    run_profile = resolve_run_profile(requirements, args.profile)
    os.environ["ENGINEERING_RUN_PROFILE"] = run_profile
    workspace = prepare_workspace(
        args.project_name,
        args.workspace_root,
        reset=args.reset,
    )
    inputs = build_inputs(
        project_name=args.project_name,
        requirements=requirements,
        workspace=workspace,
        run_profile=run_profile,
    )
    return inputs, workspace


def run() -> Any:
    """Build or resume an MVP from command-line inputs."""

    args = _run_parser().parse_args()
    try:
        inputs, workspace = _prepare_from_args(args)
        if args.prepare_only:
            print(f"Prepared project workspace: {workspace.root}")
            return None

        result = EngineeringTeam().crew().kickoff(inputs=inputs)
        print(f"\nProject workspace: {workspace.root}")
        return result
    except Exception as exc:
        raise RuntimeError(f"Engineering team run failed: {exc}") from exc


def train() -> None:
    """Train the crew using the current request and workspace."""

    if len(sys.argv) < 3:
        raise ValueError("Usage: train <iterations> <training-file>")
    args = _run_parser().parse_args(sys.argv[3:])
    inputs, _workspace = _prepare_from_args(args)
    EngineeringTeam().crew().train(
        n_iterations=int(sys.argv[1]),
        filename=sys.argv[2],
        inputs=inputs,
    )


def replay() -> None:
    """Replay the latest crew run from a task ID."""

    if len(sys.argv) < 2:
        raise ValueError("Usage: replay <task-id>")
    project_name = os.getenv("ENGINEERING_PROJECT_NAME", "mvp-app")
    prepare_workspace(project_name, os.getenv("ENGINEERING_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT))
    EngineeringTeam().crew().replay(task_id=sys.argv[1])


def test() -> None:
    """Run CrewAI's iterative crew evaluation command."""

    if len(sys.argv) < 3:
        raise ValueError("Usage: test <iterations> <evaluation-model>")
    args = _run_parser().parse_args(sys.argv[3:])
    inputs, _workspace = _prepare_from_args(args)
    EngineeringTeam().crew().test(
        n_iterations=int(sys.argv[1]),
        eval_llm=sys.argv[2],
        inputs=inputs,
    )


def run_with_trigger() -> Any:
    """Run from a CrewAI trigger payload containing requirements and project_name."""

    if len(sys.argv) < 2:
        raise ValueError("No trigger JSON payload provided.")
    try:
        payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        raise ValueError("Trigger payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Trigger payload must be a JSON object.")

    requirements = payload.get("requirements") or payload.get("project_request")
    if not isinstance(requirements, str) or not requirements.strip():
        requirements = json.dumps(payload, indent=2)
    project_name = str(payload.get("project_name", "triggered-mvp"))
    payload_profile = payload.get("run_profile")
    if payload_profile is not None and not isinstance(payload_profile, str):
        raise ValueError("Trigger run_profile must be a string.")
    run_profile = resolve_run_profile(requirements, payload_profile)
    os.environ["ENGINEERING_RUN_PROFILE"] = run_profile
    workspace = prepare_workspace(
        project_name,
        os.getenv("ENGINEERING_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT),
    )
    inputs = build_inputs(
        project_name=project_name,
        requirements=requirements,
        workspace=workspace,
        run_profile=run_profile,
    )
    inputs["crewai_trigger_payload"] = json.dumps(payload)
    return EngineeringTeam().crew().kickoff(inputs=inputs)


if __name__ == "__main__":
    run()
