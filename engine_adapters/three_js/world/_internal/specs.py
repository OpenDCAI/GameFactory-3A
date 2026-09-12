"""World specification model for the three.js adapter.

Coordinates follow the three.js convention: right-handed, Y-up, metres,
radians for Euler rotations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import isfinite
from typing import Any


SUPPORTED_LIGHT_TYPES = (
    "AmbientLight",
    "HemisphereLight",
    "DirectionalLight",
    "PointLight",
    "SpotLight",
    "RectAreaLight",
)
SUPPORTED_FOG_TYPES = ("none", "Fog", "FogExp2")
#: Must match ``A3GameEnvironmentPreset`` in ``engine/runtime-host.js``.
#: Validated here so a typo fails when the world is published, not as a
#: silently unlit scene in the browser.
SUPPORTED_ENVIRONMENT_PRESETS = ("room", "sky", "gradient", "none")
SUPPORTED_CAMERA_TYPES = (
    "PerspectiveCamera",
    "OrthographicCamera",
)
SUPPORTED_CONTROL_TYPES = (
    "none",
    "OrbitControls",
    "PointerLockControls",
    "MapControls",
    "FlyControls",
)
SUPPORTED_ENTITY_ROLES = (
    "environment",
    "player_start",
    "prop",
    "npc",
    "pickup",
    "trigger",
    "vehicle",
    "weapon",
    "effect",
)
SUPPORTED_BEHAVIOR_TYPES = (
    "animation",
    "spin",
    "orbit",
    "float",
    "path",
    "audio",
)

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _identifier(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(text):
        raise ValueError(
            f"{field_name} must match [A-Za-z0-9][A-Za-z0-9_.-]*: "
            f"{value!r}"
        )
    return text


def _vector3(
    data: Any,
    default: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, float]:
    if isinstance(data, dict):
        return {
            "x": float(data.get("x", default[0])),
            "y": float(data.get("y", default[1])),
            "z": float(data.get("z", default[2])),
        }
    if isinstance(data, (list, tuple)) and len(data) >= 3:
        return {
            "x": float(data[0]),
            "y": float(data[1]),
            "z": float(data[2]),
        }
    return {"x": default[0], "y": default[1], "z": default[2]}


@dataclass(frozen=True)
class TransformSpec:
    position: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0}
    )
    rotation: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0}
    )
    scale: dict[str, float] = field(
        default_factory=lambda: {"x": 1.0, "y": 1.0, "z": 1.0}
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TransformSpec":
        payload = data or {}
        return cls(
            position=_vector3(payload.get("position")),
            rotation=_vector3(payload.get("rotation")),
            scale=_vector3(payload.get("scale"), (1.0, 1.0, 1.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": dict(self.position),
            "rotation": dict(self.rotation),
            "scale": dict(self.scale),
        }


@dataclass(frozen=True)
class WorldBehaviorSpec:
    type: str
    artifact_id: str = ""
    clip: str = ""
    loop: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldBehaviorSpec":
        behavior_type = str(data.get("type") or "").strip()
        if behavior_type not in SUPPORTED_BEHAVIOR_TYPES:
            supported = ", ".join(SUPPORTED_BEHAVIOR_TYPES)
            raise ValueError(
                f"Unsupported behavior type {behavior_type!r}; "
                f"supported: {supported}"
            )
        return cls(
            type=behavior_type,
            artifact_id=str(data.get("artifact_id") or ""),
            clip=str(data.get("clip") or ""),
            loop=bool(data.get("loop", True)),
            options={
                key: value
                for key, value in data.items()
                if key
                not in {"type", "artifact_id", "clip", "loop"}
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "artifact_id": self.artifact_id,
            "clip": self.clip,
            "loop": self.loop,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class WorldEntitySpec:
    entity_id: str
    role: str
    artifact_id: str = ""
    category: str = ""
    collision: bool | None = None
    cast_shadow: bool = True
    receive_shadow: bool = True
    transform: TransformSpec = field(default_factory=TransformSpec)
    behaviors: tuple[WorldBehaviorSpec, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        index: int = 0,
    ) -> "WorldEntitySpec":
        role = str(data.get("role") or "prop").strip()
        if role not in SUPPORTED_ENTITY_ROLES:
            supported = ", ".join(SUPPORTED_ENTITY_ROLES)
            raise ValueError(
                f"Unsupported entity role {role!r}; supported: "
                f"{supported}"
            )
        entity_id = str(
            data.get("entity_id") or f"{role}_{index:03d}"
        )
        collision = data.get("collision")
        return cls(
            entity_id=_identifier(entity_id, field_name="entity_id"),
            role=role,
            artifact_id=str(data.get("artifact_id") or ""),
            category=str(data.get("category") or ""),
            collision=(
                None if collision is None else bool(collision)
            ),
            cast_shadow=bool(data.get("cast_shadow", True)),
            receive_shadow=bool(data.get("receive_shadow", True)),
            transform=TransformSpec.from_dict(
                data.get("transform")
            ),
            behaviors=tuple(
                WorldBehaviorSpec.from_dict(dict(item))
                for item in data.get("behaviors") or []
                if isinstance(item, dict)
            ),
            parameters=dict(data.get("parameters") or {}),
        )

    @property
    def collision_enabled(self) -> bool:
        if self.collision is not None:
            return self.collision
        return self.role in {"environment", "prop", "vehicle"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "role": self.role,
            "artifact_id": self.artifact_id,
            "category": self.category,
            "collision": self.collision_enabled,
            "cast_shadow": self.cast_shadow,
            "receive_shadow": self.receive_shadow,
            "transform": self.transform.to_dict(),
            "behaviors": [
                behavior.to_dict() for behavior in self.behaviors
            ],
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class LightSpec:
    light_id: str
    type: str
    color: str = "#ffffff"
    intensity: float = 1.0
    position: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 5.0, "z": 0.0}
    )
    target: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0}
    )
    cast_shadow: bool = False
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        index: int = 0,
    ) -> "LightSpec":
        light_type = str(data.get("type") or "").strip()
        if light_type not in SUPPORTED_LIGHT_TYPES:
            supported = ", ".join(SUPPORTED_LIGHT_TYPES)
            raise ValueError(
                f"Unsupported light type {light_type!r}; supported: "
                f"{supported}"
            )
        return cls(
            light_id=_identifier(
                str(data.get("light_id") or f"light_{index:03d}"),
                field_name="light_id",
            ),
            type=light_type,
            color=str(data.get("color") or "#ffffff"),
            intensity=float(data.get("intensity", 1.0)),
            position=_vector3(
                data.get("position"),
                (0.0, 5.0, 0.0),
            ),
            target=_vector3(data.get("target")),
            cast_shadow=bool(data.get("cast_shadow", False)),
            options={
                key: value
                for key, value in data.items()
                if key
                not in {
                    "light_id",
                    "type",
                    "color",
                    "intensity",
                    "position",
                    "target",
                    "cast_shadow",
                }
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "light_id": self.light_id,
            "type": self.type,
            "color": self.color,
            "intensity": self.intensity,
            "position": dict(self.position),
            "target": dict(self.target),
            "cast_shadow": self.cast_shadow,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class CameraSpec:
    type: str = "PerspectiveCamera"
    fov: float = 50.0
    near: float = 0.1
    far: float = 2000.0
    position: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 2.0, "z": 8.0}
    )
    target: dict[str, float] = field(
        default_factory=lambda: {"x": 0.0, "y": 1.0, "z": 0.0}
    )
    controls: str = "OrbitControls"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CameraSpec":
        payload = data or {}
        camera_type = str(
            payload.get("type") or "PerspectiveCamera"
        ).strip()
        if camera_type not in SUPPORTED_CAMERA_TYPES:
            supported = ", ".join(SUPPORTED_CAMERA_TYPES)
            raise ValueError(
                f"Unsupported camera type {camera_type!r}; "
                f"supported: {supported}"
            )
        controls = str(
            payload.get("controls") or "OrbitControls"
        ).strip()
        if controls not in SUPPORTED_CONTROL_TYPES:
            supported = ", ".join(SUPPORTED_CONTROL_TYPES)
            raise ValueError(
                f"Unsupported controls {controls!r}; supported: "
                f"{supported}"
            )
        return cls(
            type=camera_type,
            fov=float(payload.get("fov", 50.0)),
            near=float(payload.get("near", 0.1)),
            far=float(payload.get("far", 2000.0)),
            position=_vector3(
                payload.get("position"),
                (0.0, 2.0, 8.0),
            ),
            target=_vector3(
                payload.get("target"),
                (0.0, 1.0, 0.0),
            ),
            controls=controls,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "fov": self.fov,
            "near": self.near,
            "far": self.far,
            "position": dict(self.position),
            "target": dict(self.target),
            "controls": self.controls,
        }


def _water_surface(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("water entries must be objects")
    result = dict(data)
    result["water_id"] = _identifier(data.get("water_id", ""), field_name="water_id")
    size = data.get("size", 60)
    size = list(size) if isinstance(size, (list, tuple)) else [size, size]
    if len(size) != 2 or any(not isfinite(float(v)) or float(v) <= 0 for v in size):
        raise ValueError("water size must contain two finite positive lengths")
    result["size"] = [float(v) for v in size]
    result["position"] = _vector3(data.get("position"))
    if not all(isfinite(v) for v in result["position"].values()):
        raise ValueError("water position must be finite")
    options = dict(data.get("options") or {})
    if options.get("quality", "standard") not in ("low", "standard"):
        raise ValueError("water quality must be low or standard")
    result["options"] = options
    return result


def _wind_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("wind must be an object")
    result = dict(data)
    if "velocity" in result:
        velocity = result["velocity"]
        if not isinstance(velocity, (list, tuple, dict)):
            raise ValueError("wind velocity must be a vector")
        if not isinstance(velocity, dict) and len(velocity) != 3:
            raise ValueError("wind velocity must have three components")
        result["velocity"] = _vector3(velocity)
        if not all(isfinite(v) for v in result["velocity"].values()):
            raise ValueError("wind velocity must be finite")
    for key, minimum in (("gustStrength", 0), ("gustPeriod", 0.1),
                         ("spatialScale", 0.1), ("response", 0.01), ("heightShear", 0)):
        if key in result:
            value = float(result[key])
            if not isfinite(value) or value < minimum:
                raise ValueError(f"Invalid wind {key}")
            result[key] = value
    if "seed" in result and not isfinite(float(result["seed"])):
        raise ValueError("wind seed must be finite")
    return result


def _water_surfaces(data: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(data, (list, tuple)):
        raise ValueError("water must be a list")
    surfaces = tuple(_water_surface(item) for item in data)
    ids = [item["water_id"] for item in surfaces]
    if len(ids) != len(set(ids)):
        raise ValueError("water_id values must be unique")
    return surfaces


@dataclass(frozen=True)
class EnvironmentSpec:
    #: Procedural image-based lighting: "room", "sky", "gradient", "none".
    #:
    #: A world that sets neither this nor ``environment_artifact_id`` has
    #: no environment map, and every PBR material in it reflects nothing —
    #: which is the difference between a scene that looks lit and one that
    #: looks like flat plastic. "gradient" is the outdoor default because
    #: it is the only preset with clouds.
    preset: str = ""
    #: Direction *towards* the sun, ``{"x","y","z"}``. Also drives the
    #: visible sun in the "sky" and "gradient" presets, so a
    #: DirectionalLight placed from it agrees with the painted sky.
    sun: dict[str, Any] = field(default_factory=dict)
    #: Palette and cloud settings for the "gradient" preset — see
    #: ``createSkyGradient`` in ``engine/visual-kit.js``.
    sky: dict[str, Any] = field(default_factory=dict)
    background: str = "#101014"
    environment_artifact_id: str = ""
    #: Equirectangular image used as the visible backdrop, when it should
    #: differ from the one used for lighting.
    background_artifact_id: str = ""
    environment_intensity: float = 1.0
    background_intensity: float = 1.0
    background_blurriness: float = 0.0
    show_sky: bool = True
    environment_rotation_degrees: float = 0.0
    background_rotation_degrees: float = 0.0
    water: tuple[dict[str, Any], ...] = ()
    wind: dict[str, Any] = field(default_factory=dict)
    tone_mapping: str = "NeutralToneMapping"
    tone_mapping_exposure: float = 1.0
    shadows: bool = True
    fog_type: str = "none"
    fog_color: str = "#101014"
    fog_near: float = 1.0
    fog_far: float = 200.0
    fog_density: float = 0.001
    ground: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> "EnvironmentSpec":
        payload = data or {}
        fog = payload.get("fog") or {}
        fog_type = str(fog.get("type") or "none").strip()
        if fog_type not in SUPPORTED_FOG_TYPES:
            supported = ", ".join(SUPPORTED_FOG_TYPES)
            raise ValueError(
                f"Unsupported fog type {fog_type!r}; supported: "
                f"{supported}"
            )
        preset = str(payload.get("preset") or "").strip().lower()
        if preset and preset not in SUPPORTED_ENVIRONMENT_PRESETS:
            supported = ", ".join(SUPPORTED_ENVIRONMENT_PRESETS)
            raise ValueError(
                f"Unsupported environment preset {preset!r}; supported: "
                f"{supported}"
            )
        return cls(
            preset=preset,
            sun=dict(payload.get("sun") or {}),
            sky=dict(payload.get("sky") or {}),
            background=str(payload.get("background") or "#101014"),
            environment_artifact_id=str(
                payload.get("environment_artifact_id") or ""
            ),
            background_artifact_id=str(
                payload.get("background_artifact_id") or ""
            ),
            environment_intensity=float(
                payload.get("environment_intensity", 1.0)
            ),
            background_intensity=float(
                payload.get("background_intensity", 1.0)
            ),
            background_blurriness=float(
                payload.get("background_blurriness", 0.0)
            ),
            show_sky=bool(payload.get("show_sky", True)),
            environment_rotation_degrees=float(payload.get("environment_rotation_degrees", 0.0)),
            background_rotation_degrees=float(payload.get("background_rotation_degrees", payload.get("environment_rotation_degrees", 0.0))),
            water=_water_surfaces(payload.get("water", [])),
            wind=_wind_config(payload.get("wind") or {}),
            tone_mapping=str(
                payload.get("tone_mapping")
                or "NeutralToneMapping"
            ),
            tone_mapping_exposure=float(
                payload.get("tone_mapping_exposure", 1.0)
            ),
            shadows=bool(payload.get("shadows", True)),
            fog_type=fog_type,
            fog_color=str(fog.get("color") or "#101014"),
            fog_near=float(fog.get("near", 1.0)),
            fog_far=float(fog.get("far", 200.0)),
            fog_density=float(fog.get("density", 0.001)),
            ground=dict(payload.get("ground") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "sun": dict(self.sun),
            "sky": dict(self.sky),
            "background": self.background,
            "environment_artifact_id": self.environment_artifact_id,
            "background_artifact_id": self.background_artifact_id,
            "environment_intensity": self.environment_intensity,
            "background_intensity": self.background_intensity,
            "background_blurriness": self.background_blurriness,
            "show_sky": self.show_sky,
            "environment_rotation_degrees": self.environment_rotation_degrees,
            "background_rotation_degrees": self.background_rotation_degrees,
            "water": [dict(item) for item in self.water],
            "wind": dict(self.wind),
            "tone_mapping": self.tone_mapping,
            "tone_mapping_exposure": self.tone_mapping_exposure,
            "shadows": self.shadows,
            "fog": {
                "type": self.fog_type,
                "color": self.fog_color,
                "near": self.fog_near,
                "far": self.fog_far,
                "density": self.fog_density,
            },
            "ground": dict(self.ground),
        }


@dataclass(frozen=True)
class WorldSpec:
    world_id: str
    name: str = ""
    project_id: str = ""
    environment: EnvironmentSpec = field(
        default_factory=EnvironmentSpec
    )
    camera: CameraSpec = field(default_factory=CameraSpec)
    lights: tuple[LightSpec, ...] = ()
    entities: tuple[WorldEntitySpec, ...] = ()
    spawn_points: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorldSpec":
        world_id = _identifier(
            str(data.get("world_id") or "world_001"),
            field_name="world_id",
        )
        return cls(
            world_id=world_id,
            name=str(data.get("name") or world_id),
            project_id=str(data.get("project_id") or ""),
            environment=EnvironmentSpec.from_dict(
                data.get("environment")
            ),
            camera=CameraSpec.from_dict(data.get("camera")),
            lights=tuple(
                LightSpec.from_dict(dict(item), index)
                for index, item in enumerate(
                    data.get("lights") or []
                )
                if isinstance(item, dict)
            ),
            entities=tuple(
                WorldEntitySpec.from_dict(dict(item), index)
                for index, item in enumerate(
                    data.get("entities") or []
                )
                if isinstance(item, dict)
            ),
            spawn_points=tuple(
                {
                    "name": str(item.get("name") or f"spawn_{index}"),
                    "position": _vector3(item.get("position")),
                    "rotation": _vector3(item.get("rotation")),
                }
                for index, item in enumerate(
                    data.get("spawn_points") or []
                )
                if isinstance(item, dict)
            ),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_id": self.world_id,
            "name": self.name,
            "project_id": self.project_id,
            "environment": self.environment.to_dict(),
            "camera": self.camera.to_dict(),
            "lights": [light.to_dict() for light in self.lights],
            "entities": [
                entity.to_dict() for entity in self.entities
            ],
            "spawn_points": [dict(item) for item in self.spawn_points],
            "metadata": dict(self.metadata),
        }

    def artifact_ids(self) -> list[str]:
        resolved = {
            entity.artifact_id
            for entity in self.entities
            if entity.artifact_id
        }
        for entity in self.entities:
            for behavior in entity.behaviors:
                if behavior.artifact_id:
                    resolved.add(behavior.artifact_id)
        if self.environment.environment_artifact_id:
            resolved.add(self.environment.environment_artifact_id)
        if self.environment.background_artifact_id:
            resolved.add(self.environment.background_artifact_id)
        for key in ("artifact_id", "texture_artifact_id",
                    "normal_artifact_id", "roughness_artifact_id"):
            reference = str(self.environment.ground.get(key) or "")
            if reference:
                resolved.add(reference)
        for water in self.environment.water:
            reference = str(water.get("normal_artifact_id") or "")
            if reference:
                resolved.add(reference)
        return sorted(resolved)
