"""The ``devlog git`` command group.

Git integration for devlog:
- `devlog git status` - show git status of journal directory
- `devlog git sync` - auto-commit journal changes
- `devlog git log` - show git log of journal directory
"""

from __future__ import annotations

import json
import sys

import click

from devlog import storage, ui
from devlog._gitops import (
    _existing_ancestor,
    auto_commit_journal,
    get_git_repo_root,
    git_status,
)

console = ui.console
err_console = ui.err_console


def register(main) -> None:
    """Attach the ``git`` group (and its subcommands) to *main*."""

    # ---------------------------------------------------------------------------
    # git
    # ---------------------------------------------------------------------------

    @main.group()
    def git() -> None:
        """Git integration commands."""

    @git.command("status")
    @click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
    def git_status_cmd(as_json: bool) -> None:
        """Show git status of the journal directory."""
        data_dir = storage.get_data_dir()
        status = git_status(data_dir)

        if as_json:
            print(json.dumps(status, ensure_ascii=False))
            return

        if not status["is_git_repo"]:
            ui.print_info("Journal directory is not a git repository.")
            if status["repo_root"]:
                console.print(
                    f"  Found git repo at parent: {status['repo_root']}"
                )
            return

        console.print(ui.success_line(f"Git repository: {status['repo_root']}"))
        console.print(f"  Branch: {status['branch'] or 'unknown'}")
        console.print(f"  Remote: {status['remote'] or 'none'}")

        if status["clean"]:
            console.print(ui.success_line("  Status: clean (no uncommitted changes)"))
        else:
            ui.print_warning("  Status: uncommitted changes")
            for change in status["changes"]:
                console.print(f"    {change}")

    @git.command("sync")
    @click.option(
        "--message", "-m", default="devlog: journal update", help="Commit message."
    )
    @click.option("--quiet", "-q", is_flag=True, help="Suppress output.")
    def git_sync(message: str, quiet: bool) -> None:
        """Auto-commit journal changes to git."""
        data_dir = storage.get_data_dir()
        repo_root = get_git_repo_root(_existing_ancestor(data_dir))

        if repo_root is None:
            ui.print_error("Journal directory is not a git repository.")
            sys.exit(1)

        if not quiet:
            console.print("Checking for changes...")

        success, msg = auto_commit_journal(repo_root, message)

        if success:
            if not quiet:
                console.print(ui.success_line(msg))
        else:
            ui.print_error(msg)
            sys.exit(1)

    @git.command("log")
    @click.option("--oneline", is_flag=True, help="One commit per line.")
    @click.option("--limit", "-n", type=int, default=20, help="Max commits to show.")
    def git_log(oneline: bool, limit: int) -> None:
        """Show git log of the journal directory."""
        data_dir = storage.get_data_dir()
        repo_root = get_git_repo_root(_existing_ancestor(data_dir))

        if repo_root is None:
            ui.print_error("Journal directory is not a git repository.")
            sys.exit(1)

        from devlog._gitops import _run_git

        if oneline:
            exit_code, stdout, stderr = _run_git(
                ["log", "--oneline", f"-{limit}"], repo_root
            )
        else:
            exit_code, stdout, stderr = _run_git(
                ["log", f"-{limit}"], repo_root
            )

        if exit_code != 0:
            ui.print_error(f"git log failed: {stderr}")
            sys.exit(1)

        print(stdout)