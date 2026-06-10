#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_BASE_DIR = Path("/home/workspace/Projects/Vamation/logs/patreon-auth-handoff")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare a headed Patreon login browser with a noVNC handoff."
    )
    parser.add_argument("--display-num", type=int, default=107, help="X display number")
    parser.add_argument("--vnc-port", type=int, default=5907, help="Local VNC port")
    parser.add_argument("--web-port", type=int, default=6087, help="Local noVNC web port")
    parser.add_argument("--cdp-port", type=int, default=9225, help="Chromium DevTools port")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE_DIR,
        help="Directory for runtime state, profile, and logs",
    )
    parser.add_argument(
        "--login-url",
        default="https://www.patreon.com/login",
        help="Initial URL to open in Chromium",
    )
    return parser.parse_args()


def kill_if_running(pid_path: Path) -> None:
    if not pid_path.exists():
        return
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    pid_path.unlink(missing_ok=True)


def wait_for_http(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = subprocess.run(
            ["curl", "-I", "-s", url],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "200" in result.stdout.splitlines()[0]:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def wait_for_cdp(cdp_port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{cdp_port}/json/version"
    while time.time() < deadline:
        result = subprocess.run(
            ["curl", "-s", url],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "Chrome/" in result.stdout:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for Chromium DevTools on port {cdp_port}")


def start_process(cmd: list[str], log_path: Path, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    log_file = log_path.open("a", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )


def main() -> int:
    args = parse_args()

    base_dir = args.base_dir
    profile_dir = base_dir / "profile"
    log_dir = base_dir / "logs"
    pid_dir = base_dir / "pids"
    runtime_path = base_dir / "runtime.json"

    for path in (profile_dir, log_dir, pid_dir):
        path.mkdir(parents=True, exist_ok=True)

    display = f":{args.display_num}"
    lock_path = Path(f"/tmp/.X{args.display_num}-lock")
    lock_path.unlink(missing_ok=True)

    for name in ("chromium", "websockify", "x11vnc", "xvfb"):
        kill_if_running(pid_dir / f"{name}.pid")

    xvfb = start_process(
        ["Xvfb", display, "-screen", "0", "1440x900x24"],
        log_dir / "xvfb.log",
    )
    (pid_dir / "xvfb.pid").write_text(str(xvfb.pid), encoding="utf-8")
    time.sleep(2)

    chromium_env = os.environ.copy()
    chromium_env["DISPLAY"] = display

    x11vnc = start_process(
        [
            "x11vnc",
            "-display",
            display,
            "-forever",
            "-shared",
            "-localhost",
            "-nopw",
            "-rfbport",
            str(args.vnc_port),
        ],
        log_dir / "x11vnc.log",
        env=chromium_env,
    )
    (pid_dir / "x11vnc.pid").write_text(str(x11vnc.pid), encoding="utf-8")

    websockify = start_process(
        ["websockify", "--web=/usr/share/novnc", str(args.web_port), f"localhost:{args.vnc_port}"],
        log_dir / "websockify.log",
    )
    (pid_dir / "websockify.pid").write_text(str(websockify.pid), encoding="utf-8")
    wait_for_http(f"http://127.0.0.1:{args.web_port}/vnc.html")

    chromium = start_process(
        [
            "chromium",
            f"--user-data-dir={profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={args.cdp_port}",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--password-store=basic",
            "--window-size=1440,900",
            "--new-window",
            args.login_url,
        ],
        log_dir / "chromium.log",
        env=chromium_env,
    )
    (pid_dir / "chromium.pid").write_text(str(chromium.pid), encoding="utf-8")
    wait_for_cdp(args.cdp_port)

    runtime = {
        "display": display,
        "vnc_port": args.vnc_port,
        "web_port": args.web_port,
        "cdp_port": args.cdp_port,
        "base_dir": str(base_dir),
        "profile_dir": str(profile_dir),
        "logs_dir": str(log_dir),
        "local_vnc_url": f"http://127.0.0.1:{args.web_port}/vnc.html?autoconnect=1&resize=remote&reconnect=1",
        "login_url": args.login_url,
        "note": "Use Zo tooling to proxy web_port externally when a shareable handoff URL is needed.",
    }
    runtime_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")

    print(json.dumps(runtime, indent=2))
    print()
    print("Browser auth handoff is ready.")
    print(f"Local noVNC URL: {runtime['local_vnc_url']}")
    print(f"DevTools port: {args.cdp_port}")
    print(f"Runtime file: {runtime_path}")
    print("Keep this process running until cookie extraction is complete.")

    return chromium.wait()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
