#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = PROJECT_ROOT / "experiments" / "artifacts" / "auth-handoff"
PID_DIR = ARTIFACT_DIR / "pids"
LOG_DIR = ARTIFACT_DIR / "logs"
PROFILE_DIR = ARTIFACT_DIR / "chromium-profile"
COOKIE_EXPORT = ARTIFACT_DIR / "pipeline-cookie-batch.json"
AUTH_METADATA_EXPORT = ARTIFACT_DIR / "pipeline-cookie-batch.auth.json"
STATUS_FILE = ARTIFACT_DIR / "status.json"
SCREENSHOT_FILE = ARTIFACT_DIR / "latest.png"
DISPLAY_NUM = ":88"
VNC_PORT = 5901
NOVNC_PORT = 6081
CHROME_DEBUG_PORT = 9223
AGENT_BROWSER_SESSION = "vamation-auth-handoff"
LOGIN_URL = "https://www.patreon.com/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_CAMPAIGN_ID = "13637777"


def ensure_dirs() -> None:
    for path in [ARTIFACT_DIR, PID_DIR, LOG_DIR, PROFILE_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def pid_file(name: str) -> Path:
    return PID_DIR / f"{name}.pid"


def log_file(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def write_status(**kwargs) -> None:
    current = {}
    if STATUS_FILE.exists():
        try:
            current = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    payload = {**current, **kwargs, "updated_at": time.time()}
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_bg(name: str, cmd: list[str], env: dict[str, str] | None = None) -> int:
    handle = open(log_file(name), "ab")
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env)
    pid_file(name).write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def read_pid(name: str) -> int | None:
    file = pid_file(name)
    if not file.exists():
        return None
    try:
        return int(file.read_text().strip())
    except Exception:
        return None


def kill_name(name: str) -> None:
    pid = read_pid(name)
    if not pid:
        return
    try:
        os.kill(pid, 15)
        time.sleep(1)
        os.kill(pid, 0)
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    except Exception:
        pass
    pid_file(name).unlink(missing_ok=True)


def wait_for_port(port: int, seconds: int = 20) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def agent_browser_cmd(*parts: str) -> list[str]:
    return ["agent-browser", "--session", AGENT_BROWSER_SESSION, *parts]


def connect_agent_browser() -> None:
    subprocess.run(
        agent_browser_cmd("connect", str(CHROME_DEBUG_PORT), "--json"),
        check=True,
        capture_output=True,
        text=True,
    )


def get_patron_cookies() -> list[dict[str, str]]:
    connect_agent_browser()
    raw = subprocess.check_output(agent_browser_cmd("cookies", "get", "--json"), text=True)
    data = json.loads(raw)
    cookies = data.get("data", {}).get("cookies", [])
    simple: list[dict[str, str]] = []
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        if "patreon.com" not in domain and "patreonusercontent.com" not in domain:
            continue
        simple.append(
            {
                "name": cookie.get("name", ""),
                "value": cookie.get("value", ""),
                "domain": domain,
            }
        )
    return simple


def build_session(cookies: list[dict[str, str]]) -> requests.Session:
    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ".patreon.com"))
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/vnd.api+json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.patreon.com/",
            "Origin": "https://www.patreon.com",
        }
    )
    return session


def validate_cookie_batch(cookies: list[dict[str, str]], campaign_id: str) -> tuple[bool, dict]:
    names = {cookie.get("name") for cookie in cookies}
    missing = [name for name in ["session_id", "cf_clearance", "patreon_device_id"] if name not in names]
    if missing:
        return False, {"reason": "missing_required_cookies", "missing": missing}

    try:
        session = build_session(cookies)
        response = session.get(f"https://www.patreon.com/api/campaigns/{campaign_id}", timeout=30)
        ok = response.status_code == 200
        details = {
            "reason": "validated" if ok else "http_error",
            "status_code": response.status_code,
            "body_prefix": response.text[:300],
        }
        return ok, details
    except Exception as exc:
        return False, {"reason": "request_exception", "error": str(exc)}


