#!/usr/bin/env python3
"""Check commit message on push for release triggers.

Triggers recognized in commit message:
- '/release-agent': build standalone agent binaries and create GitHub release tag
- '/release-bot': build bot container, create GitHub release tag, and push to Docker Hub
- '/release': do both (build agent, build bot, create GitHub release tag, push to Docker Hub)

Optional version override in message (e.g. '/release v1.1.0' or '/release-agent 1.1.0').
If omitted, baseline version is read from version.txt.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "version.txt"


def get_base_version() -> str:
    """Read base version from version.txt (the single source of truth)."""
    if VERSION_FILE.exists():
        raw = VERSION_FILE.read_text(encoding="utf-8").strip()
        m = re.match(r"^(\d+\.\d+(?:\.\d+)?)", raw)
        if m:
            return m.group(1)
        if raw:
            return raw
    return "1.0.0"


def get_commit_message() -> str:
    """Retrieve commit message from environment or git log."""
    msg = os.environ.get("COMMIT_MESSAGE") or os.environ.get("GITHUB_EVENT_HEAD_COMMIT_MESSAGE")
    if msg:
        return msg.strip()
    try:
        res = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""
    return ""


def resolve_build_version(base: str, requested_version: str | None = None) -> str:
    """Resolve a release version, using the CI run number as its patch when available."""
    candidate = (requested_version or base).lstrip("vV")
    run_number = (
        os.environ.get("GITHUB_RUN_NUMBER", "").strip()
        or os.environ.get("BUILD_ID", "").strip()
    )
    if not run_number:
        return candidate

    match = re.match(r"^(\d+)\.(\d+)(?:\.\d+)?", candidate)
    if not match:
        return candidate
    return f"{match.group(1)}.{match.group(2)}.{run_number}"


@dataclass
class ReleaseDecision:
    release_agent: bool
    release_bot: bool
    is_release: bool
    version: str
    short_version: str
    tag: str
    trigger_reason: str


def evaluate_release(message: str | None = None) -> ReleaseDecision:
    """Parse commit message and determine release triggers strictly from message text or workflow_dispatch."""
    event_name = os.environ.get("EVENT_NAME", "").strip().lower()
    if event_name == "workflow_dispatch":
        dispatch_target = os.environ.get("DISPATCH_TARGET", "all").strip().lower()
        dispatch_push_docker = os.environ.get("DISPATCH_PUSH_DOCKER", "true").strip().lower() != "false"
        dispatch_version = os.environ.get("DISPATCH_VERSION", "").strip()
        dispatch_release = os.environ.get("DISPATCH_RELEASE", "true").strip().lower() != "false"

        release_agent = dispatch_target in ("all", "installer", "agent")
        release_bot = dispatch_target in ("all", "bot") and dispatch_push_docker
        version = resolve_build_version(get_base_version(), dispatch_version or None)
        tag = f"v{version}"
        parts = version.split(".")
        major = parts[0] if len(parts) > 0 else "1"
        minor = parts[1] if len(parts) > 1 else "0"
        short_version = f"{major}.{minor}"

        return ReleaseDecision(
            release_agent=release_agent,
            release_bot=release_bot,
            is_release=dispatch_release,
            version=version,
            short_version=short_version,
            tag=tag,
            trigger_reason=f"manual workflow_dispatch (release: {dispatch_release}, target: {dispatch_target})",
        )

    msg = message if message is not None else get_commit_message()

    has_release_agent = bool(re.search(r"/(?:release|reelase)-agent\b", msg, re.IGNORECASE))
    has_release_bot = bool(re.search(r"/(?:release|reelase)-bot\b", msg, re.IGNORECASE))
    has_release_all = bool(re.search(r"/(?:release|reelase)\b(?!-agent|-bot)", msg, re.IGNORECASE))

    trigger_reasons: list[str] = []
    if has_release_all:
        trigger_reasons.append("commit message contains '/release'")
    if has_release_agent:
        trigger_reasons.append("commit message contains '/release-agent'")
    if has_release_bot:
        trigger_reasons.append("commit message contains '/release-bot'")

    release_agent = has_release_agent or has_release_all
    release_bot = has_release_bot or has_release_all
    is_release = release_agent or release_bot

    # Extract explicit version if specified in commit message (e.g. /release v1.1.0 or /release-bot 1.1.0)
    m = re.search(r"/(?:release|reelase)(?:-agent|-bot)?(?:\s+|:\s*)([vV]?\d+\.\d+(?:\.\d+)?\S*)", msg, re.IGNORECASE)
    if m:
        version = resolve_build_version(get_base_version(), m.group(1).strip())
    else:
        version = resolve_build_version(get_base_version())

    tag = f"v{version}"
    parts = version.split(".")
    major = parts[0] if len(parts) > 0 else "1"
    minor = parts[1] if len(parts) > 1 else "0"
    short_version = f"{major}.{minor}"

    reason_str = ", ".join(trigger_reasons) if trigger_reasons else "standard commit (no release triggers)"

    return ReleaseDecision(
        release_agent=release_agent,
        release_bot=release_bot,
        is_release=is_release,
        version=version,
        short_version=short_version,
        tag=tag,
        trigger_reason=reason_str,
    )


def write_github_outputs(decision: ReleaseDecision) -> None:
    """Write outputs to $GITHUB_OUTPUT file if present."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return

    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"release_agent={str(decision.release_agent).lower()}\n")
        f.write(f"release_bot={str(decision.release_bot).lower()}\n")
        f.write(f"is_release={str(decision.is_release).lower()}\n")
        f.write(f"version={decision.version}\n")
        f.write(f"short_version={decision.short_version}\n")
        f.write(f"tag={decision.tag}\n")
        f.write(f"trigger_reason={decision.trigger_reason}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect commit message on push for release triggers."
    )
    parser.add_argument("-m", "--message", help="Commit message to check")
    args = parser.parse_args()

    decision = evaluate_release(message=args.message)

    print("==================================================")
    print(" Cmdiqo Release Trigger Detection")
    print("==================================================")
    print(f"[*] Release Agent:  {decision.release_agent}")
    print(f"[*] Release Bot:    {decision.release_bot}")
    print(f"[*] Is Release:     {decision.is_release}")
    print(f"[*] Target Version: {decision.version}")
    print(f"[*] Target Tag:     {decision.tag}")
    print(f"[*] Reason:         {decision.trigger_reason}")
    print("==================================================")

    write_github_outputs(decision)


if __name__ == "__main__":
    main()
