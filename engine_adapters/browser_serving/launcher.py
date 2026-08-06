"""Command-line launcher for the 7870 Gateway and 7860 Admin UI."""

from __future__ import annotations

import argparse
import threading
import time
import urllib.error
import urllib.request

import uvicorn

from .config import BrowserServingConfig
from .frontend import launch_admin
from .gateway import create_app


def run_gateway(config: BrowserServingConfig | None = None) -> None:
    resolved = config or BrowserServingConfig.from_environment()
    uvicorn.run(
        create_app(config=resolved),
        host=resolved.gateway_host,
        port=resolved.gateway_port,
        log_level="info",
    )


def _wait_for_gateway(config: BrowserServingConfig) -> None:
    health_url = f"{config.public_gateway_url}/api/health"
    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                health_url,
                timeout=1.0,
            ) as response:
                if int(response.status) < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    raise TimeoutError(
        f"Browser Serving Gateway did not become ready: {health_url}"
    )


def run_all(config: BrowserServingConfig | None = None) -> None:
    resolved = config or BrowserServingConfig.from_environment()
    gateway_thread = threading.Thread(
        target=run_gateway,
        args=(resolved,),
        name="a3game-browser-gateway",
        daemon=True,
    )
    gateway_thread.start()
    _wait_for_gateway(resolved)
    launch_admin(resolved)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AAAGameForge Browser Serving",
    )
    parser.add_argument(
        "service",
        choices=("all", "gateway", "admin"),
        nargs="?",
        default="all",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = BrowserServingConfig.from_environment()
    if args.service == "gateway":
        run_gateway(config)
    elif args.service == "admin":
        launch_admin(config)
    else:
        run_all(config)


if __name__ == "__main__":
    main()
