"""Asset administration UI on port 7860 using only Browser Serving APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gradio as gr

from ..client import BrowserServingClient
from ..config import BrowserServingConfig


def _file_path(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else ""
    if isinstance(value, dict):
        value = value.get("path") or value.get("name") or ""
    return str(getattr(value, "name", value) or "")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _asset_name(asset: dict[str, Any]) -> str:
    return str(
        asset.get("metadata", {}).get("display_name")
        or asset.get("asset_id")
        or asset.get("native", {}).get("path", "").rsplit("/", 1)[-1]
        or asset.get("artifact_id")
        or "Asset"
    )


def _asset_choices(
    assets: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    return [
        (
            _asset_name(asset),
            str(asset.get("artifact_id") or ""),
        )
        for asset in assets
        if asset.get("artifact_id")
    ]


def _asset_rows(
    groups: dict[str, list[dict[str, Any]]],
    selected_type: str,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for group, assets in groups.items():
        if selected_type != "all" and selected_type != group:
            continue
        for asset in assets:
            native = dict(asset.get("native") or {})
            metadata = dict(asset.get("metadata") or {})
            rows.append(
                [
                    group,
                    _asset_name(asset),
                    str(native.get("class") or ""),
                    str(metadata.get("skeleton_path") or ""),
                    str(native.get("path") or ""),
                    str(asset.get("artifact_id") or ""),
                ]
            )
    return rows


def _world_choices(
    worlds: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    return [
        (
            str(world.get("world_id") or world.get("package_id")),
            str(world.get("package_id") or ""),
        )
        for world in worlds
        if world.get("package_id")
    ]


def build_admin_app(
    client: BrowserServingClient | None = None,
    *,
    config: BrowserServingConfig | None = None,
) -> gr.Blocks:
    resolved = config or BrowserServingConfig.from_environment()
    api = client or BrowserServingClient(
        resolved.public_gateway_url,
        engine=resolved.default_engine,
        timeout=180.0,
    )

    def check_connection(engine: str) -> str:
        return _json(api.engine_status(engine))

    def refresh_assets(
        engine: str,
        asset_type: str,
        selected_avatar: str = "",
        selected_motion: str = "",
    ):
        groups = api.assets.groups(engine=engine)
        avatars = list(groups.get("avatar") or [])
        motions = list(groups.get("motion") or [])
        avatar_values = {
            value for _label, value in _asset_choices(avatars)
        }
        motion_values = {
            value for _label, value in _asset_choices(motions)
        }
        return (
            groups,
            gr.update(
                choices=[
                    ("请选择 Avatar", ""),
                    *_asset_choices(avatars),
                ],
                value=(
                    selected_avatar
                    if selected_avatar in avatar_values
                    else ""
                ),
            ),
            gr.update(
                choices=[
                    ("请选择 Motion", ""),
                    *_asset_choices(motions),
                ],
                value=(
                    selected_motion
                    if selected_motion in motion_values
                    else ""
                ),
            ),
            _asset_rows(groups, asset_type or "all"),
            (
                f"Avatar: {len(avatars)}\n"
                f"Motion: {len(motions)}\n"
                f"全部资产: {sum(len(items) for items in groups.values())}"
            ),
        )

    def refresh_worlds(engine: str):
        worlds = api.worlds.list(engine=engine)
        return (
            worlds,
            gr.update(
                choices=[
                    ("请选择 Scene / World", ""),
                    *_world_choices(worlds),
                ],
                value="",
            ),
            [
                [
                    str(item.get("world_id") or ""),
                    str(item.get("project_id") or ""),
                    str(item.get("state") or ""),
                    str(item.get("package_id") or ""),
                ]
                for item in worlds
            ],
            f"Runtime World: {len(worlds)}",
        )

    def upload_asset(
        file_value: Any,
        asset_type: str,
        engine: str,
        destination: str,
        skeleton: str = "",
    ) -> str:
        path = _file_path(file_value)
        if not path:
            raise gr.Error("请选择上传文件")
        options = {}
        if skeleton:
            options["skeleton"] = skeleton
        return _json(
            api.assets.upload(
                path,
                asset_type,
                engine=engine,
                destination=destination,
                options=options,
            )
        )

    def ensure_session(
        engine: str,
        session_id: str,
    ) -> tuple[str, dict[str, Any]]:
        if session_id:
            try:
                current = api.sessions.get(session_id, engine=engine)
                if current.get("ok"):
                    return session_id, current
            except Exception:
                pass
        created = api.sessions.create(
            engine=engine,
            user_id="asset_admin",
            participant_id="asset_admin",
        )
        return str(created.get("session_id") or ""), created

    def present_avatar(
        engine: str,
        session_id: str,
        avatar_id: str,
        idle_motion: str,
        move_motion: str,
    ):
        if not avatar_id:
            raise gr.Error("请先选择 Avatar")
        resolved_session, created = ensure_session(engine, session_id)
        configured = api.sessions.configure(
            resolved_session,
            {
                "avatar_id": avatar_id,
                "idle_animation": idle_motion,
                "move_animation": move_motion,
            },
            engine=engine,
        )
        return (
            resolved_session,
            _json(
                {
                    "session": created,
                    "configure": configured,
                }
            ),
        )

    def play_motion(
        engine: str,
        session_id: str,
        motion_id: str,
    ):
        if not motion_id:
            raise gr.Error("请先选择 Motion")
        resolved_session, created = ensure_session(engine, session_id)
        result = api.sessions.play_preview_animation(
            resolved_session,
            motion_id,
            engine=engine,
        )
        return (
            resolved_session,
            _json({"session": created, "animation": result}),
        )

    def stop_session(engine: str, session_id: str):
        if not session_id:
            return "", _json({"ok": True, "removed": False})
        result = api.sessions.stop(session_id, engine=engine)
        return "", _json(result)

    def upload_scene(
        file_value: Any,
        engine: str,
        world_id: str,
        project_id: str,
        publish: bool,
    ) -> str:
        path = _file_path(file_value)
        if not path:
            raise gr.Error("请选择 Scene 文件")
        return _json(
            api.worlds.upload(
                path,
                engine=engine,
                world_id=world_id,
                project_id=project_id,
                publish=publish,
            )
        )

    preview_url = (
        resolved.public_gateway_url
        + "?embed=1&mode=editor"
    )
    preview_html = (
        '<div style="width:100%;aspect-ratio:16/9;min-height:360px;'
        'background:#050505;border:1px solid #d1d5db;overflow:hidden">'
        f'<iframe src="{preview_url}" title="Engine browser preview" '
        'allow="fullscreen;autoplay;clipboard-read;clipboard-write;'
        'pointer-lock" style="width:100%;height:100%;border:0"></iframe>'
        "</div>"
    )

    responsive_css = """
    .gradio-container {
      width: 100% !important;
      max-width: 1240px !important;
      overflow-x: hidden;
      box-sizing: border-box !important;
    }
    @media (max-width: 600px) {
      .gradio-container {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        padding: 12px !important;
      }
      .gradio-container * {
        min-width: 0 !important;
        max-width: 100% !important;
        box-sizing: border-box !important;
      }
      .gradio-container h1 {
        font-size: 24px !important;
        line-height: 1.25 !important;
        overflow-wrap: anywhere !important;
      }
      .gradio-container p {
        overflow-wrap: anywhere !important;
        word-break: break-word !important;
      }
      .gradio-container .row,
      .gradio-container .gradio-row {
        flex-direction: column !important;
        flex-wrap: wrap !important;
      }
      .gradio-container .row > *,
      .gradio-container .gradio-row > * {
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
      }
      .gradio-container button {
        min-width: 0 !important;
        max-width: 100% !important;
        white-space: normal !important;
      }
      .gradio-container iframe { max-width: 100% !important; }
    }
    """
    with gr.Blocks(
        title="AAAGameForge 资产上传 / 导入",
        css=responsive_css,
        fill_width=True,
    ) as app:
        gr.HTML(f"<style>{responsive_css}</style>")
        groups_state = gr.State({})
        worlds_state = gr.State([])
        session_state = gr.State("")

        gr.Markdown("# AAAGameForge 资产上传 / 导入调试页")
        gr.Markdown(
            "此页面只调用 Browser Serving API。引擎通过 backend contract "
            "接入，UE5 是当前 example backend。"
        )
        with gr.Row():
            engine = gr.Dropdown(
                choices=[("Unreal Engine 5 Example", "ue5")],
                value=resolved.default_engine,
                label="Engine",
            )
            check_button = gr.Button("检查引擎连接")
            refresh_button = gr.Button("刷新资产列表", variant="primary")
            clear_button = gr.Button("清除当前展示 / 会话")
        connection_output = gr.Textbox(
            label="连接状态",
            lines=6,
        )

        gr.Markdown("### Engine Pixel Streaming 预览")
        preview_frame = gr.HTML(preview_html)
        preview_refresh_button = gr.Button("刷新预览窗口")

        gr.Markdown("## 1. 选择 / 导入 Avatar")
        with gr.Row():
            avatar_dropdown = gr.Dropdown(label="Avatar")
            avatar_file = gr.File(label="上传 Avatar 文件")
        avatar_destination = gr.Textbox(
            label="目标目录",
            placeholder="留空使用 backend 默认目录",
        )
        with gr.Row():
            import_avatar_button = gr.Button(
                "导入 Avatar",
                variant="primary",
            )
            present_existing_button = gr.Button("展示当前 Avatar")

        gr.Markdown("## 2. 给当前 Avatar 导入 / 播放 Motion")
        with gr.Row():
            motion_dropdown = gr.Dropdown(label="Motion")
            motion_file = gr.File(label="上传 Motion 文件")
        motion_destination = gr.Textbox(label="Motion 目标目录")
        motion_skeleton = gr.Textbox(
            label="Skeleton Path",
            placeholder="UE Motion 导入需要 Skeleton",
        )
        with gr.Row():
            import_motion_button = gr.Button(
                "导入 Motion",
                variant="primary",
            )
            play_existing_motion_button = gr.Button(
                "播放当前 Motion"
            )
        idle_motion = gr.Dropdown(label="Idle Motion")
        move_motion = gr.Dropdown(label="Move Motion")

        gr.Markdown("## 3. 已导入引擎资产")
        with gr.Row():
            browser_asset_type = gr.Dropdown(
                choices=[
                    "all",
                    "avatar",
                    "skeleton",
                    "motion",
                    "environment",
                    "prop",
                    "weapon",
                    "effect",
                    "material",
                    "texture",
                ],
                value="all",
                label="资产类型",
            )
            asset_stats = gr.Textbox(label="统计", lines=4)
        asset_browser = gr.Dataframe(
            headers=[
                "Type",
                "Name",
                "Class",
                "Skeleton",
                "Native Path",
                "Artifact ID",
            ],
            interactive=False,
        )

        gr.Markdown("## 4. Scene / World 导入与 Runtime Package")
        with gr.Row():
            world_dropdown = gr.Dropdown(label="Scene / World")
            refresh_worlds_button = gr.Button("刷新 Scene / World")
        scene_file = gr.File(label="上传 Scene 文件")
        with gr.Row():
            scene_world_id = gr.Textbox(label="World ID")
            scene_project_id = gr.Textbox(label="Project ID")
            scene_publish = gr.Checkbox(label="发布 Runtime Package", value=True)
        import_scene_button = gr.Button(
            "导入 Scene / 构建 World",
            variant="primary",
        )
        world_stats = gr.Textbox(label="World 统计")
        world_browser = gr.Dataframe(
            headers=["World ID", "Project ID", "State", "Package ID"],
            interactive=False,
        )

        gr.Markdown("## 5. 通用资产导入")
        with gr.Row():
            generic_file = gr.File(label="上传通用资产")
            generic_type = gr.Dropdown(
                choices=[
                    "prop",
                    "weapon",
                    "effect",
                    "material",
                    "texture",
                ],
                value="prop",
                label="资产类型",
            )
            generic_destination = gr.Textbox(label="目标目录")
        import_generic_button = gr.Button("导入通用资产")

        operation_output = gr.Textbox(
            label="操作结果",
            lines=14,
        )

        check_button.click(
            check_connection,
            inputs=[engine],
            outputs=[connection_output],
        )
        preview_refresh_button.click(
            lambda: (
                '<iframe src="'
                + preview_url
                + f'&refresh={Path.cwd().stat().st_mtime_ns}" '
                'style="width:100%;height:520px;border:0" '
                'allow="fullscreen;autoplay;pointer-lock"></iframe>'
            ),
            outputs=[preview_frame],
        )
        refresh_event = refresh_button.click(
            refresh_assets,
            inputs=[
                engine,
                browser_asset_type,
                avatar_dropdown,
                motion_dropdown,
            ],
            outputs=[
                groups_state,
                avatar_dropdown,
                motion_dropdown,
                asset_browser,
                asset_stats,
            ],
        )
        refresh_event.then(
            lambda groups: (
                gr.update(
                    choices=[
                        ("请选择 Idle Motion", ""),
                        *_asset_choices(
                            list((groups or {}).get("motion") or [])
                        ),
                    ]
                ),
                gr.update(
                    choices=[
                        ("请选择 Move Motion", ""),
                        *_asset_choices(
                            list((groups or {}).get("motion") or [])
                        ),
                    ]
                ),
            ),
            inputs=[groups_state],
            outputs=[idle_motion, move_motion],
        )
        browser_asset_type.change(
            lambda groups, kind: _asset_rows(groups or {}, kind),
            inputs=[groups_state, browser_asset_type],
            outputs=[asset_browser],
        )
        import_avatar_button.click(
            upload_asset,
            inputs=[
                avatar_file,
                gr.State("avatar"),
                engine,
                avatar_destination,
                gr.State(""),
            ],
            outputs=[operation_output],
        ).then(
            refresh_assets,
            inputs=[
                engine,
                browser_asset_type,
                avatar_dropdown,
                motion_dropdown,
            ],
            outputs=[
                groups_state,
                avatar_dropdown,
                motion_dropdown,
                asset_browser,
                asset_stats,
            ],
        )
        import_motion_button.click(
            upload_asset,
            inputs=[
                motion_file,
                gr.State("motion"),
                engine,
                motion_destination,
                motion_skeleton,
            ],
            outputs=[operation_output],
        )
        present_existing_button.click(
            present_avatar,
            inputs=[
                engine,
                session_state,
                avatar_dropdown,
                idle_motion,
                move_motion,
            ],
            outputs=[session_state, operation_output],
        )
        play_existing_motion_button.click(
            play_motion,
            inputs=[
                engine,
                session_state,
                motion_dropdown,
            ],
            outputs=[session_state, operation_output],
        )
        clear_button.click(
            stop_session,
            inputs=[engine, session_state],
            outputs=[session_state, operation_output],
        )
        refresh_worlds_button.click(
            refresh_worlds,
            inputs=[engine],
            outputs=[
                worlds_state,
                world_dropdown,
                world_browser,
                world_stats,
            ],
        )
        import_scene_button.click(
            upload_scene,
            inputs=[
                scene_file,
                engine,
                scene_world_id,
                scene_project_id,
                scene_publish,
            ],
            outputs=[operation_output],
        ).then(
            refresh_worlds,
            inputs=[engine],
            outputs=[
                worlds_state,
                world_dropdown,
                world_browser,
                world_stats,
            ],
        )
        import_generic_button.click(
            upload_asset,
            inputs=[
                generic_file,
                generic_type,
                engine,
                generic_destination,
                gr.State(""),
            ],
            outputs=[operation_output],
        )

    return app


def launch_admin(
    config: BrowserServingConfig | None = None,
) -> None:
    resolved = config or BrowserServingConfig.from_environment()
    build_admin_app(config=resolved).launch(
        server_name=resolved.admin_host,
        server_port=resolved.admin_port,
        inbrowser=False,
        show_error=True,
    )