def launch(auto_export: Path | None = None, metadata_output: Path | None = None, timeout_seconds: int = 900, poll_seconds: int = 5, campaign_id: str = DEFAULT_CAMPAIGN_ID) -> None:
    ensure_dirs()
    for name in ["auth-watcher", "novnc", "x11vnc", "chromium", "fluxbox", "xvfb"]:
        kill_name(name)

    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY_NUM

    start_bg("xvfb", ["Xvfb", DISPLAY_NUM, "-screen", "0", "1440x900x24", "-ac"])
    time.sleep(1)
    start_bg("fluxbox", ["fluxbox"], env=env)
    start_bg("x11vnc", ["x11vnc", "-display", DISPLAY_NUM, "-forever", "-shared", "-rfbport", str(VNC_PORT), "-nopw"])
    start_bg("novnc", ["/usr/share/novnc/utils/novnc_proxy", "--listen", str(NOVNC_PORT), "--vnc", f"127.0.0.1:{VNC_PORT}"])
    start_bg(
        "chromium",
        [
            "chromium",
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            f"--user-agent={UA}",
            LOGIN_URL,
        ],
        env=env,
    )

    ok = wait_for_port(VNC_PORT, 20) and wait_for_port(NOVNC_PORT, 20) and wait_for_port(CHROME_DEBUG_PORT, 20)
    if ok:
        try:
            connect_agent_browser()
        except Exception:
            ok = False

    auto_export_path = str(auto_export.resolve()) if auto_export else ""
    metadata_path = str((metadata_output or AUTH_METADATA_EXPORT).resolve()) if (auto_export or metadata_output) else ""

    write_status(
        phase="running" if ok else "failed",
        display=DISPLAY_NUM,
        vnc_port=VNC_PORT,
        novnc_port=NOVNC_PORT,
        chrome_debug_port=CHROME_DEBUG_PORT,
        agent_browser_session=AGENT_BROWSER_SESSION,
        login_url=LOGIN_URL,
        cookie_export=str(COOKIE_EXPORT),
        auth_metadata_export=str(AUTH_METADATA_EXPORT),
        auto_export_target=auto_export_path,
        metadata_output=metadata_path,
        screenshot=str(SCREENSHOT_FILE),
        campaign_id=campaign_id,
        last_exported_at="",
        last_export_validation=None,
    )

    if ok and auto_export:
        watcher_cmd = [
            os.environ.get("PYTHON", "python"),
            str(Path(__file__).resolve()),
            "wait-for-auth",
            "--output",
            str(auto_export.resolve()),
            "--metadata-output",
            str((metadata_output or AUTH_METADATA_EXPORT).resolve()),
            "--timeout-seconds",
            str(timeout_seconds),
            "--poll-seconds",
            str(poll_seconds),
            "--campaign-id",
            campaign_id,
        ]
        start_bg("auth-watcher", watcher_cmd)
        write_status(auth_watcher_pid=read_pid("auth-watcher"), phase="awaiting-auth")

    print(
        json.dumps(
            {
                "ok": ok,
                "display": DISPLAY_NUM,
                "vnc_port": VNC_PORT,
                "novnc_port": NOVNC_PORT,
                "chrome_debug_port": CHROME_DEBUG_PORT,
                "agent_browser_session": AGENT_BROWSER_SESSION,
                "auto_export_target": auto_export_path,
                "metadata_output": metadata_path,
                "status_file": str(STATUS_FILE),
            },
            indent=2,
        )
    )


def export_cookies(output: Path | None = None, metadata_output: Path | None = None, campaign_id: str = DEFAULT_CAMPAIGN_ID, require_valid: bool = False) -> int:
    ensure_dirs()
    cookies = get_patron_cookies()
    validation_ok, validation = validate_cookie_batch(cookies, campaign_id)
    if require_valid and not validation_ok:
        print(json.dumps({"cookie_count": len(cookies), "validated": False, "validation": validation}, indent=2))
        return 1

    target = output or COOKIE_EXPORT
    meta_target = metadata_output or AUTH_METADATA_EXPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    meta_target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    auth_time = datetime.now(timezone.utc).isoformat()
    meta = {
        "authenticated_at": auth_time,
        "cookie_file": str(target),
        "cookie_count": len(cookies),
        "cookie_names": sorted(cookie.get("name", "") for cookie in cookies),
        "validated": validation_ok,
        "validation": validation,
        "campaign_id": campaign_id,
    }
    meta_target.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_status(last_exported_at=auth_time, last_export_validation=meta, phase="authenticated" if validation_ok else "running")
    print(json.dumps({"cookie_count": len(cookies), "output": str(target), "metadata_output": str(meta_target), "validated": validation_ok, "validation": validation}, indent=2))
    return 0


