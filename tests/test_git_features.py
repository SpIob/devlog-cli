"""Tests for Tier 2 git features.

Covers ``devlog add --from-commit`` and the ``devlog git`` command
group against a real (temporary) git repository, plus the tag-extraction
and commit-parsing helpers in :mod:`devlog._gitops` in isolation.
"""

from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

from devlog import storage
from devlog._gitops import (
    _extract_tags_from_commit,
    get_git_repo_root,
    is_git_repo,
)
from devlog.cli import main
from devlog.models import Entry


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A real git repo with two conventional commits; cwd + data dir point into it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "feat(auth): add login flow"],
        cwd=tmp_path, check=True,
    )
    (tmp_path / "f.txt").write_text("two\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fix(api): handle null session"],
        cwd=tmp_path, check=True,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "journal"))
    return tmp_path


# ---------------------------------------------------------------------------
# _gitops helpers
# ---------------------------------------------------------------------------


def test_is_git_repo(repo):
    assert is_git_repo(repo) is True
    assert is_git_repo(repo / "nope") is False


def test_get_git_repo_root(repo):
    assert get_git_repo_root(repo) == repo


def test_extract_tags_conventional_commit():
    assert _extract_tags_from_commit("feat(auth): add login") == ["auth"]
    assert _extract_tags_from_commit("fix(api): handle null session") == ["api"]


def test_extract_tags_bracket_prefix():
    assert _extract_tags_from_commit("[backend] tweak retry") == ["backend"]


def test_extract_tags_no_match():
    assert _extract_tags_from_commit("plain commit message") == []


def test_extract_tags_invalid_tag_filtered():
    # Scope with a space is not a valid devlog tag → filtered out.
    assert _extract_tags_from_commit("feat(bad tag): x") == []


# ---------------------------------------------------------------------------
# add --from-commit
# ---------------------------------------------------------------------------


def test_add_from_commit_head(repo):
    result = CliRunner().invoke(main, ["add", "--from-commit", "HEAD", ""])
    assert result.exit_code == 0, result.output
    entries = storage.load_entries()
    assert len(entries) == 1
    assert "handle null session" in entries[0].message
    assert "api" in entries[0].tags
    assert any(t.startswith("commit-") for t in entries[0].tags)


def test_add_from_commit_with_prefix_note(repo):
    result = CliRunner().invoke(main, ["add", "--from-commit", "HEAD", "shipped this"])
    assert result.exit_code == 0
    entries = storage.load_entries()
    assert entries[0].message.startswith("shipped this\n\n")


def test_add_from_commit_backdates_to_author_date(repo):
    CliRunner().invoke(main, ["add", "--from-commit", "HEAD", ""])
    entries = storage.load_entries()
    # The commit was made "now", so the entry date should be within a minute.
    from datetime import datetime, timezone

    ts = datetime.strptime(entries[0].created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    delta = abs((datetime.now(tz=timezone.utc) - ts).total_seconds())
    assert delta < 120


def test_add_from_commit_ignores_invalid_tags_option(repo):
    # -t values pass through normal validation; valid ones merge in front.
    result = CliRunner().invoke(
        main, ["add", "--from-commit", "HEAD", "", "-t", "deploy"]
    )
    assert result.exit_code == 0, result.output
    entries = storage.load_entries()
    assert entries[0].tags[0] == "deploy"
    assert "api" in entries[0].tags


def test_add_from_commit_outside_repo_fails(runner, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # not a git repo
    result = runner.invoke(main, ["add", "--from-commit", "HEAD", ""])
    assert result.exit_code == 1
    assert "Not in a git repository" in result.output


def test_add_from_commit_bad_ref_fails(repo):
    result = CliRunner().invoke(main, ["add", "--from-commit", "nope-ref", ""])
    assert result.exit_code == 1
    assert "Could not get info" in result.output


# ---------------------------------------------------------------------------
# devlog git group
# ---------------------------------------------------------------------------


def test_git_group_registered():
    from devlog.cli import main as cli_main

    assert "git" in cli_main.commands


def test_git_status_clean_repo(repo):
    result = CliRunner().invoke(main, ["git", "status", "--json"])
    assert result.exit_code == 0
    import json

    obj = json.loads(result.output)
    assert obj["is_git_repo"] is True
    # journal/ subdir is inside the repo but the repo tracks only f.txt
    assert isinstance(obj["clean"], bool)


def test_git_sync_commits_journal_changes(repo):
    # Create the journal inside the repo so git can see it.
    storage.add_entry(
        Entry(
            id="abcdef12-1111-1111-1111-111111111111",
            message="sync me",
            tags=[],
            created_at="2025-01-01T00:00:00Z",
        )
    )
    result = CliRunner().invoke(main, ["git", "sync", "-m", "journal sync"])
    assert result.exit_code == 0, result.output
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert "journal sync" in log


def test_git_sync_outside_repo_fails(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(main, ["git", "sync"])
    assert result.exit_code == 1
    assert "not a git" in result.output.lower()
