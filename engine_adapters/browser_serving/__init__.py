"""Stable public entry point for engine-agnostic browser serving."""

from typing import Any

from .client import BrowserServingClient
from .cg_video import CgVideoError, CgVideoGateway, CgVideoGatewayProtocol
from .config import BrowserServingConfig
from .contracts import (
    API_VERSION,
    AssetImportRequest,
    AssetRecord,
    BrowserServingError,
    EngineBackend,
    EngineCapabilities,
    EngineCapabilityError,
    EngineDescriptor,
    StagedUpload,
    UnknownEngineError,
    WorldRecord,
)
from .service import BrowserServingService


def create_app(
    service: BrowserServingService | None = None,
    *,
    config: BrowserServingConfig | None = None,
    backends: list[Any] | None = None,
):
    """Create the optional FastAPI Gateway on first use."""

    from .gateway import create_app as _create_app

    return _create_app(service, config=config, backends=backends)


__all__ = [
    "API_VERSION",
    "AssetImportRequest",
    "AssetRecord",
    "BrowserServingClient",
    "CgVideoError",
    "CgVideoGateway",
    "CgVideoGatewayProtocol",
    "BrowserServingConfig",
    "BrowserServingError",
    "BrowserServingService",
    "EngineBackend",
    "EngineCapabilities",
    "EngineCapabilityError",
    "EngineDescriptor",
    "StagedUpload",
    "UnknownEngineError",
    "WorldRecord",
    "create_app",
]
