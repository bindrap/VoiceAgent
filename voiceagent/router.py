"""Decide which persona should handle an utterance.

Order: explicit call ("hey finance", "switch to work", "finance mode")
       -> keyword/pattern scoring (config/triggers.json) -> optional LLM classify when ambiguous
       -> otherwise stay on the current persona.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, cfg


@dataclass
class Decision:
    persona: str
    text: str = ""          # utterance with the call phrase removed
    explicit: bool = False
    reason: str = ""


def load_triggers(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "config" / "triggers.json").read_text(encoding="utf-8"))


def _resolve(name: str, triggers: dict) -> str | None:
    name = name.lower().strip().replace(" ", "_")
    for persona, spec in triggers.items():
        if name == persona or name in [a.lower() for a in spec.get("aliases", [])]:
            return persona
    return None


SWITCH = re.compile(
    r"^\W*(?:(?:please|ok|okay|hey|yo)[,\s]+)*(?:switch|change|go|swap)\s+(?:over\s+)?(?:to|into)\s+"
    r"(?:the\s+)?(?P<name>[a-z]+(?:[ _]ops)?)\b(?:\s+(?:mode|agent|bot|persona)\b)?[,.!?:\s]*(?P<rest>.*)$", re.I)
CALL = re.compile(r"^\W*(?:hey|yo|ok|okay)[,\s]+(?P<name>[a-z]+)\b[,.!?:\s]*(?P<rest>.*)$", re.I)
MODE = re.compile(r"^\W*(?P<name>[a-z]+)\s+(?:mode|agent|bot)\b[,.!?:\s]*(?P<rest>.*)$", re.I)


def explicit_call(text: str, triggers: dict) -> Decision | None:
    for rx in (SWITCH, CALL, MODE):
        m = rx.match(text)
        if not m:
            continue
        name = m.group("name").strip()
        persona = _resolve(name, triggers) or (_resolve(name.split()[0], triggers) if name else None)
        if persona:
            return Decision(persona, m.group("rest").strip(), True, f"called '{name}'")
    return None


def score(text: str, spec: dict) -> int:
    low = text.lower()
    s = sum(1 for kw in spec.get("keywords", []) if re.search(rf"\b{re.escape(kw.lower())}\b", low))
    s += 2 * sum(1 for pat in spec.get("patterns", []) if re.search(pat, text, re.I))
    return s


def scores(text: str, triggers: dict) -> dict[str, int]:
    return {p: score(text, spec) for p, spec in triggers.items()}


def route(text: str, current: str, triggers: dict, personas: dict, classify=None) -> Decision:
    call = explicit_call(text, triggers)
    if call and call.persona in personas:
        return call

    sc = scores(text, triggers)
    best = max(sc, key=sc.get)
    if sc[best] >= cfg.router_min_score and best != current and sc[best] > sc.get(current, 0):
        return Decision(best, text, False, f"keywords {sc[best]}")

    ambiguous = 0 < sc[best] < cfg.router_min_score and best != current
    if ambiguous and cfg.router_use_llm and classify:
        picked = classify(text, current)
        if picked in personas and picked != current:
            return Decision(picked, text, False, "llm classify")
    return Decision(current, text, False, "stay")
