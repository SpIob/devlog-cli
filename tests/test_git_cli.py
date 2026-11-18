"""CLI-layer tests for the ``devlog git`` command group (:mod:`devlog._git_cli`).

These drive ``git status`` / ``git sync`` / ``git log`` through the Click
runner against a *real* temporary git repository -- no ``subprocess``
mocking -- mirroring the ``repo`` fixture already used for ``_gitops`` in
``tests/test_git_features.py``.

The ``runner`` fixture from ``tests/conftest.py`` pins ``DEVLOG_DATA_DIR``
to ``tmp_path``; the commands resolve the repo from ``storage.get_data_dir()``,
so the repo is initialised at that very same ``tmp_path``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devlog.cli import main
from tests.conftest import strip_ansi


def _git(args: list[str], cwd) -> subprocess.CompletedProcess:
    """Run a git command in *cwd*, raising on a non-zero exit code."""
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture()
def git_repo(tmp_path):
    """A real git repo at ``tmp_path`` with one conventional commit.

    ``tmp_path`` is also the ``DEVLOG_DATA_DIR`` the ``runner`` fixture
    points at, so the CLI resolves this repo for ``git status``/``sync``/
    ``log``.
    """
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("one\n")
    _git(["add", "f.txt"], tmp_path)
    _git(["commit", "-q", "-m", "feat(auth): add login flow"], tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# git status
# ---------------------------------------------------------------------------


def test_git_status_not_a_repo(runner, tmp_path):
    """No repo anywhere up the tree: info line, no parent-repo note, exit 0."""
    result = runner.invoke(main, ["git", "status"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "not a git repository" in out.lower()
    assert "Found git repo at parent" not in out


def test_git_status_reports_parent_repo(runner, monkeypatch):
    """``repo_root`` set while ``is_git_repo`` is false prints the parent note.

    ``git_status()`` never itself returns that shape (it short-circuits to
    ``repo_root=None`` when no repo is found), so the branch is only
    reachable by injecting the dict the command expects.
    """
    import devlog._git_cli as git_cli

    fake = {
        "is_git_repo": False,
        "repo_root": "/tmp/parent-repo",
        "clean": True,
        "changes": [],
        "branch": None,
        "remote": None,
    }
    monkeypatch.setattr(git_cli, "git_status", lambda data_dir: dict(fake))

    result = runner.invoke(main, ["git", "status"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "not a git repository" in out.lower()
    assert "Found git repo at parent: /tmp/parent-repo" in out


def test_git_status_clean_repo(runner, git_repo):
    """Inside a clean repo: repo root, branch, remote and clean status."""
    branch = _git(["branch", "--show-current"], git_repo).stdout.strip()
    # Widen the console so Rich doesn't soft-wrap the (long) tmp_path.
    result = runner.invoke(main, ["git", "status"], env={"COLUMNS": "400"})
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert f"Git repository: {git_repo}" in out
    assert f"Branch: {branch}" in out
    assert "Remote: none" in out
    assert "clean (no uncommitted changes)" in out


def test_git_status_uncommitted_changes(runner, git_repo):
    """Dirty working tree: warning line plus one line per changed path."""
    (git_repo / "f.txt").write_text("changed\n")
    result = runner.invoke(main, ["git", "status"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "uncommitted changes" in out
    assert "f.txt" in out


def test_git_status_json_clean_repo(runner, git_repo):
    """The --json flag emits the raw status dict (already-covered path)."""
    result = runner.invoke(main, ["git", "status", "--json"])
    assert result.exit_code == 0, result.output
    obj = json.loads(result.output)
    assert obj["is_git_repo"] is True
    assert obj["clean"] is True
    assert obj["branch"]


# ---------------------------------------------------------------------------
# git sync
# ---------------------------------------------------------------------------


def test_git_sync_not_a_repo(runner, tmp_path):
    """Outside any repo: error panel + exit 1."""
    result = runner.invoke(main, ["git", "sync"])
    assert result.exit_code == 1
    out = strip_ansi(result.output)
    assert "not a git repository" in out.lower()


def test_git_sync_commits_changes(runner, git_repo):
    """Happy path: pending change is committed and reported."""
    (git_repo / "journal.txt").write_text("entry\n")
    result = runner.invoke(main, ["git", "sync", "-m", "journal sync"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "Checking for changes..." in out
    assert "Committed successfully" in out
    log = _git(["log", "--oneline", "-1"], git_repo).stdout
    assert "journal sync" in log


def test_git_sync_commit_failure(runner, git_repo):
    """A failing commit surfaces the error and exits 1."""
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (git_repo / "journal.txt").write_text("entry\n")

    result = runner.invoke(main, ["git", "sync"])
    assert result.exit_code == 1
    out = strip_ansi(result.output)
    assert "git commit failed" in out


# ---------------------------------------------------------------------------
# git log
# ---------------------------------------------------------------------------


def test_git_log_not_a_repo(runner, tmp_path):
    """Outside any repo: error panel + exit 1."""
    result = runner.invoke(main, ["git", "log"])
    assert result.exit_code == 1
    out = strip_ansi(result.output)
    assert "not a git repository" in out.lower()


def test_git_log_oneline(runner, git_repo):
    """--oneline prints one short-summary line per commit."""
    result = runner.invoke(main, ["git", "log", "--oneline"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "feat(auth): add login flow" in out


def test_git_log_full(runner, git_repo):
    """Default (non-oneline) output includes the commit metadata."""
    result = runner.invoke(main, ["git", "log"])
    assert result.exit_code == 0, result.output
    out = strip_ansi(result.output)
    assert "feat(auth): add login flow" in out
    assert "Author:" in out


def test_git_log_failure_unborn_head(runner, tmp_path):
    """A repo with no commits makes ``git log`` fail -> error + exit 1."""
    _git(["init", "-q"], tmp_path)
    result = runner.invoke(main, ["git", "log"])
    assert result.exit_code == 1
    out = strip_ansi(result.output)
    assert "git log failed" in out
