"""Tools the model can request. Each action is validated, then either runs or waits for a voice confirm."""
import csv
import io
import re
import shlex
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import vault
from .config import cfg

TOOL_DOCS = {
    "shell": '{"tool":"shell","cmd":"<one simple command>","reason":"<why>"} — run on Nanak. '
             "Read-only commands run immediately; anything else needs my spoken confirmation.",
    "sql": '{"tool":"sql","query":"<single SELECT>"} — read-only query on the Perfin finance database '
           "(tables like spending_log, personal_log, gym_log). Start with a sqlite_master query if unsure of the schema.",
    "read_note": '{"tool":"read_note","file":"<vault-relative .md path>"} — read one of my notes in full.',
    "list_notes": '{"tool":"list_notes","dir":"<vault folder, default daily>","n":<how many, default 10>} — '
                  "newest notes in a folder (e.g. daily, gym, jits/journal, Work/AMANDA/TASK).",
    "search_notes": '{"tool":"search_notes","query":"<words>","dir":"<optional folder>"} — find lines across my notes, newest first.',
    "note_append": '{"tool":"note_append","file":"<vault path or \\"daily\\">","text":"<one or two lines in my voice>"} '
                   "— append only. The daily note is immediate; other files need my spoken confirmation.",
}

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|reindex|load_extension)\b", re.I
)
CHAIN_TOKENS = (";", "&&", "||", "|", ">", "<", "`", "$(")


def tool_docs(names: list[str]) -> str:
    return "\n".join(f"- {TOOL_DOCS[n]}" for n in names if n in TOOL_DOCS)


@dataclass
class Prepared:
    tool: str
    desc: str
    needs_confirm: bool = False
    run: Callable[[], str] = field(default=lambda: "")
    error: str = ""


def is_denied(cmd: str) -> bool:
    low = cmd.lower()
    return any(d.lower() in low for d in cfg.command_denylist)


def is_readonly(cmd: str) -> bool:
    """Allowlist prefix match; chained/redirected commands never count as read-only."""
    if any(tok in cmd for tok in CHAIN_TOKENS):
        return False
    return any(cmd.strip().startswith(p) for p in cfg.readonly_allowlist)


def validate_sql(query: str) -> str:
    q = query.strip().rstrip(";").strip()
    if ";" in q:
        raise ValueError("one statement only")
    if not re.match(r"(?is)^(select|with)\b", q):
        raise ValueError("SELECT only")
    if FORBIDDEN_SQL.search(q):
        raise ValueError("write keywords aren't allowed")
    return q


def _ssh_args(remote_cmd: str) -> list[str]:
    target = f"{cfg.ssh_user}@{cfg.ssh_host}" if cfg.ssh_user else cfg.ssh_host
    args = ["ssh", "-p", cfg.ssh_port, "-o", "BatchMode=yes"]
    if cfg.ssh_key:
        args += ["-i", cfg.ssh_key]
    return args + [target, remote_cmd]


def run_shell(cmd: str, timeout: int = 60) -> str:
    try:
        if cfg.host == "nanak":
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        elif cfg.ssh_host:
            p = subprocess.run(_ssh_args(cmd), capture_output=True, text=True, timeout=timeout)
        else:
            return "error: NANAK_SSH_HOST not set"
    except subprocess.TimeoutExpired:
        return "error: timed out"
    out = vault.redact((p.stdout + p.stderr).strip())[:4000]
    return out if p.returncode == 0 else f"exit {p.returncode}: {out}"


def run_sql(query: str) -> str:
    q = validate_sql(query)
    local = Path(cfg.perfin_db_path) if cfg.perfin_db_path else None
    if local and local.exists():
        con = sqlite3.connect(f"{local.resolve().as_uri()}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            cur = con.execute(q)
            rows = cur.fetchmany(cfg.perfin_max_rows)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([d[0] for d in cur.description or []])
            w.writerows(rows)
            return buf.getvalue()[:4000]
        finally:
            con.close()
    if cfg.ssh_host:
        remote = f"sqlite3 -readonly -header -csv {shlex.quote(cfg.perfin_remote_db_path)}"
        p = subprocess.run(_ssh_args(remote), input=f"PRAGMA query_only=ON;\n{q};\n",
                           capture_output=True, text=True, timeout=60)
        out = (p.stdout + p.stderr).strip()
        return out[:4000] if p.returncode == 0 else f"error: {out[:500]}"
    return "error: the Perfin database isn't available here. Fall back to Investments.md and the daily notes' portfolio lines (marked as possibly stale) and tell me that's what you used."


def prepare(persona, action: dict) -> Prepared:
    """Validate one model-requested action against the persona's allowed tools."""
    tool = action.get("tool") or ("shell" if action.get("cmd") else "")
    if tool not in persona.tools:
        return Prepared(tool, "", error=f"tool '{tool}' isn't available in {persona.name} mode")

    try:
        if tool == "shell":
            cmd = (action.get("cmd") or "").strip()
            if not cmd:
                return Prepared(tool, "", error="empty command")
            if is_denied(cmd):
                return Prepared(tool, cmd, error="blocked by denylist")
            why = action.get("reason", "")
            return Prepared(tool, f"run {cmd}. {why}".strip(), not is_readonly(cmd), lambda: run_shell(cmd))

        if tool == "sql":
            q = validate_sql(action.get("query", ""))
            return Prepared(tool, f"query: {q}", False, lambda: run_sql(q))

        if tool == "read_note":
            rel = action.get("file", "")
            vault.safe_path(rel)
            return Prepared(tool, f"read {rel}", False, lambda: vault.read_note(rel))

        if tool == "list_notes":
            d, n = action.get("dir") or "", action.get("n") or 10
            vault.safe_dir(d or cfg.daily_notes_dir)
            return Prepared(tool, f"list notes in {d or cfg.daily_notes_dir}", False, lambda: vault.list_notes(d, n))

        if tool == "search_notes":
            q, d = action.get("query") or "", action.get("dir") or ""
            vault.safe_dir(d)
            return Prepared(tool, f"search notes for {q}", False, lambda: vault.search_notes(q, d))

        if tool == "note_append":
            rel, text = action.get("file", ""), (action.get("text") or "").strip()
            if not text:
                return Prepared(tool, "", error="nothing to add")
            if rel.lower() in ("daily", "today"):
                def go() -> str:
                    block = f"{vault.timestamp_heading('voice note')}\n{text}"
                    return f"added to {vault.append_to_daily(block).name}"
                return Prepared(tool, f"add to today's note: {text}", False, go)
            vault.safe_path(rel)
            if not vault.is_writable(rel, persona.writable):
                return Prepared(tool, "", error=f"{rel} isn't writable in {persona.name} mode")
            return Prepared(tool, f"add this to {Path(rel).stem}: {text}", True,
                            lambda: f"added to {vault.append_to_note(rel, text).name}")
    except ValueError as e:
        return Prepared(tool, "", error=str(e))
    return Prepared(tool, "", error="unknown tool")
