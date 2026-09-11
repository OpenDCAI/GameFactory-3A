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
    browser_play_dir: Path | None = None
    upload_game_id: str = "_browser_uploads"
    upload_run_id: str = "browser_uploads"
    default_engine: str = "ue5"
    ue_project: Path | None = None
    ue_root: Path | None = None
    ue_host: str = "127.0.0.1"
    ue_remote_port: int = 30010
    unity_project: Path | None = None
    unity_root: Path | None = None
    unity_host: str = "127.0.0.1"
    unity_port: int = 30010
    unity_webgl_build: Path | None = None
    godot_project: Path | None = None
    godot_executable: Path | None = None
    godot_host: str = "127.0.0.1"
    godot_runtime_port: int = 30050
    godot_auto_open_browser: bool = False
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
    pixel_use_frontend: bool = False
    pixel_start_timeout: float = 120.0
    cg_video_enabled: bool = True
    cg_video_allow_cloud: bool = False
    cg_video_max_workers: int = 1

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
            browser_play_dir=_path_env(
                "A3GAME_BROWSER_PLAY_DIR"
            ),
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
            unity_project=_path_env("A3GAME_UNITY_PROJECT"),
            unity_root=_path_env("A3GAME_UNITY_ROOT"),
            unity_host=os.environ.get(
                "A3GAME_UNITY_HOST",
                "127.0.0.1",
            ),
            unity_port=int(
                os.environ.get("A3GAME_UNITY_PORT", "30010")
            ),
            unity_webgl_build=_path_env(
                "A3GAME_UNITY_WEBGL_BUILD"
            ),
            godot_project=_path_env("A3GAME_GODOT_PROJECT"),
            godot_executable=_path_env("A3GAME_GODOT_EXECUTABLE"),
            godot_host=os.environ.get(
                "A3GAME_GODOT_HOST",
                "127.0.0.1",
            ),
            godot_runtime_port=int(
                os.environ.get("A3GAME_GODOT_RUNTIME_PORT", "30050")
            ),
            godot_auto_open_browser=_flag(
                "A3GAME_GODOT_AUTO_OPEN_BROWSER",
                False,
            ),
            runtime_host=os.environ.get(
                "A3GAME_UNITY_RUNTIME_HOST",
                os.environ.get(
                    "A3GAME_UE_RUNTIME_HOST",
                    "127.0.0.1",
                ),
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
                False,
            ),
            pixel_start_timeout=float(
                os.environ.get(
                    "A3GAME_BROWSER_PIXEL_START_TIMEOUT",
                    "120",
                )
            ),
            cg_video_enabled=_flag(
                "A3GAME_BROWSER_CG_VIDEO_ENABLED",
                True,
            ),
            cg_video_allow_cloud=_flag(
                "A3GAME_BROWSER_CG_VIDEO_ALLOW_CLOUD",
                False,
            ),
            cg_video_max_workers=int(
                os.environ.get(
                    "A3GAME_BROWSER_CG_VIDEO_MAX_WORKERS",
                    "1",
                )
            ),
        )
