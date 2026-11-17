"""The ``devlog digest`` and ``devlog ask`` commands (the ``[ai]`` tier).

Both commands are fully offline and default to **extractive** output —
no model, no network. When ``--llm`` is passed (or ``DEVLOG_AI=1`` for
``digest``), the extractive text is handed to a local Ollama server for
a written summary; ``ask`` always uses local embeddings for semantic
search (one call to embed the query). Errors from Ollama are rendered
as actionable messages and exit 1; they never crash with a traceback.
"""

from __future__ import annotations

import json
import sys

import click

from devlog import storage, ui
from devlog._aiops import summarize_week_text
from devlog._ollama import OllamaError

console = ui.console
err_console = ui.err_console


def register(main) -> None:
    """Attach the ``digest`` and ``ask`` commands to *main*."""

    def _handle_ollama_error(exc: OllamaError) -> None:
        ui.print_error(str(exc))
        sys.exit(1)

    @main.command()
    @click.option(
        "--week",
        "period",
        flag_value="week",
        default=True,
        help="Summarize the last 7 days.",
    )
    @click.option(
        "--month",
        "period",
        flag_value="month",
        help="Summarize the last 30 days.",
    )
    @click.option(
        "--days",
        type=int,
        default=None,
        help="Summarize the last N days (alternative to --week/--month).",
    )
    @click.option(
        "--from",
        "since",
        default=None,
        help="Start date (inclusive). See date formats below.",
    )
    @click.option(
        "--to",
        "until",
        default=None,
        help="End date (inclusive). See date formats below.",
    )
    @click.option(
        "--anchor",
        default=None,
        help="Anchor day (YYYY-MM-DD) for --week/--month. Defaults to today.",
    )
    @click.option(
        "--llm",
        is_flag=True,
        default=False,
        help="Also produce an LLM-written summary via local Ollama.",
    )
    @click.option("--model", default=None, help="Ollama model for --llm (default: llama3.2).")
    @click.option("--quiet", "-q", is_flag=True, help="Print the digest text only.")
    @click.option(
        "--json", "as_json", is_flag=True, help="Output a single JSON summary object."
    )
    def digest(
        period: str,
        days: int | None,
        anchor: str | None,
        since: str | None,
        until: str | None,
        llm: bool,
        model: str | None,
        quiet: bool,
        as_json: bool,
    ) -> None:
        """Summarize recent journal activity (extractive, or --llm for a written summary)."""
        from datetime import timedelta

        from devlog import _dates, _ollama

        all_entries = storage.load_entries()
        from devlog.cli._shared import _resolve_tz_or_exit
        tz = _resolve_tz_or_exit()

        # Resolve date range based on period
        if period == "week":
            period_days = 7
        elif period == "month":
            period_days = 30
        else:
            period_days = 7

        # --days overrides period
        if days is not None:
            period_days = days

        # Handle --from/--to (highest priority)
        if since or until:
            since_dt, until_dt = _dates._parse_since_until(since, until)
            # Filter entries by date range
            filtered = _dates._filter_by_date(all_entries, since_dt, until_dt)
            # Determine start/end for display
            if since_dt:
                start_date = since_dt.astimezone(tz).date() if tz else since_dt.date()
            else:
                start_date = min((e.created_dt.astimezone(tz).date() for e in filtered if e.created_dt.year > 1970), default=_dates.today_local_date(tz))
            if until_dt:
                end_date = until_dt.astimezone(tz).date() if tz else until_dt.date()
            else:
                end_date = max((e.created_dt.astimezone(tz).date() for e in filtered if e.created_dt.year > 1970), default=_dates.today_local_date(tz))
            entries = filtered
        else:
            # Use period/anchor
            if anchor:
                try:
                    anchor_dt = _dates._parse_date_bound(anchor, tz=tz)
                except click.BadParameter as exc:
                    ui.print_error(str(exc))
                    sys.exit(2)
                end_date = anchor_dt.astimezone(tz).date() if tz is not None else anchor_dt.date()
            else:
                end_date = _dates.today_local_date(tz)

            start_date = end_date - timedelta(days=period_days - 1)

            entries = _dates._filter_by_local_window(
                all_entries, end_date=end_date, days=period_days - 1, tz=tz
            )

        entries.sort(key=lambda e: e.created_at, reverse=False)

        text = summarize_week_text(entries, start_date=start_date, end_date=end_date, tz=tz)

        if llm:
            try:
                reply = _ollama.generate(
                    _digest_prompt(text), model=model or "llama3.2"
                )
            except OllamaError as exc:
                _handle_ollama_error(exc)
                return
            if quiet:
                print(reply)
            else:
                console.print(f"[bold]LLM summary[/bold] ({start_date} → {end_date})")
                console.print(reply)
            return

        if as_json:
            print(
                json.dumps(
                    {
                        "period": period,
                        "start": start_date.isoformat(),
                        "end": end_date.isoformat(),
                        "week_start": start_date.isoformat(),
                        "week_end": end_date.isoformat(),
                        "entry_count": len(entries),
                        "digest": text,
                    },
                    ensure_ascii=False,
                )
            )
            return

        if not entries:
            ui.print_info(f"No entries in the selected period ({start_date} to {end_date}).")
            return

        if not quiet:
            console.print(
                f"[bold]Digest[/bold] · {start_date} → {end_date} · "
                f"{len(entries)} {ui.plural_noun(len(entries), 'entry')}"
            )
            body_lines = text.splitlines()
            if body_lines and body_lines[0].startswith("Weekly digest:"):
                body_lines = body_lines[1:]
            text = "\n".join(body_lines).lstrip("\n")
        print(text)

    @main.command()
    @click.argument("query", required=True)
    @click.option("--top", "-n", default=5, show_default=True, help="Max matches to show.")
    @click.option(
        "--json", "as_json", is_flag=True, help="Output structured JSON results."
    )
    @click.option("--model", default=None, help="Embedding model (default: nomic-embed-text).")
    def ask(query: str, top: int, as_json: bool, model: str | None) -> None:
        """Search the journal semantically: ASK "what did I do on auth?".

        Uses local embeddings (Ollama). On first run the whole journal is
        embedded once and cached in <data dir>/embeddings.json; later runs
        only embed new entries and the query.
        """

        from devlog import _aiindex

        all_entries = storage.load_entries()
        if not all_entries:
            if as_json:
                print(json.dumps({"query": query, "results": []}, ensure_ascii=False))
            else:
                ui.print_info("No entries in the journal yet — nothing to ask.")
            return

        data_dir = storage.get_data_dir()
        import os

        previous_model = os.environ.get("DEVLOG_EMBED_MODEL")
        if model:
            # Explicit --model wins over DEVLOG_EMBED_MODEL for this run.
            os.environ["DEVLOG_EMBED_MODEL"] = model
        try:
            index = _aiindex.ensure_index(all_entries, data_dir)
            ranked = _aiindex.search_entries(query, all_entries, index, top=top)
        except OllamaError as exc:
            _handle_ollama_error(exc)
            return
        finally:
            if model:
                if previous_model is None:
                    os.environ.pop("DEVLOG_EMBED_MODEL", None)
                else:
                    os.environ["DEVLOG_EMBED_MODEL"] = previous_model

        by_id = {e.id: e for e in all_entries}
        if as_json:
            print(
                json.dumps(
                    {
                        "query": query,
                        "results": [
                            {
                                "id": entry_id,
                                "message": by_id[entry_id].message,
                                "created_at": by_id[entry_id].created_at,
                                "score": round(score, 4),
                            }
                            for entry_id, score in ranked
                            if entry_id in by_id
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return

        if not ranked:
            ui.print_info("No matching entries found.")
            return
        console.print(f"[bold]Q:[/bold] {query}")
        for entry_id, score in ranked:
            entry = by_id[entry_id]
            console.print(
                f"  {entry.created_at}  {entry.message}  "
                f"[dim](score {score:.2f}, id {entry_id[:8]})[/dim]"
            )


def _digest_prompt(digest_text: str) -> str:
    """Wrap the extractive digest in a short instruction for the local LLM."""
    return (
        "You are summarizing a week of a developer's journal. Write a short, "
        "factual summary (a few sentences) of the entries below. Do not invent "
        "anything.\n\n" + digest_text
    )
