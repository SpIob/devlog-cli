"""The ``devlog theme`` command group (11 subcommands).

Split out of :mod:`devlog.cli` to keep that module focused on the
journal commands: this block was ~470 lines, a fifth of cli.py.
Registration order is preserved -- ``cli`` calls :func:`register`
right after defining the ``main`` group, so ``devlog theme ...``
behaves exactly as before, including help order.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[import, no-redef]

from devlog import theme_preview, themes, ui

console = ui.console


def register(main) -> None:
    """Attach the ``theme`` group (and its subcommands) to *main*."""

    # ---------------------------------------------------------------------------
    # theme
    # ---------------------------------------------------------------------------


    @main.group()
    def theme() -> None:
        """View or change the active color theme."""


    @theme.command("list")
    @click.option("--flat", is_flag=True, default=False, help="Disable section grouping.")
    @click.option("--no-preview", is_flag=True, default=False, help="Hide the preview swatch column.")
    def theme_list(flat: bool, no_preview: bool) -> None:
        """Print every theme role and its current style."""
        console.print(
            ui.theme_table(grouped=not flat, show_preview=not no_preview)
        )


    @theme.command("show")
    @click.argument("role", required=False)
    def theme_show(role: str | None) -> None:
        """Print the active theme, or the value of a single ROLE.

        When ROLE is omitted, the full palette is dumped as a starter
        ``theme.toml`` to STDOUT (all lines commented out so it is a safe
        template to copy and edit).
        """
        palette = themes.get_active_theme()
        if role:
            if role not in themes.ROLES:
                ui.print_error(
                    f'Unknown role "{role}". Run `devlog theme list` to see valid roles.'
                )
                sys.exit(1)
            print(palette[role])
            return
        themes.write_default_theme(sys.stdout)


    def _read_theme_source(path: Path) -> dict[str, str]:
        """Read a theme file and convert parse errors into a clean error path.

        Returns the raw ``[palette]`` mapping. Raises :class:`SystemExit`
        with a CLI-friendly error on failure.
        """
        try:
            return themes._parse_file(path)
        except tomllib.TOMLDecodeError as exc:
            ui.print_error(f"Theme file is invalid TOML: {exc}")
            sys.exit(1)
        except (OSError, TypeError) as exc:
            ui.print_error(f"Cannot read theme file: {exc}")
            sys.exit(1)


    @theme.command("set")
    @click.argument(
        "source",
        type=click.Path(exists=True, dir_okay=False, readable=True),
    )
    @click.option(
        "--check",
        "check_only",
        is_flag=True,
        default=False,
        help="Validate SOURCE without installing it. Exits 1 on any error.",
    )
    def theme_set(source: str, check_only: bool) -> None:
        """Install a theme file as the active theme.

        SOURCE is a path to a ``theme.toml`` file. Its contents are
        validated; unknown roles are ignored with a warning, invalid style
        values cause the install to be rejected. On success the file is
        copied to the active theme path and the change takes effect for
        the next devlog invocation. With ``--check`` the file is only
        validated; nothing is written.
        """
        src = Path(source)
        raw = _read_theme_source(src)

        unknown, invalid = themes.validate_source(raw)
        for key in unknown:
            ui.print_warning(f"theme role '{key}' is unknown and will be ignored.")
        for key in invalid:
            ui.print_warning(
                f"theme role '{key}' has invalid style {raw[key]!r}; "
                "fix before installing."
            )

        if invalid:
            ui.print_error(
                f"Refusing to install: {len(invalid)} role(s) have invalid style values."
            )
            sys.exit(1)

        if check_only:
            print(f"Theme file is valid: {src}")
            return

        try:
            active = themes.install_theme_file(src, themes.get_theme_path())
        except themes.ThemeInstallError as exc:
            ui.print_error(str(exc))
            sys.exit(1)

        print(f"Theme installed at {themes.get_theme_path()} ({len(active)} roles).")


    @theme.command("use")
    @click.argument("name")
    def theme_use(name: str) -> None:
        """Install a bundled theme NAME as the active theme.

        NAME must be one of the names listed by ``devlog theme builtins``.
        Use ``devlog theme use default`` to reset the active theme to the
        built-in defaults without removing the file.
        """
        try:
            src = themes.get_builtin_theme_path(name)
        except themes.ThemeNotFoundError:
            names = ", ".join(themes.list_builtin_themes())
            ui.print_error(
                f'Unknown builtin theme "{name}". Available: {names}'
            )
            sys.exit(1)

        raw = _read_theme_source(src)
        unknown, invalid = themes.validate_source(raw)
        for key in unknown:
            ui.print_warning(f"theme role '{key}' is unknown and will be ignored.")
        for key in invalid:
            ui.print_warning(
                f"theme role '{key}' has invalid style {raw[key]!r}; "
                "this is a bug in the bundled theme, please report it."
            )

        if invalid:
            ui.print_error(
                f"Refusing to install builtin {name!r}: {len(invalid)} invalid role(s)."
            )
            sys.exit(1)

        try:
            active = themes.install_theme_file(src, themes.get_theme_path())
        except themes.ThemeInstallError as exc:
            ui.print_error(str(exc))
            sys.exit(1)

        print(f"Theme '{name}' installed at {themes.get_theme_path()} ({len(active)} roles).")


    @theme.command("builtins")
    def theme_builtins() -> None:
        """List all bundled theme names with a short description."""
        from rich.box import ROUNDED
        from rich.table import Table

        table = Table(
            box=ROUNDED,
            show_header=True,
            title="Bundled themes",
            title_justify="left",
            title_style="bold",
            header_style="bold",
            expand=False,
        )
        table.add_column("Name", style=ui._s("id_dim"), no_wrap=True)
        table.add_column("Description", style="default")
        for name in themes.list_builtin_themes():
            meta = themes.get_builtin_meta(name)
            table.add_row(name, meta.get("description", ""))
        console.print(table)


    @theme.command("reset")
    @click.confirmation_option(prompt="Remove the active theme file?")
    def theme_reset() -> None:
        """Remove the active theme file and fall back to the defaults."""
        path = themes.get_theme_path()
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                ui.print_error(f"Cannot remove theme file: {exc}")
                sys.exit(1)
        themes.reset_cache()
        print("Theme file removed (if it existed). Active palette reset to defaults.")


    @theme.command("edit")
    def theme_edit() -> None:
        """Open the active theme file in $VISUAL/$EDITOR.

        Creates a starter template at the active theme path if no file
        exists yet, so the user lands in a pre-populated buffer.
        """
        path = themes.get_theme_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            themes.write_default_theme(path)
            themes.reset_cache()
        try:
            click.edit(filename=str(path))
        except click.ClickException as exc:
            ui.print_error(str(exc))
            sys.exit(1)


    @theme.command("create")
    @click.option(
        "--from",
        "seed_from",
        default=None,
        help="Seed values from a bundled theme (e.g. dracula, nord).",
    )
    @click.option(
        "--name",
        "name",
        default=None,
        help="Value to embed as the new theme's [meta].name. Defaults to "
        "'custom', or to the seeded builtin's name when --from is given.",
    )
    @click.option(
        "--description",
        "description",
        default=None,
        help="Value to embed as the new theme's [meta].description. Defaults "
        "to a generic placeholder, or to the seeded builtin's description "
        "when --from is given.",
    )
    @click.option(
        "--output",
        "output",
        type=click.Path(dir_okay=False, writable=True),
        default=None,
        help="Write the new theme to PATH instead of installing it as active.",
    )
    @click.option(
        "--install/--no-install",
        "install",
        default=None,
        help="After the wizard, install the new theme as active. Defaults to on when --output is not given.",
    )
    def theme_create(
        seed_from: str | None,
        name: str | None,
        description: str | None,
        output: str | None,
        install: bool | None,
    ) -> None:
        """Interactively create a new theme by walking through every role.

        For each of the 28 roles (grouped by Borders / Text / Banner /
        Tables / Heatmap) the wizard prints the current draft value and
        prompts for a replacement. Style values are validated with Rich's
        Style.parse — empty or unparseable values are rejected with a retry
        hint. After every section a multi-line preview is rendered using
        synthetic UI fixtures so the user can confirm the palette before
        committing.

        When ``--from <builtin>`` is given, both the palette *and* the
        ``[meta]`` (name + description) are seeded from the builtin's
        ``[meta]`` table, so the produced file accurately reflects what was
        seeded rather than carrying the wizard's placeholder meta.

        The new theme is written via ``devlog theme create``'s own writer
        (lossless round-trip through ``devlog theme set <file>``). With
        no ``--output`` it is also installed as the active theme.
        """
        if install is None:
            install = output is None

        # Resolve seed palette (and meta) from a builtin when requested.
        seed_meta: dict[str, str] | None = None
        if seed_from is not None:
            try:
                seed = themes.load_builtin_theme(seed_from)
                seed_meta = themes.get_builtin_meta(seed_from)
            except themes.ThemeNotFoundError:
                names = ", ".join(themes.list_builtin_themes())
                ui.print_error(
                    f'Unknown builtin theme "{seed_from}". Available: {names}'
                )
                sys.exit(1)
        else:
            seed = dict(themes.get_active_theme())

        # Defaults for [meta]: explicit flag > seeded builtin > generic placeholder.
        if name is None:
            name = seed_meta["name"] if seed_meta else "custom"
        if description is None:
            description = (
                seed_meta["description"]
                if seed_meta
                else "Custom theme created with `devlog theme create`"
            )

        # Reject meta that would produce unparseable TOML up front, rather
        # than waiting until install and poisoning the active theme file.
        try:
            themes.build_theme_toml({}, name=name, description=description)
        except (ValueError, TypeError):
            ui.print_error(
                f"Invalid --name/--description: name={name!r} description={description!r}"
            )
            sys.exit(1)

        draft: dict[str, str] = dict(seed)

        click.echo(
            f"Creating theme '{name}'. Press Enter to accept each default, "
            "or type a new Rich style (e.g. 'bold #ff8800', 'color(208)')."
        )

        def _validate(value: str) -> str:
            value = value.strip()
            if not value:
                raise click.BadParameter("value cannot be empty")
            if not themes.is_valid_style(value):
                raise click.BadParameter(
                    f"{value!r} is not a valid Rich style"
                )
            return value

        for section_name, roles in themes.SECTIONS.items():
            click.echo("")
            click.echo(f"=== {section_name} ===")
            for role in roles:
                default = draft.get(role, themes.DEFAULT_THEME[role])
                value = click.prompt(
                    role,
                    default=default,
                    show_default=True,
                    value_proc=_validate,
                )
                draft[role] = value
            click.echo("")
            click.echo(theme_preview.render_preview(draft))

        text = themes.build_theme_toml(draft, name=name, description=description)

        if output is not None:
            out_path = Path(output)
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(text, encoding="utf-8")
            except OSError as exc:
                ui.print_error(f"Cannot write output file: {exc}")
                sys.exit(1)
            click.echo(f"Custom theme '{name}' written to {out_path}.")
            if install:
                try:
                    themes.install_theme_file(out_path, themes.get_theme_path())
                except themes.ThemeInstallError as exc:
                    ui.print_error(str(exc))
                    sys.exit(1)
                click.echo(
                    f"Installed as the active theme at {themes.get_theme_path()}."
                )
            return

        if install:
            active = themes.get_theme_path()
            active.parent.mkdir(parents=True, exist_ok=True)
            try:
                active.write_text(text, encoding="utf-8")
            except OSError as exc:
                ui.print_error(f"Cannot write theme file: {exc}")
                sys.exit(1)
            themes.reset_cache()
            click.echo(
                f"Custom theme '{name}' installed at {active} "
                f"({len(themes.get_active_theme())} roles)."
            )
        else:
            click.echo(
                f"Custom theme '{name}' generated but not installed "
                "(--no-install with no --output)."
            )


    @theme.command("diff")
    @click.option(
        "--default",
        "show_defaults",
        is_flag=True,
        default=False,
        help="Show roles whose active value matches the default (audit mode).",
    )
    def theme_diff(show_defaults: bool) -> None:
        """Show the active theme's overrides relative to the defaults.

        With ``--default``, inverts to show roles whose active value
        matches the default (useful for auditing which roles are still
        inheriting).
        """
        active = themes.get_active_theme()
        rows = []
        for role, default in themes._ROLE_DEFAULTS:
            active_value = active.get(role, default)
            if show_defaults:
                if active_value == default:
                    rows.append((role, default, active_value, ""))
            else:
                if active_value != default:
                    rows.append((role, default, active_value, "!"))
        if not rows:
            msg = (
                "No overrides" if not show_defaults
                else "All roles match the defaults"
            )
            print(msg)
            return

        from rich.box import ROUNDED
        from rich.table import Table
        from rich.text import Text

        table = Table(
            box=ROUNDED,
            show_header=True,
            title="Theme diff" if not show_defaults else "Theme audit (matches default)",
            title_justify="left",
            title_style="bold",
            header_style="bold",
            expand=False,
        )
        table.add_column("Role", style=ui._s("id_dim"), no_wrap=True)
        table.add_column("Default", style="default")
        table.add_column("Active", style="default")
        table.add_column("Δ", no_wrap=True)
        for role, default, active_value, marker in rows:
            table.add_row(
                role,
                default,
                active_value,
                Text(marker, style="yellow" if marker else "dim"),
            )
        console.print(table)


    @theme.command("export")
    @click.option("--stdout", "use_stdout", is_flag=True, default=False, help="Write to STDOUT.")
    @click.option("--output", "output", type=click.Path(dir_okay=False, writable=True), default=None, help="Write to a file path.")
    @click.option("--name", "name", default="exported", show_default=True, help="Value to embed as the exported theme's [meta].name.")
    @click.option("--description", "description", default="Exported active theme", show_default=True, help="Value to embed as the exported theme's [meta].description.")
    def theme_export(use_stdout: bool, output: str | None, name: str, description: str) -> None:
        """Write the active palette as a complete, uncommented ``theme.toml``.

        The output round-trips losslessly through ``devlog theme set
        <exported.toml>``. Use ``--stdout`` to print to STDOUT, or
        ``--output PATH`` to write to a file. Exactly one of ``--stdout``
        or ``--output`` is required.
        """
        if use_stdout and output:
            ui.print_error("--stdout and --output are mutually exclusive.")
            sys.exit(1)
        if not use_stdout and not output:
            ui.print_error("Specify --stdout or --output PATH.")
            sys.exit(1)
        text = themes.export_template(name=name, description=description)
        if use_stdout:
            sys.stdout.write(text)
            return
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"Exported active theme to {out_path}.")


    @theme.command("path")
    def theme_path() -> None:
        """Print the path to the active theme file."""
        print(themes.get_theme_path())


