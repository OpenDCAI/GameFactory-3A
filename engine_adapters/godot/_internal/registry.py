"""Atomic JSON artifact registry for the Godot adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .managed_state import atomic_write_text, read_managed_text

ARTIFACT_REGISTRY_SCHEMA_VERSION = "gamefactory3a.godot.artifacts.v1"


def _reject_json_constant(value: str) -> None:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""

    raise ValueError(f"non-standard JSON constant {value!r}")


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    asset_id: str
    type: str
    backend_path: str
    source_path: str
    backend_class: str = "Resource"
    state: str = "ready"
    spawnable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRecord:
        return cls(
            artifact_id=str(data.get("artifact_id") or data.get("id") or ""),
            asset_id=str(data.get("asset_id") or ""),
            type=str(data.get("type") or ""),
            backend_path=str(data.get("backend_path") or data.get("path") or ""),
            source_path=str(data.get("source_path") or ""),
            backend_class=str(data.get("backend_class") or "Resource"),
            state=str(data.get("state") or "ready"),
            spawnable=bool(data.get("spawnable", False)),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        playable = self.type == "motion"
        renderable = self.type not in {"motion", "audio"}
        capabilities = {
            "renderable": renderable,
            "spawnable": self.spawnable,
            "collidable": self.type in {"environment", "scene"},
            "playable": playable,
        }
        return {
            "artifact_id": self.artifact_id,
            "asset_id": self.asset_id,
            "package_id": self.asset_id,
            "type": self.type,
            "representation": self.backend_class.lower(),
            "primary_asset": {
                "backend": "godot",
                "class": self.backend_class,
                "path": self.backend_path,
            },
            "runtime_capabilities": capabilities,
            "backend": "godot",
            "backend_class": self.backend_class,
            "backend_path": self.backend_path,
            "source_path": self.source_path,
            "spawnable": self.spawnable,
            "state": self.state,
            "editor_backend": {
                "backend": "godot",
                "path": self.backend_path,
            },
            "runtime": {
                "path": self.backend_path,
                "class": self.backend_class,
                "spawnable": self.spawnable,
            },
            "metadata": dict(self.metadata),
        }


class ArtifactRegistry:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _record_from_entry(
        item: dict[str, Any], index: int, path: Path
    ) -> ArtifactRecord:
        string_fields = (
            "artifact_id",
            "asset_id",
            "type",
            "backend_path",
            "source_path",
            "backend_class",
            "state",
        )
        required_nonempty = {
            "artifact_id",
            "asset_id",
            "type",
            "backend_path",
            "backend_class",
            "state",
        }
        for name in string_fields:
            if name not in item:
                raise ValueError(
                    f"Godot artifact registry entry {index} is missing {name}: {path}"
                )
            value = item[name]
            if not isinstance(value, str):
                raise TypeError(
                    f"Godot artifact registry entry {index} field {name} must be "
                    f"a string: {path}"
                )
            if name in required_nonempty and not value.strip():
                raise ValueError(
                    f"Godot artifact registry entry {index} field {name} must not "
                    f"be empty: {path}"
                )
        if not isinstance(item.get("spawnable"), bool):
            raise TypeError(
                f"Godot artifact registry entry {index} field spawnable must be "
                f"a boolean: {path}"
            )
        if not isinstance(item.get("metadata"), dict):
            raise TypeError(
                f"Godot artifact registry entry {index} field metadata must be "
                f"an object: {path}"
            )
        return ArtifactRecord.from_dict(item)

    @staticmethod
    def _validate_references(records: list[ArtifactRecord], path: Path) -> None:
        owners: dict[str, tuple[int, str]] = {}
        for index, record in enumerate(records):
            for field_name in ("artifact_id", "asset_id", "backend_path"):
                reference = getattr(record, field_name)
                previous = owners.get(reference)
                if previous is None:
                    owners[reference] = (index, field_name)
                    continue
                previous_index, previous_field = previous
                if previous_index == index:
                    continue
                if field_name == previous_field == "artifact_id":
                    raise ValueError(
                        f"Godot artifact registry contains duplicate artifact_id "
                        f"{reference!r} at entries {previous_index} and {index}: {path}"
                    )
                raise ValueError(
                    "Godot artifact registry contains ambiguous lookup reference "
                    f"{reference!r} at entries {previous_index} ({previous_field}) "
                    f"and {index} ({field_name}): {path}"
                )

    def _validate_records(self, records: list[ArtifactRecord]) -> None:
        validated = [
            self._record_from_entry(record.to_dict(), index, self.path)
            for index, record in enumerate(records)
        ]
        try:
            json.dumps(
                [record.to_dict() for record in validated],
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Godot artifact registry records must contain only strict JSON "
                f"values: {exc}"
            ) from exc
        self._validate_references(validated, self.path)

    def _read(self) -> list[ArtifactRecord]:
        try:
            registry_text = read_managed_text(
                self.path,
                label="Godot artifact registry",
            )
        except FileNotFoundError:
            return []
        payload = json.loads(
            registry_text,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise TypeError(
                f"Godot artifact registry must be a JSON object: {self.path}"
            )
        schema_version = payload.get("schema_version")
        if schema_version != ARTIFACT_REGISTRY_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported Godot artifact registry schema at "
                f"{self.path}: {schema_version!r}"
            )
        items = payload.get("artifacts")
        if not isinstance(items, list):
            raise TypeError(
                f"Godot artifact registry 'artifacts' must be a list: {self.path}"
            )

        records = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError(
                    "Godot artifact registry entries must be JSON objects; "
                    f"entry {index} is {type(item).__name__}: {self.path}"
                )
            records.append(self._record_from_entry(item, index, self.path))
        self._validate_records(records)
        return records

    def _write(self, records: list[ArtifactRecord]) -> None:
        self._validate_records(records)
        payload = {
            "schema_version": ARTIFACT_REGISTRY_SCHEMA_VERSION,
            "artifacts": [record.to_dict() for record in records],
        }
        atomic_write_text(
            self.path,
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            label="Godot artifact registry",
        )

    def upsert(self, record: ArtifactRecord) -> ArtifactRecord:
        return self.upsert_many([record])[0]

    def upsert_many(self, new_records: list[ArtifactRecord]) -> list[ArtifactRecord]:
        """Atomically insert or replace a related group of artifact records."""

        self._validate_records(new_records)
        records = {item.artifact_id: item for item in self._read()}
        for record in new_records:
            for artifact_id, current in list(records.items()):
                if (
                    current.backend_path == record.backend_path
                    and artifact_id != record.artifact_id
                ):
                    records.pop(artifact_id, None)
            records[record.artifact_id] = record
        self._write(
            sorted(
                records.values(),
                key=lambda item: (item.type, item.asset_id, item.artifact_id),
            )
        )
        return list(new_records)

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        key = str(artifact_id or "").strip()
        for record in self._read():
            if record.artifact_id == key:
                return record
        return None

    def find(self, reference: str) -> ArtifactRecord | None:
        key = str(reference or "").strip()
        for record in self._read():
            if key in {record.artifact_id, record.asset_id, record.backend_path}:
                return record
        return None

    def list(self, asset_type: str = "") -> list[ArtifactRecord]:
        normalized = str(asset_type or "").strip().lower()
        records = self._read()
        return (
            [item for item in records if item.type == normalized]
            if normalized
            else records
        )
