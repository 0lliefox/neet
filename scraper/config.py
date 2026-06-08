from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

def load(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not installed; using built-in defaults. Run: pip install pyyaml")
        return {}

    if not config_path.exists():
        logger.warning("config.yaml not found at %s; using built-in defaults", config_path)
        return {}

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    logger.debug("Loaded config from %s", config_path)
    return data

def get(cfg: dict, *keys: str, default: Any = None) -> Any:
    node = cfg
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key, default)
        if node is default:
            return default
    return node
