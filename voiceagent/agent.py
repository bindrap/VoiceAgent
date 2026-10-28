import json
from datetime import datetime

from . import llm, memory, router, tools
from .config import cfg, check_llm_allowed, llm_profile
from .personas import build_system_prompt, load_personas


class Session:
    def __init__(self, ui, persona: str | None = None, use_memory: bool = True):
        self.ui = ui
        self.use_memory = use_memory
        self.personas = load_personas()
        self.triggers = router.load_triggers()
        self.histories: dict[str, list[dict]] = {}
        self.current = ""
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = cfg.data_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
        self.switch(persona or cfg.default_persona, announce=False)

    def _log(self, **entry) -> None:
        entry["ts"] = datetime.now().isoformat(timespec="seconds")
        entry["persona"] = self.current
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @property
    def persona(self):
        return self.personas[self.current]

    def switch(self, name: str, announce: bool = True) -> None:
        if name not in self.personas:
            raise KeyError(f"unknown persona '{name}' (have: {', '.join(self.personas)})")
        p = self.personas[name]
        check_llm_allowed(name, p.local_only, llm_profile(name))
        system = {"role": "system", "content": build_system_prompt(p)}
        hist = self.histories.setdefault(name, [system])
        hist[0] = system  # refresh context: notes may have changed since last time
        self.current = name
        if announce:
            self.ui.say(f"Switching to {name}.")
        self._log(event="switch", to=name)

    def _classify(self, text: str, current: str) -> str:
        names = "\n".join(f"- {n}: {p.description}" for n, p in self.personas.items())
        prompt = (f"Pick which assistant should handle this request. Reply with ONE word from the list, "
                  f"or '{current}' if unsure.\n{names}\n\nRequest: {text}")
        try:
            out = llm.chat([{"role": "user", "content": prompt}], llm_profile("router")).strip().lower()
        except Exception:
            return current
        return next((n for n in self.personas if n in out), current)

    def handle(self, text: str) -> None:
        decision = router.route(text, self.current, self.triggers, self.personas, self._classify)
        if decision.persona != self.current:
            try:
                self.switch(decision.persona)
            except PermissionError as e:
                self.ui.say(f"I can't switch to {decision.persona}. {e}")
                return
        text = decision.text if decision.explicit else text
        if not text:
            return
        self._trim()
        self.histories[self.current].append({"role": "user", "content": text})
        self._log(role="user", text=text)
        if self.persona.protocol == "actions":
            self._act()
        else:
            self._converse()

    def _trim(self) -> None:
        h = self.histories[self.current]
        keep = cfg.history_turns * 2
        if len(h) > keep + 1:
            self.histories[self.current] = [h[0]] + h[-keep:]

    def _converse(self) -> None:
        h = self.histories[self.current]
        reply = llm.chat(h, llm_profile(self.current))
        if not reply:
            self.ui.say("I didn't get a reply from the model. Try again.")
            return
        h.append({"role": "assistant", "content": reply})
        self._log(role="assistant", text=reply)
        self.ui.say(reply)

    def _act(self) -> None:
        """Loop: model proposes JSON {say, actions[]}; confirm-gated actions need a spoken yes."""
        h = self.histories[self.current]
        prof = llm_profile(self.current)
        for _ in range(cfg.max_action_steps):
            plan, raw = None, ""
            for attempt in range(2):
                raw = llm.chat(h, prof, json_mode=True)
                try:
                    plan = llm.parse_json(raw)
                    break
                except Exception:
                    self._log(event="bad_plan", raw=raw[:1000])
                    if attempt == 0:
                        h.append({"role": "assistant", "content": raw or "(empty)"})
                        h.append({"role": "user", "content": 'Reply with only a JSON object: {"say":"...","actions":[]}'})
            if plan is None:
                if raw.strip() and "{" not in raw:
                    plan = {"say": raw.strip(), "actions": []}  # plain prose: speak it
                else:
                    self.ui.say("I got a garbled reply from the model. Try again.")
                    return
            else:
                h.append({"role": "assistant", "content": raw})
            self._log(role="assistant", plan=plan)

            actions = plan.get("actions") or []
            if not actions:
                self.ui.say(plan.get("say") or "Done.")
                return

            results = []
            for a in actions:
                prep = tools.prepare(self.persona, a)
                if prep.error:
                    results.append({"action": a, "ok": False, "output": prep.error})
                    continue
                if prep.needs_confirm and not self.ui.confirm(f"I want to {prep.desc}"):
                    self._log(event="declined", desc=prep.desc)
                    self.ui.say("Okay, cancelled.")
                    h.append({"role": "user", "content": "[I declined that action. Don't retry it.]"})
                    return
                out = prep.run()
                self._log(event="tool", tool=prep.tool, desc=prep.desc, output=out[:500])
                results.append({"tool": prep.tool, "desc": prep.desc, "output": out})

            h.append({"role": "user", "content": "Tool results:\n" + json.dumps(results, ensure_ascii=False)
                      + "\nGive a brief spoken summary (actions: []) or propose next actions."})
        self.ui.say("I hit my step limit. Here's where I got to.")

    def finalize(self, propose: bool = True) -> None:
        """End of session: write what happened back to the notes."""
        if not self.use_memory:
            return
        for name, hist in self.histories.items():
            if len(hist) <= 1:
                continue
            p = self.personas[name]
            try:
                if memory.summarize_to_daily(p, hist):
                    print(f"[memory] saved {name} session summary to today's note")
                if propose:
                    n = memory.propose_updates(p, hist, self.ui)
                    if n:
                        print(f"[memory] applied {n} note update(s) for {name}")
            except Exception as e:
                print(f"[memory] skipped for {name}: {e}")
