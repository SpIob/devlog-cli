"""Shell completion script generators for devlog.

Extracted from cli.py to keep the main CLI module focused on Click wiring.
"""

from __future__ import annotations

from devlog import ui


def _get_commands():
    """Lazily import COMMANDS to avoid circular imports."""
    from devlog.cli import COMMANDS
    return COMMANDS


def _bash_completion() -> str:
    """Generate a bash completion script driven by :data:`COMMANDS`.

    A hand-written snippet is kept for the per-subcommand option
    completions (``list|search|...``, ``tag``, ``theme``) — those are
    not generic enough to be worth driving from :data:`COMMANDS`. The
    top-level command list, however, is generated so adding a new
    command in :data:`COMMANDS` automatically appears in the
    completion script.
    """
    COMMANDS = _get_commands()
    names = " ".join(name for name, _ in COMMANDS)
    return f"""# bash completion for devlog
# Source this file or copy it into ~/.bash_completion.d/
_devlog_completion() {{
    local cur prev words cword
    _init_completion || return
    local commands="{names}"
    if [[ ${{cword}} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "${{commands}}" -- "${{cur}}"))
        return
    fi
    case "${{words[1]}}" in
        edit|delete|show) COMPREPLY=($(compgen -W "$(devlog list --quiet 2>/dev/null | python3 -c 'import sys,json
for line in sys.stdin: print(json.loads(line)["id"][:8])')" -- "${{cur}}")) ;;
        list|search|tail|export) COMPREPLY=($(compgen -W "--tag --limit --all --quiet --since --until --format --output" -- "${{cur}}")) ;;
        tag) COMPREPLY=($(compgen -W "--delete --yes --dry-run --limit --all --quiet" -- "${{cur}}")) ;;
        theme) COMPREPLY=($(compgen -W "list show set path reset edit diff export use builtins create" -- "${{cur}}")) ;;
    esac
}}
complete -F _devlog_completion devlog
"""


def _zsh_completion() -> str:
    """Generate a zsh completion script driven by :data:`COMMANDS`."""
    COMMANDS = _get_commands()
    lines = ["#compdef devlog", "# zsh completion for devlog", "_devlog() {", "    local -a commands", "    commands=("]
    for name, desc in COMMANDS:
        # zsh single-quoted strings: escape any embedded single quote.
        safe = desc.replace("'", "'\\''")
        lines.append(f"        '{name}:{safe}'")
    lines.append("    )")
    lines.append("    _describe 'command' commands")
    lines.append("}")
    lines.append('_devlog "$@"')
    return "\n".join(lines) + "\n"


def _fish_completion() -> str:
    """Generate a fish completion script driven by :data:`COMMANDS`."""
    COMMANDS = _get_commands()
    lines = [
        "# fish completion for devlog",
        "complete -c devlog -f",
    ]
    for name, desc in COMMANDS:
        # fish double-quoted strings: escape backslash, double-quote, $.
        safe = desc.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        lines.append(
            f'complete -c devlog -n "__fish_use_subcommand" -a "{name}" -d "{safe}"'
        )
    return "\n".join(lines) + "\n"


def completions(shell: str) -> None:
    """Print a shell completion script for the given shell."""
    shell = shell.lower()
    if shell == "bash":
        print(_bash_completion())
    elif shell == "zsh":
        print(_zsh_completion())
    elif shell == "fish":
        print(_fish_completion())
    else:
        ui.print_error(f'Unsupported shell "{shell}".')
        raise SystemExit(1)