"""Configuration for browser serving and its local frontends."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve(strict=False) if value else None


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BrowserServingConfig:
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 7870
    admin_host: str = "127.0.0.1"
    admin_port: int = 7860
    public_gateway_url: str = "http://127.0.0.1:7870"
    upload_game_id: str = "_browser_uploads"
    upload_run_id: str = "browser_uploads"
    default_engine: str = "ue5"
    ue_project: Path | None = None
    ue_root: Path | None = None
    ue_host: str = "127.0.0.1"
    ue_remote_port: int = 30010
    runtime_host: str = "127.0.0.1"
    base_runtime_port: int = 31020
    pixel_host: str = "127.0.0.1"
    base_pixel_http_port: int = 18080
    base_pixel_streamer_port: int = 18888
    base_pixel_sfu_port: int = 18889
    session_port_stride: int = 10
    max_sessions: int = 8
    preview_map: str = ""
    render_offscreen: bool = True
    dry_run: bool = False
    pixel_use_frontend: bool = True
    pixel_start_timeout: float = 120.0

    @classmethod
    def from_environment(cls) -> "BrowserServingConfig":
        gateway_host = os.environ.get(
            "A3GAME_BROWSER_GATEWAY_HOST",
            "127.0.0.1",
        )
        gateway_port = int(
            os.environ.get("A3GAME_BROWSER_GATEWAY_PORT", "7870")
        )
        public_url = os.environ.get(
            "A3GAME_BROWSER_PUBLIC_URL",
            f"http://127.0.0.1:{gateway_port}",
        ).rstrip("/")
        return cls(
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            admin_host=os.environ.get(
                "A3GAME_BROWSER_ADMIN_HOST",
                "127.0.0.1",
            ),
            admin_port=int(
                os.environ.get("A3GAME_BROWSER_ADMIN_PORT", "7860")
            ),
            public_gateway_url=public_url,
            upload_game_id=os.environ.get(
                "A3GAME_BROWSER_UPLOAD_GAME_ID",
                "_browser_uploads",
            ),
            upload_run_id=os.environ.get(
                "A3GAME_BROWSER_UPLOAD_RUN_ID",
                "browser_uploads",
            ),
            default_engine=os.environ.get(
                "A3GAME_BROWSER_ENGINE",
                "ue5",
            ),
            ue_project=_path_env("A3GAME_UE_PROJECT"),
            ue_root=_path_env("A3GAME_UE_ROOT"),
            ue_host=os.environ.get("A3GAME_UE_HOST", "127.0.0.1"),
            ue_remote_port=int(
                os.environ.get("A3GAME_UE_PORT", "30010")
            ),
            runtime_host=os.environ.get(
                "A3GAME_UE_RUNTIME_HOST",
                "127.0.0.1",
            ),
            base_runtime_port=int(
                os.environ.get(
                    "A3GAME_BROWSER_BASE_RUNTIME_PORT",
                    "31020",
                )
            ),
            pixel_host=os.environ.get(
                "A3GAME_BROWSER_PIXEL_HOST",
                "127.0.0.1",
            ),
            base_pixel_http_port=int(
                os.environ.get(
                    "A3GAME_BROWSER_BASE_PIXEL_HTTP_PORT",
                    "18080",
                )
            ),
            base_pixel_streamer_port=int(
                os.environ.get(
                    "A3GAME_BROWSER_BASE_PIXEL_STREAMER_PORT",
                    "18888",
                )
            ),
            base_pixel_sfu_port=int(
                os.environ.get(
                    "A3GAME_BROWSER_BASE_PIXEL_SFU_PORT",
                    "18889",
                )
            ),
            session_port_stride=int(
                os.environ.get(
                    "A3GAME_BROWSER_SESSION_PORT_STRIDE",
                    "10",
                )
            ),
            max_sessions=int(
                os.environ.get(
                    "A3GAME_BROWSER_MAX_SESSIONS",
                    "8",
                )
            ),
            preview_map=os.environ.get(
                "A3GAME_BROWSER_PREVIEW_MAP",
                "",
            ).strip(),
            render_offscreen=_flag(
                "A3GAME_BROWSER_RENDER_OFFSCREEN",
                True,
            ),
            dry_run=_flag("A3GAME_BROWSER_DRY_RUN", False),
            pixel_use_frontend=_flag(
                "A3GAME_BROWSER_PIXEL_USE_FRONTEND",
                True,
            ),
            pixel_start_timeout=float(
                os.environ.get(
                    "A3GAME_BROWSER_PIXEL_START_TIMEOUT",
                    "120",
                )
            ),
        )
