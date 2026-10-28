"""Web API flow: the confirm gate pauses a turn and the browser's answer resumes the same turn."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
    from voiceagent import agent, web
except (ImportError, RuntimeError):  # fastapi/httpx not installed (starlette raises RuntimeError without httpx)
    TestClient = None

from voiceagent.config import cfg


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class WebFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "daily").mkdir()
        (root / "Investments.md").write_text("# Inv\n", encoding="utf-8")
        (root / "Parteek.md").write_text("I am Parteek", encoding="utf-8")
        self.root = root
        self.saved = {k: getattr(cfg, k) for k in ("vault_dir", "data_dir")}
        object.__setattr__(cfg, "vault_dir", root)
        object.__setattr__(cfg, "data_dir", root / "sessions")
        self.client = TestClient(web.app, base_url="http://localhost")
        self.headers = {"Authorization": f"Bearer {cfg.web_token or 'NONE'}", "X-Session-Id": "t1"}
        web.sessions.clear()
        patches = [
            mock.patch.object(web.tts, "synthesize_wav_bytes", lambda t: b""),
            mock.patch.object(web.stt, "transcribe", lambda b: "save that I checked my balance"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        for k, v in self.saved.items():
            object.__setattr__(cfg, k, v)
        self.tmp.cleanup()

    def _plan_replies(self):
        return iter([
            json.dumps({"say": "", "actions": [{"tool": "note_append", "file": "Investments.md", "text": "checked"}]}),
            json.dumps({"say": "Saved it.", "actions": []}),
        ])

    def _start_finance(self):
        r = self.client.post("/api/persona", json={"name": "finance"}, headers=self.headers)
        self.assertEqual(r.status_code, 200)

    def test_confirm_yes_resumes_same_turn(self):
        replies = self._plan_replies()
        with mock.patch.object(agent.llm, "chat", lambda *a, **k: next(replies)):
            self._start_finance()
            r = self.client.post("/api/text", json={"text": "save that I checked my balance"}, headers=self.headers).json()
            self.assertEqual(r["status"], "confirm_required")
            self.assertIn("Investments", r["confirm"]["prompt"])
            self.assertNotIn("checked", (self.root / "Investments.md").read_text(encoding="utf-8"))
            r = self.client.post("/api/confirm", json={"answer": "yes"}, headers=self.headers).json()
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["reply_text"], "Saved it.")
        self.assertIn("checked", (self.root / "Investments.md").read_text(encoding="utf-8"))

    def test_spoken_no_cancels(self):
        replies = self._plan_replies()
        with mock.patch.object(agent.llm, "chat", lambda *a, **k: next(replies)):
            self._start_finance()
            self.client.post("/api/text", json={"text": "save it"}, headers=self.headers)
            r = self.client.post("/api/text", json={"text": "no cancel that"}, headers=self.headers).json()
        self.assertEqual(r["reply_text"], "Okay, cancelled.")
        self.assertNotIn("checked", (self.root / "Investments.md").read_text(encoding="utf-8"))

    def test_rejects_bad_token_and_host(self):
        bad = {**self.headers, "Authorization": "Bearer wrong"}
        self.assertEqual(self.client.get("/api/health", headers=bad).status_code, 401)
        other = TestClient(web.app, base_url="http://evil.example")
        self.assertEqual(other.get("/api/health", headers=self.headers).status_code, 403)


if __name__ == "__main__":
    unittest.main()
