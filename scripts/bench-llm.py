#!/usr/bin/env python3
"""VoiceAgent LLM bench — same fixed prompts across every model."""
import json, re, sys, time, urllib.request, os

BASE = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api") + "/chat"

# ~3.5K-token persona-style background, mirrors what VoiceAgent sends (condensed)
SYSTEM = """You are server_ops, an assistant for running a home server (Nanak, Arch Linux, Docker).
Background notes:
- 29 containers: caddy, perfin (port 5001), immich (2283), nextcloud, jellyfin (8096), navidrome (4533), komga (25600), qbittorrent (8080), dawarich (3000), healthchecks (8000), lubelogger, home-search (8110), kiosk (5055), lidarr, mylar3, lazylibrarian, audiobookshelf, prowlarr, watchtower, ntfy, dnsmasq, influxdb (8086), 4x immich/pg/redis helpers.
- Storage: 64GB NVMe root (keep lean), 1TB SATA /mnt/storage (all docker data), 1TB SSD /mnt/backup (Borg).
- Rules: nothing that grows goes on NVMe. Don't auto-update Immich (x3.0.2 pinned). Don't reboot host casually. Borg backup Sundays 3am, deps to /mnt/backup.
- Docker: data-root /mnt/storage/lib-docker. parteek is in docker group (no sudo needed for docker).
- Cron: daily-enrich.py every 10min, backup.sh Sunday 3am, healthchecks pings after each cron.
- Known quirks: Caddyfile edits need `docker restart caddy` (single-file bind mount). qBittorrent API only from inside container. FB link issues after pacman upgrades: restart tailscaled.
TOOLS: shell(cmd) read-only allowlist (docker ps, curl, journalctl, df, ss, docker logs) or confirmed writes. read_note(path). append_note(path, text, confirmed=yes).
Output protocol: when action needed, output ONLY a JSON object: {"action": "shell", "args": {"cmd": "..."}} or {"action":"answer","args":{"text":"..."}}. No markdown, no commentary.
"""

USER_TOOL = "Check if the Immich container is running and report its status using a Docker command that filters for the container name (e.g. `docker ps -a --filter \"name=immich\"`)."
USER_SPEED = "In one short sentence, tell me how I check free disk space."

TOOLS = [{
    "type": "function",
    "function": {
        "name": "shell",
        "description": "Run a read-only shell command on the server. Use for status checks.",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string", "description": "shell command, read-only"}},
            "required": ["cmd"],
        },
    },
}]

def extract_content(msg):
    """Return final answer content, accounting for reasoning/harmony channels."""
    out = msg.get("content") or ""
    # ollama harmony-style models put final text in reasoning_content sometimes
    if not out.strip():
        rc = msg.get("reasoning_content") or ""
        # keep tail of reasoning — final answer usually there, before-tool scratch is not ideal but still text
        out = rc
    # strip qwen think tags
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
    return out

def parse_json(out):
    out = out.strip()
    if out.startswith("```"):
        out = re.sub(r"^```(?:json)?\s*", "", out)
        out = re.sub(r"\s*```$", "", out)
    # find first {...} block
    m = re.search(r"\{.*\}", out, re.DOTALL)
    return json.loads(m.group(0) if m else out)

def call(model, system, user, max_tokens, timeout=600):
    body = {
        "model": model, "temperature": 0, "seed": 42, "max_tokens": max_tokens,
        "stream": False, "tools": TOOLS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # more background bulk to make prompt-eval a factor like the real agent
    if len(system) > 500:
        body["messages"][0]["content"] += "\n" + "".join(
            "- server note %d: journal 2026-08-%02d, no errors observed, disk ok, memory ok, fan ok.\n"
            % (i, (i % 28) + 1) for i in range(120)
        )
    t0 = time.time()
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("OLLAMA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        # Cloud endpoint may stream multiple JSON objects; find the line with tool_calls
        try:
            txt = raw.decode()
            lines = [ln for ln in txt.splitlines() if ln.strip()]
            data = None
            # look for a line containing tool_calls (most informative)
            for ln in reversed(lines):
                if '"tool_calls"' in ln:
                    data = json.loads(ln)
                    break
            if data is None:
                # fallback to last line
                last = lines[-1] if lines else txt
                data = json.loads(last)
        except Exception:
            data = json.loads(raw)
    # Handle both OpenAI‑compatible (local) and cloud response shapes
    if "choices" in data:
        msg = data["choices"][0]["message"]
    else:
        msg = data.get("message", {})
    usage = data.get("usage", {})
    tc = msg.get("tool_calls") or []
    usage["_tool_calls"] = tc
    wall = time.time() - t0
    content = extract_content(msg)
    return wall, content, usage

def bench(model):
    print(f"\n===== {model} =====")
    # tool-call accuracy
    try:
        wall, out, usage = call(model, SYSTEM, USER_TOOL, 256)
        toks = usage.get("completion_tokens", 0) or 0
        ptoks = usage.get("prompt_tokens", 0) or 0
        rate = toks / wall if wall and toks else 0
        tc = usage.get("_tool_calls") or []  # stashed by call()
        ok, parsed = False, {}
        if tc:
            import json as _j
            try:
                args = tc[0]["function"]["arguments"]
                if isinstance(args, str):
                    parsed = _j.loads(args)
                else:
                    parsed = args
                ok = parsed.get("action", "shell") == "shell" and "immich" in str(parsed.get("cmd", ""))
            except Exception:
                args = tc[0]["function"]["arguments"]
                parsed = {"raw": str(args)[:200]}
        else:
            try:
                parsed = parse_json(out)
                ok = parsed.get("action") == "shell" and "immich" in str(parsed.get("args", {}).get("cmd", ""))
            except Exception:
                ok, parsed = False, {"raw": out[:200]}
        print(f"TOOL   tool_call={len(tc)}  wall={wall:5.1f}s  out_tok={toks:4d}  rate={rate:5.1f} tok/s  (prompt ~{ptoks} tok)  JSON_OK={ok}")
        print(f"       -> {json.dumps(parsed)[:160]}")
    except Exception as e:
        print(f"TOOL   FAILED: {type(e).__name__}: {e}")
    # short-prompt latency
    try:
        wall, out, usage = call(model, "Answer briefly.", USER_SPEED, 64)
        toks = usage.get("completion_tokens", 0) or 0
        print(f"SPEED  wall={wall:5.2f}s  out_tok={toks:3d}  rate={(toks/wall if wall and toks else 0):5.1f} tok/s")
    except Exception as e:
        print(f"SPEED  FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    for m in sys.argv[1:]:
        bench(m)
