"""Vault file access: safe paths, redaction, append-only writes."""
import fnmatch
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .config import cfg

BLOCKED_PARTS = ("_memory", ".git", ".obsidian", "repos")
BLOCKED_NAME = re.compile(
    r"(\.env|apikey|password|secret|credential|creditcardpin|sin_number|serveraccount|nanakagent)", re.I
)
SECRET_LINE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|bearer)\b(\s*[:=]\s*|\s+)(\S+)"
)
SECRET_TOKENS = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|[A-Fa-f0-9]{40,})\b")
SUMMARY_BLOCK = re.compile(r"<!-- daily-summary-start -->.*?<!-- daily-summary-end -->", re.S)


def redact(text: str) -> str:
    text = SECRET_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)
    return SECRET_TOKENS.sub("[redacted]", text)


def safe_path(rel: str, root: Path | None = None, for_context: bool = False) -> Path:
    """Resolve a vault-relative path. Raises ValueError if outside the vault or sensitive."""
    root = (root or cfg.vault_dir).resolve()
    p = (root / rel).resolve()
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        raise ValueError("path is outside the vault")
    if any(part.lower() in BLOCKED_PARTS for part in parts):
        raise ValueError("that folder is off limits")
    if not for_context and BLOCKED_NAME.search(p.name):
        raise ValueError("that file is off limits")
    if p.suffix.lower() not in (".md", ".txt"):
        raise ValueError("only .md and .txt notes")
    return p


def read_text(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def read_note(rel: str, root: Path | None = None, limit: int = 8000) -> str:
    p = safe_path(rel, root)
    if not p.exists():
        raise ValueError("file not found")
    return redact(read_text(p, limit))


def recent_daily(n: int, root: Path | None = None) -> list[tuple[str, str]]:
    root = root or cfg.vault_dir
    notes = sorted((root / cfg.daily_notes_dir).glob("20*.md"), reverse=True)[:n]
    return [(p.stem, SUMMARY_BLOCK.sub("", read_text(p, 6000))) for p in reversed(notes)]


def recent_files(rel_dir: str, n: int, root: Path | None = None) -> list[Path]:
    base = (root or cfg.vault_dir) / rel_dir
    files = [p for p in base.rglob("*.md") if "assets" not in p.parts] if base.exists() else []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:n]


SKIP_DIRS = {"assets", "node_modules", ".stfolder", ".trash"}


def safe_dir(rel: str, root: Path | None = None) -> Path:
    root = (root or cfg.vault_dir).resolve()
    base = (root / (rel or "")).resolve()
    try:
        parts = base.relative_to(root).parts
    except ValueError:
        raise ValueError("path is outside the vault")
    if any(part.lower() in BLOCKED_PARTS for part in parts):
        raise ValueError("that folder is off limits")
    if not base.is_dir():
        raise ValueError("folder not found")
    return base


def _note_files(base: Path):
    for p in base.rglob("*"):
        if p.suffix.lower() not in (".md", ".txt") or not p.is_file():
            continue
        rel_parts = {x.lower() for x in p.relative_to(base).parts}
        if rel_parts & (SKIP_DIRS | set(BLOCKED_PARTS)) or BLOCKED_NAME.search(p.name):
            continue
        yield p


def list_notes(rel_dir: str = "", n: int = 10, root: Path | None = None) -> str:
    """Newest notes in a folder. Daily notes sort by date in the filename (sync can change mtimes)."""
    root = (root or cfg.vault_dir).resolve()
    base = safe_dir(rel_dir or cfg.daily_notes_dir, root)
    files = list(_note_files(base))
    files.sort(key=lambda p: (p.stem if base.name == cfg.daily_notes_dir else p.stat().st_mtime), reverse=True)
    n = max(1, min(int(n or 10), 30))
    return "\n".join(p.relative_to(root).as_posix() for p in files[:n]) or "no notes found"


def search_notes(query: str, rel_dir: str = "", root: Path | None = None, max_hits: int = 15) -> str:
    """Case-insensitive search of vault notes, newest files first. Lines with all words, else any word."""
    root = (root or cfg.vault_dir).resolve()
    terms = [t.lower() for t in re.findall(r"\w+", query or "") if len(t) > 1]
    if not terms:
        raise ValueError("give me something to search for")
    base = safe_dir(rel_dir, root)
    files = sorted(_note_files(base), key=lambda p: p.stat().st_mtime, reverse=True)
    all_hits, any_hits = [], []
    for p in files:
        if p.stat().st_size > 500_000:
            continue
        for i, line in enumerate(read_text(p, 500_000).splitlines(), 1):
            low = line.lower()
            if not line.strip() or line.lstrip().startswith("<!--"):
                continue
            found = [t for t in terms if t in low]
            if not found:
                continue
            hit = f"{p.relative_to(root).as_posix()}:{i}: {redact(line.strip())[:160]}"
            (all_hits if len(found) == len(terms) else any_hits).append(hit)
        if len(all_hits) >= max_hits:
            break
    hits = (all_hits + any_hits)[:max_hits]
    return "\n".join(hits) or "no matches"


def is_writable(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)


def daily_path(root: Path | None = None, day: datetime | None = None) -> Path:
    day = day or datetime.now()
    return (root or cfg.vault_dir) / cfg.daily_notes_dir / f"{day:%Y-%m-%d}.md"


def _atomic_write(p: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp, p)


def append_to_daily(block: str, root: Path | None = None, now: datetime | None = None) -> Path:
    """Insert a block into today's note, just above the closing '---' before 'Notes for Claude'."""
    now = now or datetime.now()
    p = daily_path(root, now)
    if not p.exists():
        raise ValueError(f"no daily note for {now:%Y-%m-%d} yet")
    content = p.read_text(encoding="utf-8")
    block = block.strip("\n")
    m = re.search(r"^## Notes for Claude", content, flags=re.M)
    if m:
        cut = content.rfind("---", 0, m.start())
        cut = cut if cut != -1 else m.start()
        new = content[:cut].rstrip("\n") + "\n\n" + block + "\n\n" + content[cut:]
    else:
        new = content.rstrip("\n") + "\n\n" + block + "\n"
    _atomic_write(p, new)
    return p


def timestamp_heading(topic: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"### {now:%I:%M %p}".replace("### 0", "### ") + f" — {topic}"


def append_to_note(rel: str, text: str, root: Path | None = None, now: datetime | None = None) -> Path:
    """Append-only: adds a dated bullet under '## Voice updates' at the end of the file."""
    now = now or datetime.now()
    p = safe_path(rel, root)
    if not p.exists():
        raise ValueError("file not found")
    content = p.read_text(encoding="utf-8")
    if "## Voice updates" not in content:
        content = content.rstrip("\n") + "\n\n## Voice updates\n"
    bullet = f"- ({now:%Y-%m-%d}, voice) " + " ".join(text.split())
    _atomic_write(p, content.rstrip("\n") + "\n" + bullet + "\n")
    return p
