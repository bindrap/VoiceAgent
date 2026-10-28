import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from voiceagent import router, tools, vault
from voiceagent.config import LLMProfile, check_llm_allowed, is_local_url
from voiceagent.personas import build_system_prompt, load_personas
from voiceagent.stt import clean_transcript
from voiceagent.tts import clean_for_speech
from voiceagent.ui import parse_yes_no

PERSONAS = load_personas()
TRIGGERS = router.load_triggers()


def route(text, current="friend"):
    return router.route(text, current, TRIGGERS, PERSONAS)


class RouterTests(unittest.TestCase):
    def test_explicit_switch(self):
        d = route("switch to finance mode")
        self.assertEqual((d.persona, d.explicit, d.text), ("finance", True, ""))

    def test_hey_call_with_rest(self):
        d = route("hey nanak, is jellyfin running?")
        self.assertEqual((d.persona, d.text), ("server_ops", "is jellyfin running?"))

    def test_mode_phrase(self):
        self.assertEqual(route("coach mode").persona, "coach")

    def test_hey_generic_does_not_switch(self):
        self.assertEqual(route("hey what's up").persona, "friend")

    def test_keywords(self):
        self.assertEqual(route("how is my tfsa and portfolio doing").persona, "finance")
        self.assertEqual(route("what's the status on STRY0010572").persona, "work")
        self.assertEqual(route("check the docker containers on the server").persona, "server_ops")

    def test_sticky_when_no_signal(self):
        self.assertEqual(route("yeah that makes sense", "finance").persona, "finance")


class SqlGuardTests(unittest.TestCase):
    def test_allows_select(self):
        self.assertEqual(tools.validate_sql("SELECT * FROM spending_log;"), "SELECT * FROM spending_log")
        tools.validate_sql("with x as (select 1) select * from x")
        tools.validate_sql("select update_date from t")

    def test_blocks_writes(self):
        for q in ("DELETE FROM t", "select 1; drop table t", "update t set a=1",
                  "with x as (select 1) insert into t select * from x", "pragma writable_schema=1",
                  "select 1 union select 2; attach database 'x' as y"):
            with self.assertRaises(ValueError, msg=q):
                tools.validate_sql(q)


