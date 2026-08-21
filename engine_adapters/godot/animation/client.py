"""Stable animation operations for GodotClient v1."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .._internal import (
    inspection_bone_tracks,
    inspection_skeleton_paths,
    validate_resource_inspection,
)
from ..assets import GodotAssetsClient
from ..contracts import GodotOperationResult


class GodotAnimationClient:
    def __init__(self, assets: GodotAssetsClient) -> None:
        self._assets = assets

    def import_motion(
        self,
        source: Mapping[str, Any],
        *,
        skeleton: str,
        destination: str = "",
        avatar_name: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(skeleton or "").strip():
            return GodotOperationResult.failure(
                "animation.import_motion",
                "skeleton is required and must name a Skeleton3D NodePath",
            ).to_dict()
        result = self._assets.import_motion(
            source,
            skeleton=skeleton,
            destination=destination,
            avatar_name=avatar_name,
            options=options,
        )
        result["operation"] = "animation.import_motion"
        return result

    def resolve_skeleton(self, avatar: str) -> dict[str, Any]:
        operation = "animation.resolve_skeleton"
        try:
            record = self._assets._registry.find(avatar)
        except (OSError, TypeError, ValueError) as exc:
            return self._assets._registry_failure(
                operation,
                exc,
                payload={"avatar": str(avatar or "")},
            )
        if record is None or record.type != "avatar":
            return GodotOperationResult.failure(
                operation,
                f"Unknown registered avatar: {avatar}",
            ).to_dict()
        payload: dict[str, Any] = {
            "avatar": record.to_dict(),
            "resource_path": record.backend_path,
        }
        try:
            process, inspection = self._assets._inspect_native(record.backend_path)
            payload["process"] = process.to_dict()
            payload["inspection"] = inspection
            errors = validate_resource_inspection(inspection, "avatar")
            if process.returncode != 0 or errors:
                return GodotOperationResult.failure(
                    operation,
                    *(errors or [str(inspection.get("error") or "inspection failed")]),
                    payload=payload,
                ).to_dict()
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        skeletons = inspection_skeleton_paths(inspection)
        recorded = str(record.metadata.get("skeleton") or "").strip()
        skeleton = recorded if recorded in skeletons else skeletons[0]
        return GodotOperationResult.success(
            operation,
            payload={
                "avatar": avatar,
                "skeleton": skeleton,
                "resource_path": record.backend_path,
                "source": "live_godot_inspection",
                "skeletons": skeletons,
                "inspection": inspection,
                "process": process.to_dict(),
            },
        ).to_dict()

    def validate_compatibility(
        self,
        motion: str,
        skeleton: str,
    ) -> dict[str, Any]:
        operation = "animation.validate_compatibility"
        expected = str(skeleton or "").strip()
        if not expected:
            return GodotOperationResult.failure(
                operation, "skeleton is required"
            ).to_dict()
        try:
            record = self._assets._registry.find(motion)
        except (OSError, TypeError, ValueError) as exc:
            return self._assets._registry_failure(
                operation,
                exc,
                payload={
                    "motion": str(motion or ""),
                    "expected_skeleton": expected,
                },
            )
        if record is None or record.type != "motion":
            return GodotOperationResult.failure(
                operation,
                f"Unknown registered Motion: {motion}",
            ).to_dict()
        payload: dict[str, Any] = {
            "motion": motion,
            "expected_skeleton": expected,
            "resource_path": record.backend_path,
        }
        try:
            process, inspection = self._assets._inspect_native(record.backend_path)
            payload["process"] = process.to_dict()
            payload["inspection"] = inspection
            payload["actual_skeletons"] = inspection_skeleton_paths(inspection)
            payload["bone_tracks"] = inspection_bone_tracks(inspection)
            errors = validate_resource_inspection(
                inspection,
                "motion",
                expected_skeleton=expected,
            )
            if process.returncode != 0 and not errors:
                errors = [
                    str(
                        inspection.get("error")
                        or f"Godot inspection exited {process.returncode}"
                    )
                ]
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        if errors:
            return GodotOperationResult.failure(
                operation,
                *errors,
                payload=payload,
            ).to_dict()
        return GodotOperationResult.success(
            operation,
            payload=payload,
        ).to_dict()
