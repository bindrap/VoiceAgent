import json
import re

import requests

from .config import LLMProfile


def chat(messages: list[dict], profile: LLMProfile, json_mode: bool = False) -> str:
    body = {
        "model": profile.model,
        "messages": messages,
        "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
    }
    if profile.reasoning_effort:
        body["reasoning_effort"] = profile.reasoning_effort
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = requests.post(
        f"{profile.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {profile.api_key}"},
        json=body,
        timeout=300,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"].get("content") or "").strip()


def parse_json(text: str) -> dict:
    """Tolerant JSON extraction (models sometimes wrap output in prose or fences)."""
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("plan is not an object")
    if "tool" in obj and not obj.get("actions"):  # model sent one bare action instead of the wrapper
        obj = {"say": obj.get("say", ""), "actions": [{k: v for k, v in obj.items() if k != "say"}]}
    acts = obj.get("actions")
    obj["actions"] = [a for a in acts if isinstance(a, dict)] if isinstance(acts, list) else []
    return obj
