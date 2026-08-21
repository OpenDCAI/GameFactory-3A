"""Private implementation helpers for the Godot adapter."""

from .inspection import (
    godot_4_version_error,
    godot_import_error_lines,
    inspect_godot_resource,
    inspection_bone_tracks,
    inspection_skeleton_paths,
    parse_godot_version,
    validate_resource_inspection,
)
from .managed_state import (
    atomic_write_bytes,
    atomic_write_text,
    prepare_managed_file,
    read_managed_bytes,
    read_managed_text,
    validate_managed_directory,
    validate_managed_file,
)
from .registry import ArtifactRecord, ArtifactRegistry
from .source import (
    GeneratedAssetSourceResolver,
    ResolvedAssetSource,
    source_descriptor,
    validate_regular_directory_tree,
)
from .transport import (
    GodotProcessResult,
    GodotTransport,
    find_godot_binary,
    managed_process_kwargs,
    terminate_process_tree,
)

__all__ = [
    "ArtifactRecord",
    "ArtifactRegistry",
    "GeneratedAssetSourceResolver",
    "GodotProcessResult",
    "GodotTransport",
    "ResolvedAssetSource",
    "atomic_write_bytes",
    "atomic_write_text",
    "find_godot_binary",
    "godot_4_version_error",
    "godot_import_error_lines",
    "inspect_godot_resource",
    "inspection_bone_tracks",
    "inspection_skeleton_paths",
    "managed_process_kwargs",
    "parse_godot_version",
    "prepare_managed_file",
    "read_managed_bytes",
    "read_managed_text",
    "source_descriptor",
    "terminate_process_tree",
    "validate_managed_directory",
    "validate_managed_file",
    "validate_regular_directory_tree",
    "validate_resource_inspection",
]