class ToolGateTests(unittest.TestCase):
    def setUp(self):
        self.finance = PERSONAS["finance"]
        self.ops = PERSONAS["server_ops"]

    def test_readonly_vs_confirm(self):
        self.assertTrue(tools.is_readonly("docker ps"))
        self.assertFalse(tools.is_readonly("docker ps && rm x"))
        self.assertFalse(tools.is_readonly("cat /etc/passwd > /tmp/x"))
        self.assertFalse(tools.is_readonly("docker restart caddy"))

    def test_shell_flags(self):
        self.assertFalse(tools.prepare(self.ops, {"tool": "shell", "cmd": "docker ps"}).needs_confirm)
        self.assertTrue(tools.prepare(self.ops, {"tool": "shell", "cmd": "docker restart caddy"}).needs_confirm)
        self.assertTrue(tools.prepare(self.ops, {"tool": "shell", "cmd": "rm -rf /"}).error)

    def test_tool_not_in_persona(self):
        self.assertTrue(tools.prepare(self.finance, {"tool": "shell", "cmd": "ls"}).error)
        self.assertTrue(tools.prepare(PERSONAS["friend"], {"tool": "sql", "query": "select 1"}).error)

    def test_sql_via_finance(self):
        self.assertFalse(tools.prepare(self.finance, {"tool": "sql", "query": "select 1"}).error)
        self.assertTrue(tools.prepare(self.finance, {"tool": "sql", "query": "delete from t"}).error)

    def test_note_append_rules(self):
        self.assertTrue(tools.prepare(self.finance, {"tool": "note_append", "file": "Parteek/Health.md", "text": "x"}).error)
        self.assertTrue(tools.prepare(self.finance, {"tool": "note_append", "file": "../../etc/x.md", "text": "x"}).error)
        self.assertTrue(tools.prepare(self.finance, {"tool": "note_append", "file": "Investments.md", "text": "x"}).needs_confirm)
        self.assertFalse(tools.prepare(PERSONAS["notes"], {"tool": "note_append", "file": "daily", "text": "x"}).needs_confirm)

    def test_read_note_blocks_secrets(self):
        self.assertTrue(tools.prepare(self.ops, {"tool": "read_note", "file": "Nanak/NanakAgent.md"}).error)
        self.assertTrue(tools.prepare(self.ops, {"tool": "read_note", "file": "Nanak/IMMICH_APIKEY.md"}).error)
        self.assertTrue(tools.prepare(self.ops, {"tool": "read_note", "file": "_memory/MEMORY.md"}).error)


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "daily").mkdir()
        self.now = datetime(2026, 10, 26, 13, 40)
        self.daily = self.root / "daily" / "2026-10-26.md"
        self.daily.write_text("# 2026-10-26\n\n## Notes\n\n### 1:06 PM — x\n\n---\n\n## Notes for Claude\n\n- ctx\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_insert_position(self):
        vault.append_to_daily("### 1:40 PM — Voice\nhello", self.root, self.now)
        text = self.daily.read_text(encoding="utf-8")
        self.assertLess(text.index("hello"), text.index("## Notes for Claude"))
        self.assertLess(text.index("### 1:06 PM"), text.index("hello"))
        self.assertIn("---\n\n## Notes for Claude", text)

    def test_daily_missing(self):
        with self.assertRaises(ValueError):
            vault.append_to_daily("x", self.root, datetime(2026, 1, 1))

    def test_append_to_note_is_append_only(self):
        note = self.root / "Inv.md"
        note.write_text("# Inv\nkeep me\n", encoding="utf-8")
        vault.append_to_note("Inv.md", "bought more", self.root, self.now)
        vault.append_to_note("Inv.md", "sold some", self.root, self.now)
        text = note.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Inv\nkeep me\n"))
        self.assertEqual(text.count("## Voice updates"), 1)
        self.assertIn("- (2026-10-26, voice) bought more\n- (2026-10-26, voice) sold some\n", text)

    def test_safe_path(self):
        with self.assertRaises(ValueError):
            vault.safe_path("../x.md", self.root)
        with self.assertRaises(ValueError):
            vault.safe_path("_memory/a.md", self.root)
        with self.assertRaises(ValueError):
            vault.safe_path("a.py", self.root)

    def test_redact(self):
        out = vault.redact("api_key: abc123xyz\nnormal line\npassword=hunter2 and Bearer abcdef")
        self.assertNotIn("abc123xyz", out)
        self.assertNotIn("hunter2", out)
        self.assertIn("normal line", out)

    def test_is_writable(self):
        self.assertTrue(vault.is_writable("Work/ServiceNow/STRY1/notes.md", ["Work/ServiceNow/*/*.md"]))
        self.assertFalse(vault.is_writable("Parteek.md", ["Investments.md"]))

    def test_list_and_search_notes(self):
        (self.root / "daily" / "2026-10-27.md").write_text("met Gautham about TASK0109467\n", encoding="utf-8")
        (self.root / "_memory").mkdir()
        (self.root / "_memory" / "x.md").write_text("TASK0109467 secret\n", encoding="utf-8")
        listing = vault.list_notes("daily", 5, self.root).splitlines()
        self.assertEqual(listing[0], "daily/2026-10-27.md")
        hits = vault.search_notes("TASK0109467", root=self.root)
        self.assertIn("daily/2026-10-27.md:1", hits)
        self.assertNotIn("_memory", hits)
        with self.assertRaises(ValueError):
            vault.list_notes("../", 5, self.root)

    def test_system_prompt_loads_context(self):
        (self.root / "Parteek.md").write_text("I am Parteek. token: sk-abcdefghijklmnopqrstuv", encoding="utf-8")
        prompt = build_system_prompt(PERSONAS["friend"], self.root)
        self.assertIn("I am Parteek", prompt)
        self.assertNotIn("sk-abcdefghijklmnopqrstuv", prompt)
        self.assertIn("friend", prompt)


class MiscTests(unittest.TestCase):
    def test_parse_json_fences(self):
        from voiceagent import llm
        self.assertEqual(llm.parse_json('```json\n{"say":"hi","actions":[]}\n```')["say"], "hi")
        flat = llm.parse_json('{"tool":"list_notes","dir":"daily","n":3}')
        self.assertEqual(flat["actions"], [{"tool": "list_notes", "dir": "daily", "n": 3}])
        with self.assertRaises(Exception):
            llm.parse_json("just words")
    def test_yes_no(self):
        self.assertTrue(parse_yes_no("yeah go ahead"))
        self.assertFalse(parse_yes_no("no wait"))
        self.assertFalse(parse_yes_no("yes... actually no"))
        self.assertIsNone(parse_yes_no("hmm what"))

    def test_clean_for_speech(self):
        out = clean_for_speech("## Title\n- **bold** `code` [link](http://x)")
        for bad in ("#", "*", "`", "http"):
            self.assertNotIn(bad, out)

    def test_clean_for_speech_keeps_words_intact(self):
        text = "Hello there, this is a well-known test."
        self.assertEqual(clean_for_speech(text), text)
        self.assertEqual(clean_for_speech("```\nx = 1\n```\nDone."), "Done.")

    def test_clean_transcript(self):
        self.assertEqual(clean_transcript(" [BLANK_AUDIO] "), "")
        self.assertEqual(clean_transcript("Thank you."), "")
        self.assertEqual(clean_transcript("check (music) docker"), "check docker")

    def test_local_url_guard(self):
        self.assertTrue(is_local_url("http://127.0.0.1:11434/v1"))
        self.assertTrue(is_local_url("http://192.168.1.5:11434"))
        self.assertTrue(is_local_url("http://100.101.1.1:11434"))
        self.assertFalse(is_local_url("https://ollama.com/v1"))
        prof = LLMProfile("https://ollama.com/v1", "k", "m", 0.6, 100)
        with self.assertRaises(PermissionError):
            check_llm_allowed("friend", True, prof)


if __name__ == "__main__":
    unittest.main()
