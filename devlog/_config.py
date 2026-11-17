"""Configuration store for devlog (persisted in <data-dir>/config.json).

Values here are low-level defaults that CLI commands can read without
requiring env vars. All keys are optional; env vars always take
precedence when both are set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from devlog._store import AtomicStore

if TYPE_CHECKING:
    pass

# Known config keys with their types and descriptions
CONFIG_SCHEMA: dict[str, dict] = {
    "auto_sync": {
        "type": "bool",
        "default": False,
        "description": "Auto-run `devlog git sync` after every write command",
    },
    "watch_interval": {
        "type": "int",
        "default": 2,
        "description": "Polling interval (seconds) for `devlog watch`",
    },
    "default_llm_model": {
        "type": "str",
        "default": "llama3.2",
        "description": "Default Ollama model for `devlog digest --llm`",
    },
    "default_embed_model": {
        "type": "str",
        "default": "nomic-embed-text",
        "description": "Default Ollama model for `devlog ask` embeddings",
    },
    "ollama_host": {
        "type": "str",
        "default": "http://127.0.0.1:11434",
        "description": "Ollama server URL (overrides DEVLOG_OLLAMA_HOST)",
    },
    "webhook_url": {
        "type": "str",
        "default": "",
        "description": "POST this URL on every `add`/`edit` (JSON body: entry data)",
    },
    "webhook_secret": {
        "type": "str",
        "default": "",
        "description": "HMAC secret for webhook signature verification",
    },
    "theme": {
        "type": "str",
        "default": "",
        "description": "Active theme name (mirrors `devlog theme use`)",
    },
}


def _get_store() -> AtomicStore:
    return AtomicStore("config.json", default={})


def get_config() -> dict[str, Any]:
    """Return the full config dict (with defaults applied)."""
    store = _get_store()
    data = store.load()
    # Apply defaults for missing keys
    result = {}
    for key, spec in CONFIG_SCHEMA.items():
        result[key] = data.get(key, spec["default"])
    return result


def get_config_value(key: str, default: Any = None) -> Any:
    """Get a single config value (env var takes precedence)."""
    env_key = f"DEVLOG_{key.upper()}"
    if env_key in os.environ:
        val = os.environ[env_key]
        # Try to coerce bool/int
        spec = CONFIG_SCHEMA.get(key)
        if spec and spec["type"] == "bool":
            return val.lower() in ("1", "true", "yes", "on")
        if spec and spec["type"] == "int":
            return int(val)
        return val
    cfg = get_config()
    return cfg.get(key, default)


def set_config_value(key: str, value: Any) -> None:
    """Set a single config value."""
    if key not in CONFIG_SCHEMA:
        raise ValueError(f"Unknown config key: {key}. Valid keys: {', '.join(CONFIG_SCHEMA)}")
    store = _get_store()
    data = store.load()
    data[key] = value
    store.save(data)


def unset_config_value(key: str) -> None:
    """Remove a config key (reverts to default/env)."""
    store = _get_store()
    data = store.load()
    if key in data:
        del data[key]
        store.save(data)


def config_as_json() -> str:
    """Return the full effective config as JSON."""
    import json

    return json.dumps(get_config(), indent=2, ensure_ascii=False)


@dataclass
class ConfigValue:
    key: str
    value: Any
    source: str  # "env", "config", "default"
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "description": self.description,
        }