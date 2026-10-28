"""Session memory = notes. Summaries append to the daily note; durable updates are proposed + confirmed."""
import json
from datetime import datetime

from . import llm, vault
from .config import cfg, llm_profile

SUMMARY_PROMPT = (
    "Write a short note about this voice session, first person as Parteek, casual, 2-5 short lines or "
    "checkbox tasks. Capture decisions, state changes, open items. No fluff. Plain Markdown, no heading."
)
PROPOSE_PROMPT = (
    "From this voice session, propose up to 3 durable facts worth saving to my notes (a changed status, "
    "a new decision, a balance, an open task). Respond ONLY with JSON: "
    '{{"updates":[{{"file":"<one of: {files}>","text":"<one line, first person>"}}]}}. '
    'If nothing is worth saving, return {{"updates":[]}}.'
)


def _transcript(history: list[dict]) -> str:
    lines = []
    for m in history:
        if m["role"] == "user" and not m["content"].startswith(("Tool results", "[")):
            lines.append(f"Me: {m['content']}")
        elif m["role"] == "assistant" and not m["content"].lstrip().startswith("{"):
            lines.append(f"Assistant: {m['content']}")
    return "\n".join(lines)[-8000:]


def summarize_to_daily(persona, history: list[dict], now: datetime | None = None) -> str | None:
    """Append a session summary to today's daily note. Returns the text written."""
    transcript = _transcript(history)
    if not cfg.summary_to_daily or not transcript.strip():
        return None
    summary = llm.chat(
        [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": transcript}],
        llm_profile(persona.name),
    ).strip()
    block = f"{vault.timestamp_heading(f'Voice session ({persona.name})', now)}\n{summary}"
    vault.append_to_daily(block, now=now)
    return summary


def propose_updates(persona, history: list[dict], ui) -> int:
    """LLM proposes durable updates; each is read back and applied only on a spoken yes."""
    if not cfg.propose_note_updates or not persona.writable:
        return 0
    transcript = _transcript(history)
    if not transcript.strip():
        return 0
    files = ", ".join(f for f in persona.writable if "*" not in f) or "none"
    if files == "none":
        return 0
    raw = llm.chat(
        [{"role": "system", "content": PROPOSE_PROMPT.format(files=files)},
         {"role": "user", "content": transcript}],
        llm_profile(persona.name), json_mode=True,
    )
    try:
        updates = llm.parse_json(raw).get("updates", [])[:3]
    except Exception:
        return 0
    applied = 0
    for u in updates:
        rel, text = u.get("file", ""), (u.get("text") or "").strip()
        if not text or not vault.is_writable(rel, persona.writable):
            continue
        if ui.confirm(f"Save to {rel}: {text}"):
            vault.append_to_note(rel, text)
            applied += 1
    return applied
