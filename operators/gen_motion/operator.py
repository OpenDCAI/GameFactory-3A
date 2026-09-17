"""Unified operator for rigging, text-to-motion and motion retargeting."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


TASK_KIND = "motion"
SUPPORTED_TASK_TYPES = {
    "retarget", "rig", "text_to_motion", "humanoid",
    # Cloud-backend equivalents: no local weights, no subprocess, no BVH step.
    # "cloud_rig"      → TripoRiggingModel only  (analogous to "rig")
    # "cloud_humanoid" → check + rig + animate    (analogous to "humanoid")
    "cloud_rig", "cloud_humanoid",
    # Zero-weight analytic backend: fits a rig to mesh geometry, solves skin
    # weights and generates a preset clip with no model and no GPU.
    # "vibe"          → skeleton + skinning + clip
    # "vibe_retarget" → the above, then the normal bpy retarget stage
    "vibe", "vibe_retarget",
}

#: Artifact key -> filename, one directory per task.
_PER_GAME_NAMES = {
    "rig_path": "rig.txt",
    "skeleton_path": "skeleton.txt",
    "mesh_obj_path": "mesh.obj",
    "motion_bvh_path": "motion.bvh",
    "raw_motion_bvh_path": "motion_raw.bvh",
    "ik_motion_bvh_path": "motion_ik.bvh",
    "joints_npy_path": "joints.npy",
    "preview_mp4_path": "preview.mp4",
    "retargeted_fbx_path": "retargeted.fbx",
    "anim_only_fbx_path": "animation.fbx",
    "mapping_path": "mapping.json",
    "retarget_info_path": "retarget_info.json",
    "rigged_glb_path": "rigged.glb",
    "animated_glb_path": "animated.glb",
    "rig_check_path": "rig_check.json",
    "rig_report_path": "rig_report.json",
    "anim_report_path": "anim_report.json",
    "converted_path": "converted.fbx",
    "skin_report_path": "skin_report.json",
    "vibe_report_path": "vibe_report.json",
}

#: The same artifacts under the flat layout, where one directory holds several
#: tasks and the filename carries the task id.
_LEGACY_SUFFIXES = {
    "rig_path": "_rig.txt",
    "skeleton_path": "_skeleton.txt",
    "mesh_obj_path": "_mesh.obj",
    "motion_bvh_path": "_motion.bvh",
    "raw_motion_bvh_path": "_motion_raw.bvh",
    "ik_motion_bvh_path": "_motion_ik.bvh",
    "joints_npy_path": "_joints.npy",
    "preview_mp4_path": "_preview.mp4",
    "retargeted_fbx_path": ".fbx",
    "anim_only_fbx_path": "_anim_only.fbx",
    "mapping_path": "_mapping.json",
    "retarget_info_path": "_retarget_info.json",
    "rigged_glb_path": "_rigged.glb",
    "animated_glb_path": "_animated.glb",
    "rig_check_path": "_rig_check.json",
    "rig_report_path": "_rig_report.json",
    "anim_report_path": "_anim_report.json",
    "converted_path": "_converted.fbx",
    "skin_report_path": "_skin_report.json",
    "vibe_report_path": "_vibe_report.json",
}


class GenMotionOperator:
    """Run one of the four human-motion stages under the shared motion kind."""

    def __init__(
        self,
        bpy_python: str | None = None,
        output_dir: Optional[str] = None,
        run_id: str = "default",
        default_game_id: Optional[str] = None,
        *,
        puppeteer_model: Any | None = None,
        momask_model: Any | None = None,
        # Cloud backend slots. Injected the same way the local ones are, so the
        # operator never learns which provider is behind them (R6 swappability).
        rig_check_model: Any | None = None,
        cloud_rig_model: Any | None = None,
        cloud_animation_model: Any | None = None,
        cloud_format_model: Any | None = None,
        device: str = "cpu",
        verbose: bool = False,
        retarget_fn: Callable[..., dict] | None = None,
    ):
        self.bpy_python = str(bpy_python) if bpy_python else None
        self.puppeteer_model = puppeteer_model
        self.momask_model = momask_model
        self.rig_check_model = rig_check_model
        self.cloud_rig_model = cloud_rig_model
        self.cloud_animation_model = cloud_animation_model
        self.cloud_format_model = cloud_format_model
        self.run_id = run_id
        self.default_game_id = default_game_id
        self.device = str(device)
        self.verbose = bool(verbose)
        self.retarget_fn = retarget_fn
        self.output_dir = Path(output_dir) if output_dir else None
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_outputs(
        self,
        inp: dict,
        task_id: str,
    ) -> tuple[str, Path, dict[str, Path]]:
        if self.output_dir is not None:
            root = self.output_dir
            outputs = {
                key: root / f"{task_id}{suffix}"
                for key, suffix in _LEGACY_SUFFIXES.items()
            }
            return "", root, outputs

        from pipeline.common import paths

        game_id = paths.infer_game_id(inp, fallback=self.default_game_id)
        root = paths.task_output_dir(
            game_id,
            TASK_KIND,
            task_id,
            run_id=self.run_id,
        )
        outputs = {
            key: root / filename
            for key, filename in _PER_GAME_NAMES.items()
        }
        return game_id, root, outputs

    @staticmethod
    def _required_path(inp: dict, key: str, *aliases: str) -> Path:
        keys = (key, *aliases)
        value = next((inp[name] for name in keys if inp.get(name)), None)
        if not value:
            raise ValueError(
                f"Motion task requires {key!r}."
                + (f" (or {', '.join(map(repr, aliases))})" if aliases else "")
            )
        from pipeline.common import paths

        path = paths.resolve_input_path(value)
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"{key} is missing or empty: {path}")
        return path

    def _target_mesh(self, inp: dict) -> Path:
        """
        The character mesh, whatever the task called it.

        ``target_glb_path`` predates OBJ support and is still what most task
        files say, so both names resolve here and the extension check is what
        actually decides whether the file is usable.
        """
        from .funcs.retarget_motion import normalise_mesh_ext

        mesh = self._required_path(inp, "target_mesh_path", "target_glb_path")
        normalise_mesh_ext(mesh.suffix)
        return mesh

    def _source_motion(self, inp: dict, outputs: dict[str, Path]) -> Path:
        """
        The clip to retarget, from disk or from an external library.

        A task either points at a file it already has (``source_motion_path``)
        or names a library to take one from (``motion_source`` with a path,
        URL or archive member). The second form exists because generated
        motion runs out of range long before a game does — see
        ``funcs.fetch_motion``.
        """
        if inp.get("motion_source"):
            from .funcs.fetch_motion import fetch_motion

            fetched = fetch_motion(
                source=str(inp["motion_source"]),
                path=inp.get("source_motion_path"),
                url=inp.get("source_motion_url"),
                member=inp.get("source_motion_member"),
                dest_dir=str(outputs["retargeted_fbx_path"].parent),
            )
            return Path(fetched["motion_path"])
        return self._required_path(inp, "source_motion_path")

    @staticmethod
    def _required_prompt(inp: dict) -> str:
        prompt = inp.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Motion task requires a non-empty 'prompt'.")
        return prompt.strip()

    def _rig(
        self,
        target_mesh: Path,
        outputs: dict[str, Path],
        inp: dict,
        seed: int,
    ) -> dict[str, Any]:
        if self.puppeteer_model is None:
            raise RuntimeError(
                "task_type='rig' and 'humanoid' require PuppeteerModel. "
                "Configure --puppeteer-model-path and --puppeteer-python."
            )
        from .funcs.rig_character import rig_character

        artifacts = rig_character(
            target_mesh.read_bytes(),
            self.puppeteer_model,
            mesh_format=target_mesh.suffix,
            seed=seed,
            post_filter=bool(inp.get("post_filter", True)),
        )
        _write_text(outputs["rig_path"], artifacts.get("rig_text"), "rig")
        _write_text(
            outputs["skeleton_path"],
            artifacts.get("skeleton_text"),
            "skeleton",
        )
        _write_bytes(
            outputs["mesh_obj_path"],
            artifacts.get("mesh_obj_bytes"),
            "mesh OBJ",
        )
        return artifacts

    def _generate_motion(
        self,
        prompt: str,
        outputs: dict[str, Path],
        inp: dict,
        seed: int,
    ) -> dict[str, Any]:
        if self.momask_model is None:
            raise RuntimeError(
                "task_type='text_to_motion' and 'humanoid' require "
                "MoMaskModel. Configure --momask-model-path and "
                "--momask-python."
            )
        from .funcs.generate_motion import generate_motion

        artifacts = generate_motion(
            prompt,
            self.momask_model,
            seed=seed,
            motion_length=int(inp.get("motion_length", 0)),
            repeat_times=int(inp.get("repeat_times", 1)),
            cond_scale=float(inp.get("cond_scale", 4.0)),
            time_steps=int(inp.get("time_steps", 18)),
            temperature=float(inp.get("temperature", 1.0)),
            use_ik=bool(inp.get("use_ik", True)),
            in_place=bool(inp.get("in_place", False)),
            in_place_lock_height=bool(
                inp.get("in_place_lock_height", False)
            ),
        )
        _write_bytes(
            outputs["motion_bvh_path"],
            artifacts.get("bvh_bytes"),
            "selected motion BVH",
        )
        optional = (
            ("raw_motion_bvh_path", "raw_bvh_bytes"),
            ("ik_motion_bvh_path", "ik_bvh_bytes"),
            ("preview_mp4_path", "preview_mp4_bytes"),
        )
        for output_key, artifact_key in optional:
            value = artifacts.get(artifact_key)
            if value:
                _write_bytes(outputs[output_key], value, artifact_key)

        joints = artifacts.get("joints")
        if joints is not None:
            import numpy as np

            outputs["joints_npy_path"].parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            np.save(outputs["joints_npy_path"], joints, allow_pickle=False)
        return artifacts

    @staticmethod
    def _resolve_mapping(inp: dict) -> Path | None:
        """Return an explicit ``mapping_path``, or ``None`` to auto-derive."""
        mapping_value = inp.get("mapping_path")
        if not mapping_value:
            return None
        from pipeline.common import paths
        from .funcs.retarget_utils.validate_mapping import (
            load_and_validate_mapping,
        )

        mapping_path = paths.resolve_input_path(mapping_value)
        load_and_validate_mapping(mapping_path)
        return mapping_path

    def _retarget(
        self,
        source_motion: Path,
        target_mesh: Path,
        target_rig: Path,
        outputs: dict[str, Path],
        inp: dict,
        *,
        fps: int,
    ) -> dict[str, Any]:
        from .funcs.retarget_motion import (
            normalise_mesh_ext,
            normalise_source_ext,
        )

        normalise_source_ext(source_motion.suffix)
        normalise_mesh_ext(target_mesh.suffix)

        mapping_path = self._resolve_mapping(inp)

        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")
        global_scale = float(inp.get("global_scale", 1.0))
        if global_scale <= 0:
            raise ValueError(
                f"global_scale must be positive, got {global_scale}"
            )
        root_scale_value = inp.get("root_scale")
        root_scale = (
            None if root_scale_value is None else float(root_scale_value)
        )
        max_delta_deg = float(inp.get("max_delta_deg", 0.0))
        if max_delta_deg < 0:
            raise ValueError(
                f"max_delta_deg must be non-negative, got {max_delta_deg}"
            )
        export_anim_only = bool(inp.get("export_anim_only", True))
        retarget_fn = self.retarget_fn
        if retarget_fn is None:
            if not self.bpy_python:
                raise RuntimeError(
                    "Motion retargeting requires a bpy Python executable. "
                    "Pass bpy_python to GenMotionOperator or use --bpy-python."
                )
            from .funcs.retarget_motion import retarget_motion

            retarget_fn = retarget_motion

        return retarget_fn(
            bpy_python=self.bpy_python or "",
            source_motion_path=str(source_motion),
            target_mesh_path=str(target_mesh),
            target_rig_path=str(target_rig),
            output_path=str(outputs["retargeted_fbx_path"]),
            anim_only_output_path=str(outputs["anim_only_fbx_path"]),
            mapping_path=str(mapping_path) if mapping_path else None,
            mapping_output_path=str(outputs["mapping_path"]),
            info_output_path=str(outputs["retarget_info_path"]),
            fps=fps,
            global_scale=global_scale,
            root_scale=root_scale,
            max_delta_deg=max_delta_deg,
            bake_root_to_bone=bool(inp.get("bake_root_to_bone", False)),
            export_anim_only=export_anim_only,
            action_name=(
                str(inp["action_name"]) if inp.get("action_name") else None
            ),
            device=self.device,
            verbose=self.verbose,
        )

    def _vibe(
        self,
        outputs: dict[str, Path],
        inp: dict,
    ) -> dict[str, Any]:
        """Generate local procedural rig, skin, motion and optional animated GLB artifacts."""
        from .funcs.vibe_motion_utils.pipeline import generate_vibe_motion

        mesh = None
        creature = inp.get("creature")
        has_mesh = bool(inp.get("target_mesh_path") or inp.get("target_glb_path"))
        if has_mesh and creature is not None:
            raise ValueError("Pass either creature or target_mesh_path, not both")
        if inp.get("task_type", "").lower() == "vibe_retarget":
            if not has_mesh and creature is None:
                raise ValueError("vibe_retarget requires creature or target_mesh_path")
            if inp.get("skin", True) is not True:
                raise ValueError("vibe_retarget requires skin=true")
            fps = float(inp.get("fps", 30))
            if not fps.is_integer() or fps < 1:
                raise ValueError("vibe_retarget requires positive integer fps; use vibe for fractional-fps BVH")
        if has_mesh:
            from .funcs.vibe_motion_utils.skeleton import mesh_from_arrays

            mesh_path = self._target_mesh(inp)
            vertices, faces = _load_mesh_arrays(mesh_path)
            mesh = mesh_from_arrays(vertices, faces, name=mesh_path.stem)
            creature = None

        artifacts = generate_vibe_motion(
            preset=inp.get("preset"),
            segments=inp.get("segments"),
            transitions=inp.get("transitions"),
            creature=creature,
            mesh=mesh,
            template=str(inp.get("template", "biped_armed")),
            rig_preset=str(inp.get("rig_preset", "biped")),
            skin_preset=str(inp.get("skin_preset", "compact")),
            num_frames=inp.get("num_frames"),
            fps=inp.get("fps", 30.0),
            heading_deg=inp.get("heading_deg", 0.0),
            skin=inp.get("skin", True),
            motion_overrides=inp.get("motion_overrides"),
            rig_overrides=inp.get("rig_overrides"),
            skin_overrides=inp.get("skin_overrides"),
        )

        _write_bytes(
            outputs["motion_bvh_path"],
            artifacts.get("bvh_bytes"),
            "vibe motion BVH",
        )
        for output_key, artifact_key, label in (
            ("rig_path", "rig_text", "rig"),
            ("skeleton_path", "skeleton_text", "skeleton"),
        ):
            if artifacts.get(artifact_key):
                _write_text(outputs[output_key], artifacts[artifact_key], label)
        if artifacts.get("animated_glb_bytes"):
            _write_bytes(outputs["animated_glb_path"], artifacts["animated_glb_bytes"], "vibe animated GLB")
        if artifacts.get("mesh_obj_bytes"):
            _write_bytes(
                outputs["mesh_obj_path"],
                artifacts["mesh_obj_bytes"],
                "vibe mesh OBJ",
            )
        for output_key, artifact_key in (
            ("skin_report_path", "skin_report_json"),
            ("rig_report_path", "rig_report_json"),
            ("vibe_report_path", "vibe_report_json"),
        ):
            if artifacts.get(artifact_key):
                path = outputs[output_key]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(artifacts[artifact_key], encoding="utf-8")

        joints = artifacts.get("joints")
        if joints is not None:
            import numpy as np

            outputs["joints_npy_path"].parent.mkdir(parents=True, exist_ok=True)
            np.save(outputs["joints_npy_path"], joints, allow_pickle=False)

        failures = (artifacts.get("metrics") or {}).get("failures") or []
        if failures:
            logger.warning(
                "[gen_motion] vibe clip %s failed %s",
                artifacts.get("preset"),
                ", ".join(map(str, failures)),
            )
        for note in artifacts.get("notes") or []:
            logger.info("[gen_motion] vibe: %s", note)
        return artifacts

    def run(self, inp: dict) -> dict:
        """Execute a retarget, rig, text-to-motion or humanoid task."""
        task_type = str(inp.get("task_type", "retarget")).lower()
        if task_type not in SUPPORTED_TASK_TYPES:
            raise NotImplementedError(
                f"Unsupported motion task_type={task_type!r}. Supported: "
                + ", ".join(sorted(SUPPORTED_TASK_TYPES))
            )

        task_id = str(inp.get("task_id", f"task_{int(time.time())}"))
        seed = int(inp.get("seed", 42))
        game_id, task_dir, outputs = self._resolve_outputs(inp, task_id)
        target_mesh: Path | None = None
        source_motion: Path | None = None
        target_rig: Path | None = None
        rig_artifacts: dict[str, Any] | None = None
        motion_artifacts: dict[str, Any] | None = None
        retarget_artifacts: dict[str, Any] | None = None
        vibe_artifacts: dict[str, Any] | None = None

        t0 = time.time()
        if task_type in {"vibe", "vibe_retarget"}:
            vibe_artifacts = self._vibe(outputs, inp)
            motion_artifacts = vibe_artifacts
            if task_type == "vibe_retarget":
                if not all(vibe_artifacts.get(key) for key in (
                    "rig_text", "mesh_obj_bytes", "bvh_bytes", "skin_report_json",
                )):
                    raise RuntimeError("vibe_retarget requires a rig, weighted mesh and BVH from this run")
                target_mesh = outputs["mesh_obj_path"]
                target_rig = outputs["rig_path"]
                source_motion = outputs["motion_bvh_path"]
                retarget_artifacts = self._retarget(
                    source_motion,
                    target_mesh,
                    target_rig,
                    outputs,
                    inp,
                    fps=int(vibe_artifacts.get("fps", 30)),
                )

        if task_type in {"rig", "humanoid"}:
            target_mesh = self._target_mesh(inp)
            rig_artifacts = self._rig(target_mesh, outputs, inp, seed)

        if task_type in {"text_to_motion", "humanoid"}:
            motion_artifacts = self._generate_motion(
                self._required_prompt(inp),
                outputs,
                inp,
                seed,
            )

        if task_type == "retarget":
            target_mesh = self._target_mesh(inp)
            target_rig = self._required_path(inp, "target_rig_path")
            source_motion = self._source_motion(inp, outputs)
            retarget_artifacts = self._retarget(
                source_motion,
                target_mesh,
                target_rig,
                outputs,
                inp,
                fps=int(inp.get("fps", 30)),
            )
        elif task_type == "humanoid":
            # The generated clip and the rig just written, not anything the
            # task named: the three stages have to agree.
            source_motion = outputs["motion_bvh_path"]
            target_rig = outputs["rig_path"]
            retarget_artifacts = self._retarget(
                source_motion,
                target_mesh,
                target_rig,
                outputs,
                inp,
                fps=int((motion_artifacts or {}).get("fps", 20)),
            )

        # ── Cloud-backend branches ──────────────────────────────────────────────
        cloud_rig_result: dict[str, Any] | None = None
        cloud_anim_result: dict[str, Any] | None = None

        if task_type in {"cloud_rig", "cloud_humanoid"}:
            from models.gen_motion.tripo_rigging_model import (
                ANIMATABLE_SPEC,
                RIG_TYPES,
                UNCLASSIFIED,
                default_preset,
            )

            from .funcs.cloud_rig_animate import (
                animate_rigged,
                check_riggable,
                rig_mesh,
            )

            def _validated_rig_type(value: Any) -> str:
                """A `rig_type` the rigging endpoint accepts, or a clear refusal.

                Only reached when rig-check was skipped. An unknown value is
                rejected here rather than at the submit call, which would be
                billed and would report the mistake as a generic parameter error.
                """
                candidate = str(value).strip().lower()
                if candidate not in RIG_TYPES:
                    raise ValueError(
                        f"rig_type={value!r} is not accepted. Use one of "
                        f"{', '.join(sorted(RIG_TYPES))}, or drop "
                        "skip_rig_check and let rig-check classify the mesh."
                    )
                return candidate

            # A URL, because these endpoints fetch the mesh themselves. A local
            # path is still read when one is given, so the model layer can key
            # its cache on content rather than on the hosting URL.
            mesh_url = str(inp.get("mesh_url") or "").strip()
            if not mesh_url:
                raise ValueError(
                    "Cloud motion tasks need 'mesh_url' — a public http(s) "
                    "link to the mesh. The provider downloads it server-side; "
                    "there is no upload endpoint and a data: URI is rejected."
                )
            mesh_bytes: bytes | None = None
            if inp.get("target_mesh_path") or inp.get("target_glb_path"):
                target_mesh = self._target_mesh(inp)
                mesh_bytes = target_mesh.read_bytes()
                mesh_fmt = target_mesh.suffix
            else:
                mesh_fmt = "." + mesh_url.rsplit(".", 1)[-1].split("?")[0].lower()

            out_format = str(inp.get("out_format", "glb")).lower().lstrip(".")

            # Rig-check first: it reports whether the mesh can be rigged and
            # classifies its body plan into a `rig_type`.
            if self.rig_check_model is not None and not inp.get("skip_rig_check"):
                import json as _json

                check_result = check_riggable(
                    mesh_url, self.rig_check_model,
                    mesh_bytes=mesh_bytes, mesh_format=mesh_fmt,
                )
                check_path = outputs["rig_check_path"]
                check_path.parent.mkdir(parents=True, exist_ok=True)
                check_path.write_text(
                    _json.dumps(check_result["raw"], indent=2), encoding="utf-8"
                )
                if not check_result["riggable"]:
                    if check_result.get("unclassified"):
                        detail = (
                            "the mesh has no recognised body plan (rig_type="
                            f"{UNCLASSIFIED!r}), so there is no topology to rig "
                            "it as"
                        )
                    else:
                        detail = (
                            "the mesh was classified as "
                            f"{check_result['rig_type']!r} but still refused"
                        )
                    raise RuntimeError(
                        f"Rig-check refused this mesh: {detail}. Rigging it "
                        "anyway is billed and returns a skeleton whose joints "
                        "are mostly unnamed, which no preset clip can drive. "
                        "Supply a different mesh, or set skip_rig_check=true to "
                        "override deliberately."
                    )
                # Prefer the service's classification over any task guess.
                rig_type = check_result["rig_type"]
            else:
                rig_type = _validated_rig_type(inp.get("rig_type", "biped"))

            if self.cloud_rig_model is None:
                raise RuntimeError(
                    "task_type='cloud_rig' and 'cloud_humanoid' require a "
                    "cloud_rig_model. Pass TripoRiggingModel() to the operator."
                )

            # A rig that will be animated must use the tripo naming spec: the
            # retarget step refuses mixamo-named skeletons outright.
            rig_spec = str(inp.get("rig_spec", "tripo"))
            if task_type == "cloud_humanoid" and rig_spec != ANIMATABLE_SPEC:
                raise ValueError(
                    f"rig_spec={rig_spec!r} cannot be animated — the retarget "
                    f"step only accepts {ANIMATABLE_SPEC!r}. Use task_type="
                    "'cloud_rig' for a mixamo-named rig, or switch the spec."
                )

            cloud_rig_result = rig_mesh(
                mesh_url, self.cloud_rig_model,
                mesh_bytes=mesh_bytes, mesh_format=mesh_fmt,
                rig_type=rig_type,
                rig_spec=rig_spec,
                out_format=out_format,
                seed=seed,
                # Retried because rigging is non-deterministic. Each attempt is
                # billed, so the default is 1.
                attempts=int(inp.get("rig_attempts", 1)),
            )
            rigged_path = _with_ext(outputs["rigged_glb_path"], out_format)
            rigged_path.parent.mkdir(parents=True, exist_ok=True)
            rigged_path.write_bytes(_artifact_bytes(cloud_rig_result))
            outputs["rigged_glb_path"] = rigged_path

            # Written beside the rig: named vs unresolved joints and the
            # limb-chain count.
            rig_report = cloud_rig_result.get("rig_report")
            if rig_report:
                import json as _json

                from models.gen_motion.tripo_rigging_model import (
                    EXPECTED_LIMBS,
                    rig_quality_note,
                )

                report_path = outputs["rig_report_path"]
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    _json.dumps({
                        "rig_type": rig_type,
                        "attempts": cloud_rig_result.get("rig_attempts"),
                        "expected_limbs": EXPECTED_LIMBS.get(rig_type.lower(), 0),
                        **rig_report,
                    }, indent=2),
                    encoding="utf-8")
                logger.info("[gen_motion] rig: %s",
                            rig_quality_note(rig_report, rig_type))

            if task_type == "cloud_humanoid":
                if self.cloud_animation_model is None:
                    raise RuntimeError(
                        "task_type='cloud_humanoid' requires cloud_animation_model."
                        " Pass TripoAnimationModel() to the operator."
                    )
                # Chained by task id, not by file: the skeleton lives
                # server-side against the rigging task and expires after 24h.
                rig_task_id = str(cloud_rig_result.get("task_id") or "").strip()
                if not rig_task_id:
                    raise RuntimeError(
                        "The rigging step returned no task_id, so the animation "
                        "step has nothing to chain from. A cached rigging "
                        f"result cannot be animated; the file is at {rigged_path}."
                    )
                # The preset must match the rig's topology; the default is
                # derived from rig_type so the pair cannot drift.
                animation = str(
                    inp.get("animation") or default_preset(rig_type)
                )
                cloud_anim_result = animate_rigged(
                    rig_task_id,
                    self.cloud_animation_model,
                    animation,
                    out_format=out_format,
                    animate_in_place=bool(inp.get("animate_in_place", False)),
                    seed=seed,
                )
                anim_path = _with_ext(outputs["animated_glb_path"], out_format)
                anim_path.parent.mkdir(parents=True, exist_ok=True)
                anim_path.write_bytes(_artifact_bytes(cloud_anim_result))
                outputs["animated_glb_path"] = anim_path

                # Logged rather than raised: a flipped joint shears one limb
                # and leaves the rest of the body usable.
                if out_format == "glb":
                    import json as _json

                    from models.gen_motion.tripo_rigging_model import (
                        inspect_animation,
                    )

                    anim_report = inspect_animation(anim_path.read_bytes())
                    outputs["anim_report_path"].parent.mkdir(
                        parents=True, exist_ok=True)
                    outputs["anim_report_path"].write_text(
                        _json.dumps(anim_report, indent=2), encoding="utf-8")
                    if anim_report["flipped"]:
                        logger.warning(
                            "[gen_motion] retarget inverted %d joint(s) — the "
                            "mesh will be sheared around %s. Worst deviation "
                            "%.1f deg.",
                            len(anim_report["flipped"]),
                            ", ".join(n for n, _ in anim_report["flipped"][:3]),
                            anim_report["worst"])
                    else:
                        logger.info(
                            "[gen_motion] clip %s drives %d/%d joints, worst "
                            "first-frame deviation %.1f deg",
                            anim_report["clip"], anim_report["moving"],
                            anim_report["joints"], anim_report["worst"])

        elapsed = time.time() - t0
        result = {
            "task_id": task_id,
            "retargeted_fbx_path": _artifact_path(
                retarget_artifacts,
                "retargeted_fbx_path",
            ),
            "anim_only_fbx_path": _artifact_path(
                retarget_artifacts,
                "anim_only_fbx_path",
            ),
            "mapping_path": _artifact_path(
                retarget_artifacts,
                "mapping_path",
            ),
            "retarget_info_path": _artifact_path(
                retarget_artifacts,
                "retarget_info_path",
            ),
            "rig_path": _existing_path(outputs["rig_path"]),
            "skeleton_path": _existing_path(outputs["skeleton_path"]),
            "mesh_obj_path": _existing_path(outputs["mesh_obj_path"]),
            "motion_bvh_path": _existing_path(outputs["motion_bvh_path"]),
            "raw_motion_bvh_path": _existing_path(
                outputs["raw_motion_bvh_path"]
            ),
            "ik_motion_bvh_path": _existing_path(
                outputs["ik_motion_bvh_path"]
            ),
            "joints_npy_path": _existing_path(outputs["joints_npy_path"]),
            "preview_mp4_path": _existing_path(outputs["preview_mp4_path"]),
            # Cloud backend artifacts. Always present as keys (None when the
            # local backend ran) — never remove or repurpose a returned key.
            "rigged_glb_path": _existing_path(outputs["rigged_glb_path"]),
            "animated_glb_path": _existing_path(outputs["animated_glb_path"]),
            "rig_check_path": _existing_path(outputs["rig_check_path"]),
            "rig_report_path": _existing_path(outputs["rig_report_path"]),
            "anim_report_path": _existing_path(outputs["anim_report_path"]),
            "skin_report_path": _existing_path(outputs["skin_report_path"]),
            "rig_task_id": (cloud_rig_result or {}).get("task_id"),
            "animation_task_id": (cloud_anim_result or {}).get("task_id"),
            "elapsed_sec": round(elapsed, 2),
            "game_id": game_id,
            "task_kind": TASK_KIND,
            "task_type": task_type,
            "output_dir": str(task_dir),
        }

        if vibe_artifacts is not None:
            current = {
                "motion_bvh_path": "bvh_bytes", "joints_npy_path": "joints",
                "rig_path": "rig_text", "skeleton_path": "skeleton_text",
                "mesh_obj_path": "mesh_obj_bytes", "skin_report_path": "skin_report_json",
                "rig_report_path": "rig_report_json", "vibe_report_path": "vibe_report_json",
                "animated_glb_path": "animated_glb_bytes",
            }
            retarget_keys = {"retargeted_fbx_path", "anim_only_fbx_path", "mapping_path", "retarget_info_path"}
            for key in outputs.keys() - retarget_keys:
                artifact = current.get(key)
                result[key] = _existing_path(outputs[key]) if artifact and vibe_artifacts.get(artifact) is not None else None

        if self.output_dir is None:
            from pipeline.common import paths

            paths.write_task_meta(
                task_dir,
                {
                    **result,
                    "run_id": self.run_id,
                    "seed": seed,
                    "prompt": inp.get("prompt"),
                    "source_motion_path": (
                        str(source_motion) if source_motion else None
                    ),
                    "target_mesh_path": (
                        str(target_mesh) if target_mesh else None
                    ),
                    "target_rig_path": (
                        str(target_rig) if target_rig else None
                    ),
                    "fps": (
                        float(vibe_artifacts["fps"]) if vibe_artifacts is not None else (
                            int((motion_artifacts or {}).get("fps", 20))
                            if motion_artifacts else int(inp.get("fps", 30))
                        )
                    ),
                    "puppeteer_joint_count": (
                        (rig_artifacts or {}).get("joint_count")
                    ),
                    "puppeteer_skin_vertex_count": (
                        (rig_artifacts or {}).get("skin_vertex_count")
                    ),
                    "retarget_runtime": self.bpy_python,
                },
            )
        return result

    def run_batch(self, inputs: list[dict]) -> list[dict]:
        """Run tasks serially so large models never overlap in GPU memory."""
        return [self.run(inp) for inp in inputs]

    def eval(self, result: dict, task: dict) -> dict:
        """Evaluate existing artifacts only."""
        from .metrics import evaluate

        return evaluate(result, task)


def _load_mesh_arrays(path: Path) -> tuple[Any, Any]:
    """Read a mesh file into ``(vertices, faces)`` with vertex order preserved."""
    import numpy as np
    import trimesh

    mesh = trimesh.load(
        str(path), force="mesh", process=False, maintain_order=True
    )
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    return (
        np.asarray(mesh.vertices, dtype=np.float64),
        np.asarray(mesh.faces, dtype=np.int64),
    )


def _write_text(path: Path, value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Puppeteer returned no usable {label} text.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_bytes(path: Path, value: Any, label: str) -> None:
    if not isinstance(value, (bytes, bytearray)) or not value:
        raise RuntimeError(f"Model returned no usable {label} bytes.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(value))


def _existing_path(path: Path) -> str | None:
    return str(path) if path.is_file() and path.stat().st_size > 0 else None


def _with_ext(path: Path, ext: str) -> Path:
    """Re-suffix an artifact to match the container actually returned.

    The stem is fixed (`rigged`, `animated`) but the container is the task's
    choice, and a mismatched suffix is only reported later, by an importer.
    """
    return path.with_suffix(f".{ext.lstrip('.').lower()}")


def _artifact_bytes(result: dict[str, Any]) -> bytes:
    """The file content a cloud stage produced, under either returned key."""
    payload = result.get("file_bytes") or result.get("glb_bytes")
    return bytes(payload)  # type: ignore[arg-type]


def _artifact_path(
    artifacts: dict[str, Any] | None,
    key: str,
) -> str | None:
    if not artifacts:
        return None
    value = artifacts.get(key)
    return str(value) if value else None


__all__ = ["GenMotionOperator", "SUPPORTED_TASK_TYPES", "TASK_KIND"]
