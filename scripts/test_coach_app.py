#!/usr/bin/env python3
"""Behaviour tests for the coach app, driven over real HTTP with the model stubbed.

    python3 scripts/test_coach_app.py

One seam: the HTTP boundary. Generation and embedding are replaced with deterministic
stubs, so these run with no Ollama, no knowledge pack, and no network. What's tested is
the machinery around the model - routing, check-in mode, citation enforcement, log
writes - because that's what a user actually touches.

ponytail: assert-based, no framework, matching the --selftest convention already in
scripts/. A runner would be more ceremony than these checks are worth.
"""
import json
import sys
import tempfile
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coach_app as app

REAL_TITLE = "How To Train For Pure Muscle Growth"


class FakePack:
    """Stands in for the embedded knowledge pack: fixed sources, known allowlist."""
    entries = [{"topic": "effort", "question": "how close to failure?",
                "answer": "Stop short of failure.",
                "sources": [{"n": 1, "title": REAL_TITLE,
                             "cited_text": "Stopping one to three reps short produces "
                                           "nearly identical growth with less fatigue."}]}]
    titles = {REAL_TITLE}

    def __init__(self):
        self.queries = []

    def search(self, query, k=4):
        self.queries.append(query)
        return self.entries


def start_server(reply, tmp):
    """Boot the real handler on an ephemeral port with generation stubbed."""
    app.LOG = Path(tmp) / "weekly-log.md"
    app.PROTOCOL = Path(tmp) / "protocol.md"
    pack = FakePack()
    app.Handler.pack = pack
    app.Handler.history = []
    sent = {}

    def fake_generate(messages):
        sent["messages"] = messages
        return reply

    app.generate = fake_generate
    app.training_summary = lambda: "Sessions: 3\nWorking sets: 44"

    srv = HTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}", sent, pack


def call(url, path, payload=None):
    if payload is None:
        with urllib.request.urlopen(url + path, timeout=10) as r:
            return r.status, json.loads(r.read())
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def test_real_citation_survives(tmp):
    srv, url, _, _ = start_server(f"Train at 1-3 RIR [[{REAL_TITLE}]].", tmp)
    try:
        code, d = call(url, "/chat", {"message": "how hard should I train?"})
        assert code == 200, code
        assert REAL_TITLE in d["reply"], d["reply"]
        assert d["stripped"] == [], d["stripped"]
    finally:
        srv.shutdown()


def test_invented_citation_stripped(tmp):
    srv, url, _, _ = start_server(
        f"Do this [[{REAL_TITLE}]] and also [[The Best Rest Times Ever]].", tmp)
    try:
        _, d = call(url, "/chat", {"message": "rest times?"})
        assert "The Best Rest Times Ever" not in d["reply"], d["reply"]
        assert REAL_TITLE in d["reply"], "a real citation must survive"
        assert d["stripped"] == ["The Best Rest Times Ever"], d["stripped"]
    finally:
        srv.shutdown()


def test_thinking_block_hidden(tmp):
    srv, url, _, _ = start_server("<think>internal monologue</think>Rest 2 min.", tmp)
    try:
        _, d = call(url, "/chat", {"message": "rest?"})
        assert "monologue" not in d["reply"], d["reply"]
        assert "Rest 2 min." in d["reply"]
    finally:
        srv.shutdown()


def test_checkin_needs_no_message_and_resets(tmp):
    srv, url, sent, pack = start_server("Your side delts are down.", tmp)
    try:
        call(url, "/chat", {"message": "unrelated earlier chat"})
        assert len(app.Handler.history) == 2
        code, d = call(url, "/chat", {"mode": "checkin", "message": ""})
        assert code == 200 and d["reply"]
        # prior chat must not bleed into the ritual
        user_msgs = [m for m in sent["messages"] if m["role"] == "user"]
        assert len(user_msgs) == 1, user_msgs
        assert "unrelated earlier chat" not in json.dumps(sent["messages"])
        # retrieval uses review topics, not the kickoff instruction text
        assert "weekly review" in pack.queries[-1], pack.queries[-1]
    finally:
        srv.shutdown()


def test_empty_chat_rejected(tmp):
    srv, url, _, _ = start_server("unused", tmp)
    try:
        code, _ = call(url, "/chat", {"message": "   "})
        assert code == 400, code
    finally:
        srv.shutdown()


def test_save_appends_and_creates(tmp):
    srv, url, _, _ = start_server("unused", tmp)
    try:
        code, d = call(url, "/save", {"text": "first entry"})
        assert code == 200 and d["saved"] is True
        assert app.LOG.exists(), "log file must be created on first save"
        assert "# Weekly log" in app.LOG.read_text()

        call(url, "/save", {"text": "second entry"})
        body = app.LOG.read_text()
        assert "first entry" in body and "second entry" in body, body
        assert body.index("first entry") < body.index("second entry"), "must append"

        code, _ = call(url, "/save", {"text": "  "})
        assert code == 400, "empty save must be rejected"
    finally:
        srv.shutdown()


def test_info_reports_backend(tmp):
    srv, url, _, _ = start_server("unused", tmp)
    try:
        _, d = call(url, "/info")
        assert d["backend"] == app.BACKEND
        assert d["entries"] == 1, d
    finally:
        srv.shutdown()


def test_unreachable_model_is_readable(tmp):
    srv, url, _, _ = start_server("unused", tmp)

    def boom(messages):
        raise OSError("connection refused")

    app.generate = boom
    try:
        code, d = call(url, "/chat", {"message": "hi"})
        assert code == 200, "a dead model must not 500"
        assert "ollama serve" in d["reply"], d["reply"]
    finally:
        srv.shutdown()


def test_user_context_carries_cut_targets(tmp):
    srv, url, sent, _ = start_server("ok", tmp)
    try:
        call(url, "/chat", {"message": "macros?"})
        system = sent["messages"][0]["content"]
        assert "1800" in system and "160" in system, "current cut targets must reach the model"
        assert "Working sets: 44" in system, "training data must reach the model"
        assert REAL_TITLE in system, "retrieved sources must reach the model"
    finally:
        srv.shutdown()



# ---- extraction resumption (scripts/build_pack.py) ----

def test_extraction_skips_completed(tmp):
    """A question already answered must never be re-asked."""
    import build_pack
    build_pack.PACK = Path(tmp) / "pack.json"
    build_pack.PACK.write_text(json.dumps({"entries": [
        {"topic": "volume", "question": build_pack.QUESTIONS[0][1],
         "answer": "a", "sources": [{"n": 1, "title": "T", "cited_text": "quote"}]}]}))
    pack = build_pack.load()
    done = {e["question"] for e in pack["entries"]}
    todo = [q for _, q in build_pack.QUESTIONS if q not in done]
    assert build_pack.QUESTIONS[0][1] not in todo
    assert len(todo) == len(build_pack.QUESTIONS) - 1


def test_extraction_checkpoint_survives(tmp):
    """Each answer is on disk immediately, so an expiry loses nothing."""
    import build_pack
    build_pack.PACK = Path(tmp) / "pack.json"
    build_pack.save({"entries": [{"topic": "t", "question": "q", "answer": "a",
                                  "sources": [{"n": 1, "title": "T", "cited_text": "c"}]}]})
    assert len(build_pack.load()["entries"]) == 1


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        with tempfile.TemporaryDirectory() as tmp:
            t(tmp)
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
