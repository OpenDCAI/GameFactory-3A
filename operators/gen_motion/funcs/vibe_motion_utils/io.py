"""Small IO helpers shared by rigging and motion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

__all__ = ['jsonable', 'save_json', 'sha256']


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        x = float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, Path):
        return str(x)
    return x


def save_json(path, data, indent=1):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=indent), encoding='utf-8')
    return path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
