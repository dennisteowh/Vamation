#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = PROJECT_ROOT / "experiments" / "artifacts"
DEFAULT_SESSION = "vamation-bridge"
DEFAULT_URL = "https://www.patreon.com/login"
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
DEFAULT_ARGS = "--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def agent_browser_base(session_name: str) -> list[str]:
    return [
        "agent-browser",
        "--session-name",
        session_name,
        "--args",
        DEFAULT_ARGS,
        "--user-agent",
        DEFAULT_UA,
    ]


def open_session(session_name: str, url: str) -> dict:
    cmd = agent_browser_base(session_name) + ["open", url]
    proc = run(cmd, check=False)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def capture_state(session_name: str, artifact_dir: Path) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    screenshot = artifact_dir / "patreon-bridge.png"
    state_path = artifact_dir / "patreon-state.json"

    shot = run(agent_browser_base(session_name) + ["screenshot", str(screenshot)], check=False)
    snap = run(agent_browser_base(session_name) + ["snapshot", "-i"], check=False)
    cookies = run(agent_browser_base(session_name) + ["cookies", "get", "--json"], check=False)
    state = run(agent_browser_base(session_name) + ["state", "save", str(state_path)], check=False)
    title = run(agent_browser_base(session_name) + ["get", "title"], check=False)
    url = run(agent_browser_base(session_name) + ["get", "url"], check=False)

    return {
        "screenshot": str(screenshot),
        "state_path": str(state_path),
        "title": title.stdout.strip(),
        "url": url.stdout.strip(),
        "snapshot": snap.stdout,
        "cookies_raw": cookies.stdout,
        "state_stdout": state.stdout,
        "state_stderr": state.stderr,
    }


def export_pipeline_cookie_batch(state_path: Path, output_path: Path) -> Path:
    data = json.loads(state_path.read_text())
    cookies = data.get("cookies", [])
    simplified = [
        {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": cookie.get("domain", ".patreon.com"),
        }
        for cookie in cookies
    ]
    output_path.write_text(json.dumps(simplified, indent=2), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Patreon auth bridge using agent-browser without touching the main pipeline.")
    parser.add_argument("command", choices=["open", "capture", "export-cookies"])
    parser.add_argument("--session-name", default=DEFAULT_SESSION)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--artifact-dir", default=str(ARTIFACT_DIR))
    parser.add_argument("--state-path", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)

    if shutil.which("agent-browser") is None:
        print("agent-browser is not installed", file=sys.stderr)
        return 1

    if args.command == "open":
        result = open_session(args.session_name, args.url)
        print(json.dumps(result, indent=2))
        return result["returncode"]

    if args.command == "capture":
        result = capture_state(args.session_name, artifact_dir)
        print(json.dumps(result, indent=2))
        return 0

    state_path = Path(args.state_path) if args.state_path else artifact_dir / "patreon-state.json"
    output_path = Path(args.output) if args.output else artifact_dir / "pipeline-cookie-batch.json"
    exported = export_pipeline_cookie_batch(state_path, output_path)
    print(str(exported))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
