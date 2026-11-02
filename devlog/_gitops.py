"""Git integration for devlog.

Provides:
- `devlog add --from-commit` - prefill message/tags from a git commit
- `devlog git sync` - sync journal with a git repo (auto-commit)
- `devlog git status` - show git status of journal directory
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from devlog import _tagops
from devlog.models import Entry


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command and return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"
    except FileNotFoundError:
        return -1, "", "git not found in PATH"
    except OSError as exc:
        return -1, "", str(exc)


def is_git_repo(path: Path) -> bool:
    """Check if a directory is a git repository."""
    return (path / ".git").exists()


def get_git_repo_root(path: Path) -> Path | None:
    """Find the root of the git repository containing path."""
    exit_code, stdout, _ = _run_git(["rev-parse", "--show-toplevel"], path)
    if exit_code == 0 and stdout:
        return Path(stdout)
    return None


def get_commit_info(commit: str, repo_root: Path) -> dict | None:
    """Get information about a git commit."""
    # Get commit message
    exit_code, message, _stderr = _run_git(
        ["show", "-s", "--format=%B", commit], repo_root
    )
    if exit_code != 0:
        return None
    
    # Get commit author date
    exit_code, author_date, _ = _run_git(
        ["show", "-s", "--format=%aI", commit], repo_root
    )
    
    # Get commit hash (short)
    exit_code, short_hash, _ = _run_git(
        ["show", "-s", "--format=%h", commit], repo_root
    )
    
    # Get commit subject (first line)
    subject = message.split("\n")[0] if message else ""
    
    # Try to extract tags from commit message (e.g., "feat(auth): add login")
    tags = _extract_tags_from_commit(message)
    
    return {
        "message": message.strip(),
        "subject": subject.strip(),
        "author_date": author_date.strip() if author_date else None,
        "short_hash": short_hash.strip() if short_hash else None,
        "tags": tags,
    }


def _extract_tags_from_commit(message: str) -> list[str]:
    """Extract potential tags from a commit message.
    
    Looks for patterns like:
    - feat(auth): ... -> tag "auth"
    - fix(api): ... -> tag "api" 
    - chore(docs): ... -> tag "docs"
    - refactor(core): ... -> tag "core"
    - test(backend): ... -> tag "backend"
    """
    import re
    tags = []
    
    # Common conventional commit prefixes
    patterns = [
        r"^(feat|fix|chore|refactor|test|docs|style|perf|build|ci|revert)\(([^)]+)\):",
        r"^\[([a-z0-9-]+)\]",  # [tag] prefix
    ]
    
    first_line = message.split("\n")[0] if message else ""
    
    for pattern in patterns:
        matches = re.findall(pattern, first_line)
        for match in matches:
            if isinstance(match, tuple):
                # (type, scope) format
                tags.append(match[1].lower())
            else:
                # [tag] format
                tags.append(match.lower())
    
    # Deduplicate and validate
    valid_tags = []
    for tag in tags:
        tag = tag.strip().lower()
        if tag and _tagops._is_valid_tag(tag) and tag not in valid_tags:
            valid_tags.append(tag)
    
    return valid_tags


def create_entry_from_commit(commit: str, repo_root: Path) -> Entry | None:
    """Create an Entry from a git commit."""
    info = get_commit_info(commit, repo_root)
    if not info:
        return None
    
    # Parse author date if available
    created_at = None
    if info["author_date"]:
        try:
            # ISO format with timezone — normalise to UTC wall time
            # before stamping, so a +08 author date doesn't get
            # relabelled "Z" with its local wall clock.
            dt = datetime.fromisoformat(info["author_date"].replace("Z", "+00:00"))
            created_at = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    
    if not created_at:
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Use subject as message, or full message if short
    message = info["subject"]
    if len(message) < 10 and info["message"]:
        message = info["message"].strip()
    
    # Add commit hash as a tag
    tags = info["tags"][:]
    if info["short_hash"]:
        tags.append(f"commit-{info['short_hash']}")
    
    return Entry(
        id="",  # Will be filled by caller
        message=message,
        tags=tags,
        created_at=created_at,
    )


def auto_commit_journal(data_dir: Path, message: str = "devlog: journal update") -> tuple[bool, str]:
    """Auto-commit the journal directory if it's a git repo."""
    if not is_git_repo(data_dir):
        return False, "Not a git repository"
    
    # Check if there are changes
    exit_code, stdout, _ = _run_git(["status", "--porcelain"], data_dir)
    if exit_code != 0:
        return False, f"git status failed: {stdout}"
    
    if not stdout.strip():
        return True, "No changes to commit"
    
    # Add all changes
    exit_code, _, stderr = _run_git(["add", "-A"], data_dir)
    if exit_code != 0:
        return False, f"git add failed: {stderr}"
    
    # Commit
    exit_code, _, stderr = _run_git(["commit", "-m", message], data_dir)
    if exit_code != 0:
        return False, f"git commit failed: {stderr}"
    
    return True, "Committed successfully"


def _existing_ancestor(path: Path) -> Path:
    """Return *path* or its nearest existing ancestor directory."""
    p = path
    while not p.exists():
        parent = p.parent
        if parent == p:
            break
        p = parent
    return p


def git_status(data_dir: Path) -> dict:
    """Get git status of the journal directory."""
    result = {
        "is_git_repo": False,
        "repo_root": None,
        "clean": True,
        "changes": [],
        "branch": None,
        "remote": None,
    }
    
    probe_dir = _existing_ancestor(data_dir)
    repo_root = get_git_repo_root(probe_dir)
    if repo_root is None:
        return result
    
    result["is_git_repo"] = True
    result["repo_root"] = str(repo_root)
    
    # Get status
    exit_code, stdout, _ = _run_git(["status", "--porcelain"], repo_root)
    if exit_code == 0 and stdout.strip():
        result["clean"] = False
        result["changes"] = stdout.strip().split("\n")
    
    # Get current branch
    exit_code, stdout, _ = _run_git(["branch", "--show-current"], repo_root)
    if exit_code == 0:
        result["branch"] = stdout.strip()
    
    # Get remote
    exit_code, stdout, _ = _run_git(["remote", "get-url", "origin"], repo_root)
    if exit_code == 0:
        result["remote"] = stdout.strip()
    
    return result