def wait_for_auth(output: Path | None = None, metadata_output: Path | None = None, timeout_seconds: int = 900, poll_seconds: int = 5, campaign_id: str = DEFAULT_CAMPAIGN_ID) -> int:
    ensure_dirs()
    deadline = time.time() + timeout_seconds
    last_validation: dict | None = None
    while time.time() < deadline:
        try:
            cookies = get_patron_cookies()
            valid, validation = validate_cookie_batch(cookies, campaign_id)
            last_validation = validation
            if valid:
                return export_cookies(output=output, metadata_output=metadata_output, campaign_id=campaign_id, require_valid=True)
        except Exception as exc:
            last_validation = {"reason": "exception", "error": str(exc)}
        time.sleep(poll_seconds)

    write_status(phase="auth-timeout", last_export_validation=last_validation)
    print(json.dumps({"authenticated": False, "validation": last_validation or {"reason": "timeout"}}, indent=2))
    return 1


def screenshot() -> None:
    ensure_dirs()
    connect_agent_browser()
    out = subprocess.check_output(agent_browser_cmd("screenshot", str(SCREENSHOT_FILE)), text=True)
    print(out)


def close_all() -> None:
    for name in ["auth-watcher", "chromium", "novnc", "x11vnc", "fluxbox", "xvfb"]:
        kill_name(name)
    write_status(phase="stopped")
    print(json.dumps({"stopped": True}, indent=2))


def reset() -> None:
    close_all()
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    for file in [COOKIE_EXPORT, AUTH_METADATA_EXPORT, SCREENSHOT_FILE, STATUS_FILE]:
        file.unlink(missing_ok=True)
    if PID_DIR.exists():
        shutil.rmtree(PID_DIR, ignore_errors=True)
    if LOG_DIR.exists():
        shutil.rmtree(LOG_DIR, ignore_errors=True)
    print(json.dumps({"reset": True, "profile_cleared": True}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    launch_parser = sub.add_parser("launch")
    launch_parser.add_argument("--auto-export", default="")
    launch_parser.add_argument("--metadata-output", default="")
    launch_parser.add_argument("--timeout-seconds", type=int, default=900)
    launch_parser.add_argument("--poll-seconds", type=int, default=5)
    launch_parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)

    export_parser = sub.add_parser("export-cookies")
    export_parser.add_argument("--output", default="")
    export_parser.add_argument("--metadata-output", default="")
    export_parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)
    export_parser.add_argument("--require-valid", action="store_true")

    wait_parser = sub.add_parser("wait-for-auth")
    wait_parser.add_argument("--output", default="")
    wait_parser.add_argument("--metadata-output", default="")
    wait_parser.add_argument("--timeout-seconds", type=int, default=900)
    wait_parser.add_argument("--poll-seconds", type=int, default=5)
    wait_parser.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN_ID)

    sub.add_parser("screenshot")
    sub.add_parser("close")
    sub.add_parser("reset")

    args = parser.parse_args()

    if args.cmd == "launch":
        launch(
            auto_export=Path(args.auto_export).resolve() if args.auto_export else None,
            metadata_output=Path(args.metadata_output).resolve() if args.metadata_output else None,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            campaign_id=args.campaign_id,
        )
        return 0
    if args.cmd == "export-cookies":
        return export_cookies(
            output=Path(args.output).resolve() if args.output else None,
            metadata_output=Path(args.metadata_output).resolve() if args.metadata_output else None,
            campaign_id=args.campaign_id,
            require_valid=args.require_valid,
        )
    if args.cmd == "wait-for-auth":
        return wait_for_auth(
            output=Path(args.output).resolve() if args.output else None,
            metadata_output=Path(args.metadata_output).resolve() if args.metadata_output else None,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            campaign_id=args.campaign_id,
        )
    if args.cmd == "screenshot":
        screenshot()
        return 0
    if args.cmd == "close":
        close_all()
        return 0
    if args.cmd == "reset":
        reset()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
