"""Parametrized coverage for the ``--limit must be a positive integer`` contract.

Every command that accepts ``--limit`` (or the ``N`` positional of
``tail``) must reject ``0`` and negative values with the same error
string and exit code 1. Centralising the check here kills the
copy-paste across ``test_list``, ``test_search``, ``test_tags``,
``test_tag_command``, ``test_today_tail_stats`` and
``test_week_yesterday``.
"""

import pytest
from click.testing import CliRunner

from devlog.cli import main
from devlog import storage
from devlog.models import Entry


_LIMIT_COMMANDS = [
    ["list"],
    ["search", "x"],
    ["tags"],
    ["tag", "x"],
    ["today"],
    ["yesterday"],
    ["week"],
]


@pytest.fixture(autouse=True)
def _seed_entry(monkeypatch, tmp_path):
    """Seed one entry so commands that filter an empty list don't
    short-circuit before the ``--limit`` validation runs."""
    monkeypatch.setenv("DEVLOG_DATA_DIR", str(tmp_path))
    storage.add_entry(
        Entry(
            id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            message="x",
            tags=[],
            created_at="2025-01-01T00:00:00Z",
        )
    )


@pytest.mark.parametrize("argv", _LIMIT_COMMANDS)
def test_limit_must_be_positive(argv, tmp_path):
    """Every limit-accepting command rejects 0 with the same message."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})
    result = runner.invoke(main, argv + ["--limit", "0"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


@pytest.mark.parametrize("argv", _LIMIT_COMMANDS)
def test_limit_negative_rejected(argv, tmp_path):
    """Negative values are also rejected — covers the ``< 0`` branch
    in :func:`devlog.cli._require_positive_int`."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})
    result = runner.invoke(main, argv + ["--limit", "-3"])
    assert result.exit_code == 1
    assert "--limit must be a positive integer" in result.output


def test_tail_n_must_be_positive(tmp_path):
    """``tail`` uses a positional ``N`` rather than ``--limit``; same
    contract, separate code path."""
    runner = CliRunner(env={"DEVLOG_DATA_DIR": str(tmp_path)})
    result = runner.invoke(main, ["tail", "0"])
    assert result.exit_code == 1
    assert "N must be a positive integer" in result.output
