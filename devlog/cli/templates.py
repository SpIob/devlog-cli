"""The ``devlog template`` command group."""

from __future__ import annotations

import sys

import click

from devlog import ui
from devlog._templates import (
    BUILTIN_TEMPLATES,
    Template,
    get_all_templates,
    get_template,
)

console = ui.console
err_console = ui.err_console


def _prompt_for_variables(template: Template) -> dict[str, str]:
    """Interactively prompt for template variables."""
    values = {}
    for var, hint in template.variables.items():
        prompt = f"{var} ({hint})"
        val = click.prompt(prompt, default="", show_default=False)
        values[var] = val
    return values


def register(main) -> None:
    @main.group()
    def template() -> None:
        """Manage entry templates for `devlog add --template`."""

    @template.command("list")
    @click.option("--quiet", "-q", is_flag=True, help="Output raw JSON lines.")
    def template_list(quiet: bool) -> None:
        """List all available templates."""
        templates = get_all_templates()

        if quiet:
            import json

            for t in templates.values():
                print(
                    json.dumps(
                        {
                            "name": t.name,
                            "description": t.description,
                            "tags": t.tags,
                            "variables": list(t.variables.keys()),
                            "builtin": t.name in BUILTIN_TEMPLATES,
                        },
                        ensure_ascii=False,
                    )
                )
            return

        if not templates:
            ui.print_info("No templates found.")
            return

        from rich.table import Table

        table = Table(title="Available Templates", show_header=True)
        table.add_column("Name", style="bold")
        table.add_column("Description")
        table.add_column("Tags")
        table.add_column("Variables")
        table.add_column("Source", style="dim")

        for t in templates.values():
            source = "builtin" if t.name in BUILTIN_TEMPLATES else "user"
            table.add_row(
                t.name,
                t.description or "—",
                ", ".join(t.tags) if t.tags else "—",
                ", ".join(t.variables.keys()) if t.variables else "—",
                source,
            )

        console.print(table)

    @template.command("show")
    @click.argument("name")
    @click.option("--quiet", "-q", is_flag=True, help="Output as JSON.")
    def template_show(name: str, quiet: bool) -> None:
        """Show a template's full content."""
        t = get_all_templates().get(name)
        if t is None:
            ui.print_error(f"Template not found: {name}")
            sys.exit(1)

        if quiet:
            import json

            print(json.dumps(t.to_dict(), ensure_ascii=False))
            return

        from rich.panel import Panel
        from rich.syntax import Syntax

        console.print(Panel(t.description or "No description", title=f"Template: {t.name}"))
        console.print(f"[bold]Tags:[/bold] {', '.join(t.tags) if t.tags else '—'}")
        console.print(
            f"[bold]Variables:[/bold] {', '.join(t.variables.keys()) if t.variables else '—'}"
        )
        console.print()
        console.print(Syntax(t.message, "markdown", theme="monokai"))

    @template.command("save")
    @click.argument("name")
    @click.option("--description", "-d", default="", help="Template description.")
    @click.option("--tag", "-t", "tags", multiple=True, help="Default tags (repeatable).")
    @click.option("--message", "-m", default=None, help="Template message (use - for stdin).")
    @click.option("--var", "variables", multiple=True, help="Variable as 'name=hint' (repeatable).")
    def template_save(
        name: str,
        description: str,
        tags: tuple[str, ...],
        message: str | None,
        variables: tuple[str, ...],
    ) -> None:
        """Save a new template (or overwrite an existing one)."""
        import sys

        if message == "-":
            if sys.stdin.isatty():
                ui.print_error("stdin is a TTY; nothing to read.")
                sys.exit(1)
            message = sys.stdin.read()
        elif message is None:
            message = click.prompt("Template message", default="", show_default=False)

        vars_dict = {}
        for v in variables:
            if "=" in v:
                k, hint = v.split("=", 1)
                vars_dict[k.strip()] = hint.strip()
            else:
                ui.print_error(f"Invalid variable format: {v} (use 'name=hint')")
                sys.exit(1)

        # Allow interactive variable definition if none given
        if not vars_dict and "{" in message:
            import re

            found = re.findall(r"\{([^}]+)\}", message)
            for var in set(found):
                hint = click.prompt(f"Hint for {{{var}}}", default="", show_default=False)
                if hint:
                    vars_dict[var] = hint

        t = Template(
            name=name,
            description=description,
            tags=[t.strip().lower() for t in tags],
            message=message,
            variables=vars_dict,
        )
        try:
            t.save()
        except ValueError as exc:
            ui.print_error(str(exc))
            sys.exit(1)
        console.print(ui.success_line(f"Saved template: {name}"))

    @template.command("edit")
    @click.argument("name")
    def template_edit(name: str) -> None:
        """Open a user template in $EDITOR."""
        from devlog.cli._shared import _edit_in_editor

        t = get_template(name)
        if t is None:
            ui.print_error(f"User template not found: {name} (cannot edit builtin)")
            sys.exit(1)

        import json

        content = json.dumps(t.to_dict(), indent=2, ensure_ascii=False)
        new_content = _edit_in_editor(content)
        if new_content is None:
            ui.print_error("Editor exited abnormally; no changes saved.")
            sys.exit(1)

        try:
            data = json.loads(new_content)
        except json.JSONDecodeError as exc:
            ui.print_error(f"Invalid JSON: {exc}")
            sys.exit(1)

        t = Template(
            name=data.get("name", name),
            description=data.get("description", ""),
            tags=[str(t) for t in data.get("tags", [])],
            message=str(data.get("message", "")),
            variables={k: str(v) for k, v in data.get("variables", {}).items()},
        )
        try:
            t.save()
        except ValueError as exc:
            ui.print_error(str(exc))
            sys.exit(1)
        console.print(ui.success_line(f"Updated template: {name}"))

    @template.command("delete")
    @click.argument("name")
    @click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
    def template_delete(name: str, yes: bool) -> None:
        """Delete a user template."""
        if name in BUILTIN_TEMPLATES:
            ui.print_error(f"Cannot delete builtin template: {name}")
            sys.exit(1)

        t = get_template(name)
        if t is None:
            ui.print_error(f"Template not found: {name}")
            sys.exit(1)

        if not yes:
            if not click.confirm(f"Delete template '{name}'?", default=False):
                ui.print_info("Aborted.")
                return

        if t.delete():
            console.print(ui.success_line(f"Deleted template: {name}"))
        else:
            ui.print_error("Failed to delete template.")
            sys.exit(1)
