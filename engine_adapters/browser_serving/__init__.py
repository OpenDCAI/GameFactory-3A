"""Stable public entry point for engine-agnostic browser serving."""

from .client import BrowserServingClient
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
from .gateway import create_app
from .service import BrowserServingService

__all__ = [
    "API_VERSION",
    "AssetImportRequest",
    "AssetRecord",
    "BrowserServingClient",
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
