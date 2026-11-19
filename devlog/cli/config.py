"""The ``devlog config`` command group."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from devlog import ui
from devlog._config import (
    CONFIG_SCHEMA,
    get_config,
    get_config_value,
    set_config_value,
    unset_config_value,
)
from devlog.storage import StorageError

console = ui.console
err_console = ui.err_console


def register(main) -> None:
    @main.group()
    def config() -> None:
        """View and manage devlog configuration."""

    @config.command("show")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def config_show(as_json: bool) -> None:
        """Show the effective configuration (env + config file + defaults)."""
        cfg = get_config()
        if as_json:
            print(json.dumps(cfg, indent=2, ensure_ascii=False))
            return

        if not cfg:
            ui.print_info("No configuration set (all defaults).")
            return

        from rich.table import Table

        table = Table(title="devlog Configuration", show_header=True)
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_column("Source", style="dim")
        table.add_column("Description", style="dim")

        for key, spec in CONFIG_SCHEMA.items():
            import os

            env_key = f"DEVLOG_{key.upper()}"
            if env_key in os.environ:
                source = "env"
            elif key in cfg and cfg[key] != spec["default"]:
                source = "config"
            else:
                source = "default"

            val = cfg.get(key, spec["default"])
            if isinstance(val, str) and len(val) > 60:
                val = val[:57] + "..."
            table.add_row(key, str(val), source, spec["description"])

        console.print(table)

    @config.command("get")
    @click.argument("key")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def config_get(key: str, as_json: bool) -> None:
        """Get a single configuration value."""
        if key not in CONFIG_SCHEMA:
            ui.print_error(f"Unknown config key: {key}")
            ui.print_info(f"Valid keys: {', '.join(CONFIG_SCHEMA)}")
            sys.exit(1)

        val = get_config_value(key)
        if as_json:
            print(json.dumps({"key": key, "value": val}, ensure_ascii=False))
        else:
            print(val)

    @config.command("set")
    @click.argument("key")
    @click.argument("value")
    def config_set(key: str, value: str) -> None:
        """Set a configuration value."""
        if key not in CONFIG_SCHEMA:
            ui.print_error(f"Unknown config key: {key}")
            ui.print_info(f"Valid keys: {', '.join(CONFIG_SCHEMA)}")
            sys.exit(1)

        # Coerce type
        spec = CONFIG_SCHEMA[key]
        if spec["type"] == "bool":
            if value.lower() in ("1", "true", "yes", "on"):
                coerced: Any = True
            elif value.lower() in ("0", "false", "no", "off", ""):
                coerced = False
            else:
                ui.print_error(f"Invalid boolean: {value}")
                sys.exit(1)
        elif spec["type"] == "int":
            try:
                coerced = int(value)
            except ValueError:
                ui.print_error(f"Invalid integer: {value}")
                sys.exit(1)
        else:
            coerced = value

        try:
            set_config_value(key, coerced)
        except StorageError as exc:
            from devlog.cli._shared import _handle_storage_error

            _handle_storage_error(exc)
        console.print(ui.success_line(f"Set {key} = {coerced!r}"))

    @config.command("unset")
    @click.argument("key")
    def config_unset(key: str) -> None:
        """Remove a configuration value (revert to default/env)."""
        if key not in CONFIG_SCHEMA:
            ui.print_error(f"Unknown config key: {key}")
            sys.exit(1)

        try:
            unset_config_value(key)
        except StorageError as exc:
            from devlog.cli._shared import _handle_storage_error

            _handle_storage_error(exc)
        console.print(ui.success_line(f"Unset {key}"))

    @config.command("path")
    def config_path() -> None:
        """Print the path to the config file."""
        from devlog._store import AtomicStore

        store = AtomicStore("config.json")
        print(store._path)

    @config.command("edit")
    def config_edit() -> None:
        """Open the config file in $EDITOR and save the edited content."""
        from devlog._store import AtomicStore
        from devlog.cli._shared import _edit_in_editor

        store = AtomicStore("config.json")
        store._path.parent.mkdir(parents=True, exist_ok=True)
        store._path.touch(exist_ok=True)

        edited = _edit_in_editor(store._path.read_text(encoding="utf-8"))
        if edited is None:
            ui.print_error("Editor exited abnormally; no changes saved.")
            sys.exit(1)

        # An empty buffer resets to defaults (AtomicStore treats a
        # blank/missing file the same way); anything else must parse to
        # a JSON object before it is allowed to touch the file — a
        # typo'd save would otherwise be silently ignored on every
        # later read, which reads as "config randomly reset".
        if not edited.strip():
            new_data: dict[str, Any] = {}
        else:
            try:
                new_data = json.loads(edited)
            except json.JSONDecodeError as exc:
                ui.print_error(f"Config file is not valid JSON: {exc}")
                ui.print_info("No changes were saved.")
                sys.exit(1)
            if not isinstance(new_data, dict):
                ui.print_error(
                    "Config file must contain a JSON object (keys mapping to values)."
                )
                ui.print_info("No changes were saved.")
                sys.exit(1)

        store.save(new_data)
        console.print(ui.success_line(f"Saved config: {store._path}"))