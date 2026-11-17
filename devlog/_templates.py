"""Entry templates for `devlog add --template`.

Templates are stored as JSON files in <data-dir>/templates/ with a
structure that supports variable substitution. A template looks like:

{
  "name": "bug-report",
  "description": "Standard bug report template",
  "tags": ["bug", "backend"],
  "message": "## Bug Report\\n\\n**What happened:** {what}\\n\\n**Expected:** {expected}\\n\\n**Steps to reproduce:** {steps}",
  "variables": {
    "what": "What actually happened?",
    "expected": "What did you expect?",
    "steps": "Steps to reproduce"
  }
}

When `devlog add --template bug-report` is run, the user is prompted
for each variable (with the description as hint), then the template
is expanded and used as the entry message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

TEMPLATES_DIR_NAME = "templates"


def _templates_dir() -> Path:
    from devlog.storage._base import get_data_dir

    return get_data_dir() / TEMPLATES_DIR_NAME


def _list_template_files() -> list[Path]:
    d = _templates_dir()
    if not d.exists():
        return []
    return sorted(d.glob("*.json"))


@dataclass
class Template:
    name: str
    description: str
    tags: list[str]
    message: str
    variables: dict[str, str]

    @classmethod
    def from_file(cls, path: Path) -> Template | None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            # Back-compat: old templates might not have variables
            variables = data.get("variables", {})
            if isinstance(variables, dict):
                # Ensure all values are strings
                variables = {k: str(v) for k, v in variables.items()}
            else:
                variables = {}
            return cls(
                name=data.get("name", path.stem),
                description=data.get("description", ""),
                tags=[str(t) for t in data.get("tags", [])],
                message=str(data.get("message", "")),
                variables=variables,
            )
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "message": self.message,
            "variables": self.variables,
        }

    def save(self) -> None:
        _templates_dir().mkdir(parents=True, exist_ok=True)
        path = _templates_dir() / f"{self.name}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)

    def delete(self) -> bool:
        path = _templates_dir() / f"{self.name}.json"
        try:
            path.unlink()
            return True
        except OSError:
            return False


def list_templates() -> list[Template]:
    return [t for f in _list_template_files() if (t := Template.from_file(f)) is not None]


def get_template(name: str) -> Template | None:
    path = _templates_dir() / f"{name}.json"
    if not path.exists():
        return None
    return Template.from_file(path)


def expand_template(template: Template, values: dict[str, str]) -> tuple[str, list[str]]:
    """Expand a template with the given variable values.

    Returns (expanded_message, tags).
    """
    message = template.message
    for key, val in values.items():
        message = message.replace(f"{{{key}}}", val)
    # Replace any remaining {var} with empty string (optional vars)
    import re

    message = re.sub(r"\{[^}]+\}", "", message)
    return message, template.tags


# Built-in templates that ship with devlog (can be overridden by user files)
BUILTIN_TEMPLATES: dict[str, Template] = {
    "bug": Template(
        name="bug",
        description="Standard bug report",
        tags=["bug"],
        message=(
            "## Bug Report\n\n"
            "**What happened:** {what}\n\n"
            "**Expected:** {expected}\n\n"
            "**Steps to reproduce:** {steps}\n\n"
            "**Environment:** {env}"
        ),
        variables={
            "what": "What actually happened?",
            "expected": "What did you expect?",
            "steps": "Steps to reproduce",
            "env": "OS, Python version, etc.",
        },
    ),
    "feature": Template(
        name="feature",
        description="Feature work log",
        tags=["feature"],
        message=(
            "## Feature: {title}\n\n"
            "**Goal:** {goal}\n\n"
            "**Approach:** {approach}\n\n"
            "**Status:** {status}"
        ),
        variables={
            "title": "Feature name",
            "goal": "What problem does this solve?",
            "approach": "Implementation approach",
            "status": "Status (started/in-progress/done)",
        },
    ),
    "meeting": Template(
        name="meeting",
        description="Meeting notes template",
        tags=["meeting", "notes"],
        message=(
            "## Meeting: {title}\n\n"
            "**Attendees:** {attendees}\n\n"
            "**Key decisions:** {decisions}\n\n"
            "**Action items:** {actions}\n\n"
            "**Notes:** {notes}"
        ),
        variables={
            "title": "Meeting title",
            "attendees": "Who was there?",
            "decisions": "Decisions made",
            "actions": "Action items (owner: task)",
            "notes": "Free-form notes",
        },
    ),
    "deploy": Template(
        name="deploy",
        description="Deployment log",
        tags=["deploy", "devops"],
        message=(
            "## Deployment: {service}\n\n"
            "**Version:** {version}\n\n"
            "**Environment:** {env}\n\n"
            "**Changes:** {changes}\n\n"
            "**Status:** {status}"
        ),
        variables={
            "service": "Service name",
            "version": "Version/tag deployed",
            "env": "Environment (prod/staging)",
            "changes": "What changed?",
            "status": "Status (success/failed/rolled back)",
        },
    ),
}


def get_all_templates() -> dict[str, Template]:
    """Return merged dict of built-in + user templates (user wins)."""
    result = dict(BUILTIN_TEMPLATES)
    for t in list_templates():
        result[t.name] = t
    return result