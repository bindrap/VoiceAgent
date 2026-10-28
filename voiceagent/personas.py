import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import vault
from .config import cfg, ROOT
from .tools import tool_docs

PROMPTS = ROOT / "prompts"


@dataclass
class Persona:
    name: str
    description: str
    prompt: str
    protocol: str = "chat"            # chat | actions
    local_only: bool = True
    context_files: list = field(default_factory=list)
    nanak_only_files: list = field(default_factory=list)
    recent_daily: int = 0
    recent_dirs: list = field(default_factory=list)
    writable: list = field(default_factory=list)
    tools: list = field(default_factory=list)


def load_personas(path: Path | None = None) -> dict[str, Persona]:
    raw = json.loads((path or ROOT / "config" / "personas.json").read_text(encoding="utf-8"))
    return {name: Persona(name=name, **spec) for name, spec in raw.items()}


def _prompt(name: str) -> str:
    try:
        return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_system_prompt(p: Persona, root: Path | None = None) -> str:
    root = root or cfg.vault_dir
    parts = [
        _prompt("base"),
        f"Today is {datetime.now():%A, %B %d, %Y}. Running on host: {cfg.host}.",
        _prompt(p.prompt),
    ]
    if p.protocol == "actions":
        parts.append(_prompt("_actions").replace("{tools}", tool_docs(p.tools)))

    files = list(p.context_files)
    if cfg.host == "nanak":
        files += p.nanak_only_files

    total = cfg.context_total_chars
    static_budget, recent_budget = int(total * 0.6), total - int(total * 0.6)
    per_file = max(1000, min(cfg.context_max_chars, static_budget // max(1, len(files))))
    ctx = []
    for rel in files:
        try:
            path = vault.safe_path(rel, root, for_context=True)
        except ValueError:
            continue
        text = vault.redact(vault.read_text(path, per_file))
        if text.strip():
            ctx.append(f"### File: {rel}\n{text}")

    recent = []
    if p.recent_daily:
        for stem, text in vault.recent_daily(p.recent_daily, root):
            recent.append((f"### Daily note {stem}", text))
    for d in p.recent_dirs:
        for f in vault.recent_files(d["dir"], d.get("n", 5), root):
            recent.append((f"### Recent: {f.relative_to(root).as_posix()}", vault.redact(vault.read_text(f, 3000))))
    per_recent = max(500, recent_budget // max(1, len(recent)))
    # newest daily notes are last in the list; keep the tail of each so the latest entries survive
    ctx += [f"{h}\n{t[-per_recent:]}" for h, t in recent if t.strip()]

    if ctx:
        parts.append("# Background (from my notes — treat as what you already know)\n\n" + "\n\n".join(ctx))
    return "\n\n".join(x for x in parts if x)
