"""End-to-end flow with a fake LLM and a temp vault/DB (no mic, no network)."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from voiceagent import agent
from voiceagent.config import cfg


class FakeUI:
    def __init__(self, answers):
        self.said, self.answers, self.asked = [], list(answers), []

    def say(self, t):
        self.said.append(t)

    def confirm(self, q):
        self.asked.append(q)
        return self.answers.pop(0)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "daily").mkdir()
        self.daily = root / "daily" / f"{datetime.now():%Y-%m-%d}.md"
        self.daily.write_text("# d\n\n---\n\n## Notes for Claude\n", encoding="utf-8")
        (root / "Investments.md").write_text("# Inv\n", encoding="utf-8")
        (root / "Parteek.md").write_text("I am Parteek", encoding="utf-8")
        db = root / "f.db"
        con = sqlite3.connect(db)
        con.execute("create table spending_log(item text, amount real)")
        con.execute("insert into spending_log values ('Tim Hortons', 5.63)")
        con.commit()
        con.close()
        self.root = root
        self.saved = {k: getattr(cfg, k) for k in ("vault_dir", "perfin_db_path", "data_dir")}
        for k, v in (("vault_dir", root), ("perfin_db_path", str(db)), ("data_dir", root / "sessions")):
            object.__setattr__(cfg, k, v)

    def tearDown(self):
        for k, v in self.saved.items():
            object.__setattr__(cfg, k, v)
        self.tmp.cleanup()

    def test_finance_query_and_confirmed_write(self):
        replies = iter([
            json.dumps({"say": "", "actions": [{"tool": "sql", "query": "select item, amount from spending_log"}]}),
            json.dumps({"say": "", "actions": [{"tool": "note_append", "file": "Investments.md", "text": "checked spending"}]}),
            json.dumps({"say": "You spent about six dollars at Tim Hortons.", "actions": []}),
        ])
        seen = []

        def fake_chat(messages, profile, json_mode=False):
            seen.append(messages[-1]["content"])
            return next(replies)

        ui = FakeUI([True])
        with mock.patch.object(agent.llm, "chat", fake_chat):
            s = agent.Session(ui, "finance")
            s.handle("how much did I spend at Tim Hortons")
        self.assertTrue(any("Tim Hortons,5.63" in m for m in seen))
        self.assertEqual(len(ui.asked), 1)
        self.assertIn("Investments", ui.asked[0])
        self.assertIn("checked spending", (self.root / "Investments.md").read_text(encoding="utf-8"))
        self.assertIn("Tim Hortons", ui.said[-1])

    def test_declined_write_changes_nothing(self):
        before = (self.root / "Investments.md").read_text(encoding="utf-8")
        reply = json.dumps({"say": "", "actions": [{"tool": "note_append", "file": "Investments.md", "text": "x"}]})
        ui = FakeUI([False])
        with mock.patch.object(agent.llm, "chat", lambda *a, **k: reply):
            s = agent.Session(ui, "finance")
            s.handle("save that I checked my balance")
        self.assertEqual(before, (self.root / "Investments.md").read_text(encoding="utf-8"))
        self.assertEqual(ui.said[-1], "Okay, cancelled.")

    def test_voice_switch_and_session_summary(self):
        ui = FakeUI([])
        with mock.patch.object(agent.llm, "chat", lambda *a, **k: "ok I hear you"):
            s = agent.Session(ui, "friend")
            s.handle("switch to finance mode")
            self.assertEqual(s.current, "finance")
            s.switch("friend", announce=False)
            s.handle("I feel stressed about work")
            s.finalize(propose=False)
        self.assertEqual(ui.said[0], "Switching to finance.")
        self.assertIn("Voice session (friend)", self.daily.read_text(encoding="utf-8"))

    def test_sql_write_attempt_blocked_end_to_end(self):
        replies = iter([
            json.dumps({"say": "", "actions": [{"tool": "sql", "query": "delete from spending_log"}]}),
            json.dumps({"say": "I can't change the database.", "actions": []}),
        ])
        ui = FakeUI([])
        with mock.patch.object(agent.llm, "chat", lambda *a, **k: next(replies)):
            agent.Session(ui, "finance").handle("wipe my spending")
        con = sqlite3.connect(self.root / "f.db")
        self.assertEqual(con.execute("select count(*) from spending_log").fetchone()[0], 1)
        con.close()


if __name__ == "__main__":
    unittest.main()
