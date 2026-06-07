#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = PROJECT_ROOT / "experiments" / "artifacts" / "cloudflare-trust"
OUT_JSON = ARTIFACT_DIR / "result.json"
URL = "https://www.patreon.com/VAMA/posts"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
EXTRA_ARGS = "--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled,--disable-features=IsolateOrigins,site-per-process,--lang=en-US,--window-size=1440,900"
COOKIE_CANDIDATES = [
    Path("/home/.z/chat-uploads/cookies - Copy-5f60ccfe942e.txt"),
    Path("/home/.z/chat-uploads/cookies-5b541d422395.txt"),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for index, cookie_file in enumerate(COOKIE_CANDIDATES, start=1):
        session = f"vamation-cloudflare-trust-{index}"
        browser_dir = ARTIFACT_DIR / f"profile-{index}"
        screenshot = ARTIFACT_DIR / f"page-{index}.png"

        run(["agent-browser", "--session", session, "close", "--all"])
        shutil.rmtree(browser_dir, ignore_errors=True)

        base = [
            "agent-browser",
            "--session", session,
            "--profile", str(browser_dir),
            "--user-agent", UA,
            "--args", EXTRA_ARGS,
        ]

        run(base + ["open", "about:blank"])
        run(base + ["cookies", "clear"])

        cookies = json.loads(cookie_file.read_text())
        for cookie in cookies:
            domain = cookie["domain"]
            url = "https://www.patreon.com" if domain.startswith(".") else f"https://{domain}"
            run(base + [
                "cookies", "set", cookie["name"], cookie["value"],
                "--domain", domain,
                "--path", "/",
                "--secure",
                "--url", url,
            ])

        opened = run(base + ["open", URL])
        time.sleep(4)
        title = run(base + ["get", "title"])
        current_url = run(base + ["get", "url"])
        snapshot = run(base + ["snapshot", "-i"])
        shot = run(base + ["screenshot", str(screenshot)])
        page_state = run(base + [
            "eval",
            '({title:document.title,url:location.href,ready:document.readyState,body:(document.body?.innerText||"").slice(0,1200),html:(document.documentElement?.outerHTML||"").slice(0,1500)})'
        ])

        results.append({
            "cookie_file": str(cookie_file),
            "open_stdout": opened.stdout,
            "open_stderr": opened.stderr,
            "title": title.stdout.strip(),
            "title_err": title.stderr,
            "url": current_url.stdout.strip(),
            "url_err": current_url.stderr,
            "snapshot": snapshot.stdout,
            "snapshot_err": snapshot.stderr,
            "page_state": page_state.stdout.strip(),
            "page_state_err": page_state.stderr,
            "screenshot": str(screenshot),
            "screenshot_stdout": shot.stdout,
            "screenshot_stderr": shot.stderr,
        })

        run(["agent-browser", "--session", session, "close", "--all"])

    payload = {
        "tested_url": URL,
        "user_agent": UA,
        "extra_args": EXTRA_ARGS,
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
