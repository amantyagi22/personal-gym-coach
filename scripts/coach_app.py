#!/usr/bin/env python3
"""Local personal health coach — chat + weekly check-in, in a browser, on your machine.

    ollama serve &                       # once
    python3 scripts/coach_app.py         # -> http://localhost:8765

Cites ONLY from private/knowledge_pack.json (built by build_pack.py from the real notebook).
Any source title the model invents is stripped before you see it, so a citation on screen is
always one the notebook actually returned.

Backend: Ollama by default — nothing leaves this Mac. COACH_BACKEND=gemini switches the
writer to the Gemini API, which on the free tier trains on your data (see startup warning).

ponytail: stdlib http.server + a single HTML string. No framework, no build step, no deps
beyond what retrieval needs. Chat history is per-browser-session and in memory only.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK_PATH = ROOT / "private/knowledge_pack.json"
PROTOCOL = ROOT / "private/protocol.md"
LOG = ROOT / "private/weekly-log.md"

OLLAMA = "http://localhost:11434"
CHAT_MODEL = os.environ.get("COACH_MODEL", "qwen3:8b")
EMBED_MODEL = os.environ.get("COACH_EMBED_MODEL", "nomic-embed-text")
BACKEND = os.environ.get("COACH_BACKEND", "ollama").lower()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
PORT = int(os.environ.get("COACH_PORT", "8765"))
TOP_K = 4

# Personal targets stay out of the repo. Set COACH_NUTRITION, or drop a line in
# private/nutrition.txt (gitignored), e.g. "2200 kcal/day, 185 g protein".
_NUTRITION_FILE = ROOT / "private/nutrition.txt"
NUTRITION_TARGETS = os.environ.get("COACH_NUTRITION") or (
    _NUTRITION_FILE.read_text().strip() if _NUTRITION_FILE.exists() else "")

SYSTEM = """You are a personal training and nutrition coach for Aman, speaking in the voice of a \
coach who has studied Jeff Nippard's work closely.

ABSOLUTE RULES:
1. Every claim you attribute to Jeff MUST come from the CITED SOURCES block below. Cite as \
[[Exact Title]] using the title exactly as given - double square brackets, nothing else. Do NOT \
write sources as *"Title"* or "Title" or a Source: list; that format is rejected and deleted. Do \
NOT copy source titles out of his protocol document below - only titles from CITED SOURCES exist.
2. If the sources do not cover the question, say plainly: "The notebook doesn't cover this \
directly" and then coach from Aman's own data and constraints WITHOUT any citation. Never invent \
a video title. Never cite Huberman, studies, or other experts.
3. Coaching from Aman's constraints (his schedule, his joints, his numbers) needs no citation - \
just never dress it up as Jeff's view.
4. Aman's RPE column is blank, so effort is ALWAYS inferred, never measured. Say so when it matters.
5. Be concise and direct. Answer in a few short paragraphs. Give at most three concrete changes.
Weights are kg."""

WARN = """
  !! GEMINI MODE - free-tier Gemini uses your prompts to improve Google's models,
     and human reviewers may read them. This request includes your training log,
     body stats and injuries. Unset COACH_BACKEND to stay fully local via Ollama.
"""


# ---------- retrieval ----------

def post_json(url, payload, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(text):
    return post_json(f"{OLLAMA}/api/embeddings",
                     {"model": EMBED_MODEL, "prompt": text})["embedding"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class Pack:
    """The cited knowledge pack + its embeddings. Embeddings cached next to the pack."""

    def __init__(self):
        if not PACK_PATH.exists():
            sys.exit(f"No knowledge pack at {PACK_PATH}.\n"
                     f"Build it first (needs `nlm login`):  python3 scripts/build_pack.py")
        self.entries = [e for e in json.loads(PACK_PATH.read_text())["entries"]
                        if any(s.get("cited_text") for s in e["sources"])]
        if not self.entries:
            sys.exit("Knowledge pack has no cited passages. Re-run build_pack.py --retry-empty")
        self.titles = {s["title"] for e in self.entries for s in e["sources"]
                       if s.get("cited_text")}
        self.vectors = self._vectors()

    def _vectors(self):
        cache = PACK_PATH.with_suffix(".embeddings.json")
        key = [e["question"] for e in self.entries]
        if cache.exists():
            c = json.loads(cache.read_text())
            if c.get("questions") == key and c.get("model") == EMBED_MODEL:
                return c["vectors"]
        print(f"Embedding {len(self.entries)} pack entries with {EMBED_MODEL} (one time)...")
        vecs = [embed(f"{e['topic']}: {e['question']}\n{e['answer'][:1500]}")
                for e in self.entries]
        cache.write_text(json.dumps(
            {"questions": key, "model": EMBED_MODEL, "vectors": vecs}))
        return vecs

    def search(self, query, k=TOP_K):
        qv = embed(query)
        ranked = sorted(zip(self.entries, self.vectors),
                        key=lambda p: cosine(qv, p[1]), reverse=True)
        return [e for e, _ in ranked[:k]]


def sources_block(entries):
    out = []
    for e in entries:
        for s in e["sources"]:
            if s.get("cited_text"):
                out.append(f'[[{s["title"]}]]\n"{s["cited_text"][:600]}"')
    return "\n\n".join(out) if out else "(no relevant sources found)"


# ---------- citation enforcement ----------

# A model that ignores the [[Title]] convention still emits titles - as *"Title"*, "Title",
# or a bare quoted line. Those bypass bracket-only checking, so anything quoted that looks
# like a source title gets validated too. Missing one means an unverified title reaches the
# user looking authoritative, which is the exact failure this app exists to prevent.
QUOTED = re.compile(r'[*_]{0,2}"([^"\n]{12,120})"[*_]{0,2}')


def strip_fake_citations(text, allowed):
    """Remove any source title the model invented, whatever syntax it used."""
    removed = []

    def check(title, fmt):
        title = title.strip().rstrip(".,;:")
        if title in allowed:
            return fmt(title)
        removed.append(title)
        return ""

    cleaned = re.sub(r"\[\[([^\]]+)\]\]",
                     lambda m: check(m.group(1), lambda t: f"[[{t}]]"), text)

    def quoted(m):
        title = m.group(1).strip().rstrip(".,;:")
        # Only police quotes that look like citations, not ordinary quoted prose.
        # A verbatim passage we handed the model is legitimate and must survive.
        if title in allowed:
            return f"[[{title}]]"
        if any(title in a for a in allowed):
            return m.group(0)  # a real cited_text excerpt, leave it
        looks_like_title = title.istitle() or title.count(" ") <= 12
        if looks_like_title and re.search(r"[A-Z]", title) and "." not in title[:-1]:
            removed.append(title)
            return ""
        return m.group(0)

    cleaned = QUOTED.sub(quoted, cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned), removed


# ---------- context from the user's own data ----------

def training_summary():
    p = subprocess.run([sys.executable, str(ROOT / "scripts/weekly_review.py")],
                       capture_output=True, text=True, cwd=ROOT)
    return p.stdout.strip() or "(no training data available)"


def read_head(path, chars):
    return path.read_text()[:chars] if path.exists() else ""


def user_context():
    parts = [f"THIS WEEK'S TRAINING DATA:\n{training_summary()}"]
    proto = read_head(PROTOCOL, 3000)
    if proto:
        # The protocol is full of 📎 *Source: "..."* lines from when it was written. Left in,
        # the model copies those titles verbatim instead of citing what we actually retrieved.
        proto = re.sub(r'📎\s*\*?(?:Source:)?\s*"[^"]+"\*?', "", proto)
        parts.append(
            "HIS CURRENT PROGRAM (background only - this is a document YOU wrote for him "
            "earlier. It is NOT a source. Never quote it, never call it a source, never "
            "answer from it. Use it only to know what he is currently doing):\n" + proto)
    log = read_head(LOG, 1500)
    if log:
        parts.append(f"RECENT WEEKLY LOG:\n{log}")
    if NUTRITION_TARGETS:
        parts.append(
            f"CURRENT NUTRITION TARGETS (set by the user, and they override anything in the "
            f"program document above): {NUTRITION_TARGETS}. If the cited sources support a "
            f"different intake, show the cited number and let the user decide - do not "
            f"silently accept and do not silently override.")
    return "\n\n".join(parts)


# ---------- model backends ----------

def call_ollama(messages):
    return post_json(f"{OLLAMA}/api/chat",
                     {"model": CHAT_MODEL, "messages": messages, "stream": False,
                      "options": {"temperature": 0.4}})["message"]["content"]


def call_gemini(messages):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return "GEMINI_API_KEY is not set. Unset COACH_BACKEND to use local Ollama."
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [{"role": "model" if m["role"] == "assistant" else "user",
                 "parts": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"]
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={key}")
    try:
        d = post_json(url, {"systemInstruction": {"parts": [{"text": system}]},
                            "contents": contents})
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except (urllib.error.HTTPError, KeyError, IndexError) as e:
        detail = e.read().decode()[:200] if isinstance(e, urllib.error.HTTPError) else str(e)
        return f"Gemini call failed ({detail}). Unset COACH_BACKEND to use local Ollama."


def generate(messages):
    return call_gemini(messages) if BACKEND == "gemini" else call_ollama(messages)


# ---------- the two flows ----------

CHECKIN_KICKOFF = """Run Aman's weekly check-in.

Step 1: In one or two sentences, name the single most important thing in this week's data - a \
muscle under its target, a lift flat two weeks, a missed session, or a stale export (if the \
latest logged session is over a week old, say so first).
Step 2: Ask him ONE question - the most useful thing the data cannot tell you (sleep, stress, \
joint pain, or which sets actually got near failure). Ask exactly one and then stop.

Do not list all your questions. Do not give changes yet - those come after he answers."""


def build_messages(history, pack, user_msg, retrieval_query=None):
    hits = pack.search(retrieval_query or user_msg)
    # Sources go LAST, nearest the question: a small model answers from whatever is most
    # recent and confident in its context, and the protocol excerpt otherwise wins.
    system = (f"{SYSTEM}\n\n=== AMAN'S DATA ===\n{user_context()}\n\n"
              f"=== CITED SOURCES — the ONLY citable material, and where your answer "
              f"must come from ===\n{sources_block(hits)}\n\n"
              f"Answer the question using the CITED SOURCES above. Cite each Jeff claim as "
              f"[[Exact Title]].")
    return [{"role": "system", "content": system}] + history[-8:] + \
           [{"role": "user", "content": user_msg}]


# ---------- web ----------

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coach</title><style>
:root{--bg:#faf9f7;--fg:#1a1a1a;--mut:#6b6b6b;--card:#fff;--line:#e5e3df;--acc:#b4532a}
@media(prefers-color-scheme:dark){:root{--bg:#1a1917;--fg:#eceae6;--mut:#9a978f;--card:#252320;--line:#38352f;--acc:#e0794a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,system-ui,sans-serif;display:flex;flex-direction:column;height:100vh}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}
.meta{font-size:12px;color:var(--mut)}
button{font:inherit;font-size:14px;padding:8px 14px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
button:disabled{opacity:.5;cursor:default}
#log{flex:1;overflow-y:auto;padding:20px;max-width:820px;width:100%;margin:0 auto}
.msg{margin-bottom:18px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.user{text-align:right}.msg.user span{background:var(--acc);color:#fff;padding:9px 13px;
border-radius:13px;display:inline-block;text-align:left;max-width:85%}
.cite{display:block;margin:9px 0;padding:9px 13px;border-left:3px solid var(--acc);
background:var(--card);border-radius:0 8px 8px 0;font-size:14px}
.cite b{display:block;font-size:12px;color:var(--acc);margin-bottom:3px}
.cite i{color:var(--mut)}
form{display:flex;gap:9px;padding:14px 20px;border-top:1px solid var(--line);
max-width:820px;width:100%;margin:0 auto}
input{flex:1;font:inherit;padding:11px 14px;border-radius:9px;border:1px solid var(--line);
background:var(--card);color:var(--fg)}
.warn{background:#8a2f14;color:#fff;padding:7px 20px;font-size:13px}
</style></head><body>
<header><div><h1>Personal Coach</h1><div class="meta" id="meta"></div></div>
<button id="checkin">Start weekly check-in</button></header>
<div id="warn"></div><div id="log"></div>
<form id="f"><input id="q" placeholder="Ask anything - training, the cut, an aching elbow..."
autocomplete="off"><button class="primary" id="send">Send</button></form>
<script>
const log=document.getElementById('log'),q=document.getElementById('q'),f=document.getElementById('f');
fetch('/info').then(r=>r.json()).then(d=>{
  document.getElementById('meta').textContent=`${d.backend} · ${d.model} · ${d.entries} cited topics`;
  if(d.backend==='gemini')document.getElementById('warn').innerHTML=
    '<div class="warn">Gemini mode: your data goes to Google and free-tier prompts are used for training.</div>';
});
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function render(t){return esc(t).replace(/\\[\\[([^\\]]+)\\]\\]/g,
  (m,x)=>`<span class="cite"><b>Source</b>${x}</span>`)}
function add(role,text){const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=role==='user'?`<span>${esc(text)}</span>`:render(text);
  log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
function addSave(el,text){const b=document.createElement('button');
  b.textContent='Save to weekly log';b.style.cssText='font-size:12px;padding:4px 9px;margin-top:8px';
  b.onclick=async()=>{b.disabled=true;
    const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})});
    b.textContent=(await r.json()).saved?'Saved to weekly-log.md':'Save failed';};
  el.appendChild(document.createElement('br'));el.appendChild(b)}
async function send(text,mode){
  if(text)add('user',text);
  const wait=add('bot','...');q.value='';q.disabled=true;
  document.getElementById('send').disabled=true;document.getElementById('checkin').disabled=true;
  try{const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({message:text,mode:mode||'chat'})});
    const d=await r.json();wait.innerHTML=render(d.reply);
    if(d.stripped&&d.stripped.length)wait.innerHTML+=
      `<div class="cite"><b>removed</b><i>${d.stripped.length} invented source title(s) stripped</i></div>`;
    addSave(wait,d.reply);
  }catch(e){wait.textContent='Error: '+e.message}
  q.disabled=false;document.getElementById('send').disabled=false;
  document.getElementById('checkin').disabled=false;q.focus();log.scrollTop=log.scrollHeight;
}
f.onsubmit=e=>{e.preventDefault();if(q.value.trim())send(q.value.trim())};
document.getElementById('checkin').onclick=()=>send('','checkin');
q.focus();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    pack = None
    history = []

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/info":
            self._send(200, json.dumps({
                "backend": BACKEND,
                "model": GEMINI_MODEL if BACKEND == "gemini" else CHAT_MODEL,
                "entries": len(self.pack.entries)}))
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path == "/save":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or "{}")
            entry = (body.get("text") or "").strip()
            if not entry:
                return self._send(400, json.dumps({"saved": False}))
            append_log(entry)
            print(f"  appended {len(entry)} chars to {LOG}")
            return self._send(200, json.dumps({"saved": True, "path": str(LOG)}))
        if self.path != "/chat":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or "{}")
        mode, msg = req.get("mode", "chat"), (req.get("message") or "").strip()
        if mode == "checkin":
            # Start the ritual on a clean thread so prior chat doesn't bleed into it,
            # but keep answering it as normal chat afterwards.
            Handler.history = []
            msg = CHECKIN_KICKOFF
            retrieval_query = "weekly review volume progression recovery cut adherence"
        elif msg:
            retrieval_query = msg
        else:
            return self._send(400, json.dumps({"reply": "Empty message."}))

        try:
            raw = generate(build_messages(Handler.history, self.pack, msg, retrieval_query))
        except (urllib.error.URLError, OSError) as e:
            return self._send(200, json.dumps({"reply":
                f"Could not reach the model ({e}). Is `ollama serve` running?"}))

        # qwen3 emits <think> blocks; the user wants the coaching, not the monologue.
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
        reply, stripped = strip_fake_citations(raw, self.pack.titles)
        Handler.history = Handler.history[-6:] + [
            {"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        if stripped:
            print(f"  stripped invented citations: {stripped}")
        self._send(200, json.dumps({"reply": reply, "stripped": stripped}))

    def log_message(self, *a):
        pass


def append_log(text):
    """Append a check-in entry to private/weekly-log.md so next week can score the loop."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    header = "" if LOG.exists() else "# Weekly log\n\n"
    LOG.write_text((LOG.read_text() if LOG.exists() else header) + text.rstrip() + "\n\n")


def selftest():
    allowed = {"How To Train For Pure Muscle Growth",
               "Training to failure is not necessary for growth; stopping one to three "
               "reps short produces nearly identical hypertrophy."}
    clean, removed = strip_fake_citations(
        "Stop at 1-3 RIR [[How To Train For Pure Muscle Growth]] and rest "
        "[[The Optimal Rest Time For Growth]].", allowed)
    assert "How To Train For Pure Muscle Growth" in clean
    assert "The Optimal Rest Time" not in clean, clean
    assert removed == ["The Optimal Rest Time For Growth"], removed

    # A model ignoring the [[ ]] convention must still be policed: a real title in
    # *"..."* form is normalised, an invented one in the same form is removed.
    clean, removed = strip_fake_citations(
        'Source: *"How To Train For Pure Muscle Growth"* and *"The Best Rest Times Ever"*.',
        allowed)
    assert "[[How To Train For Pure Muscle Growth]]" in clean, clean
    assert "The Best Rest Times Ever" not in clean, clean
    assert removed == ["The Best Rest Times Ever"], removed

    # A verbatim passage we handed the model is evidence, not a citation - it must survive.
    passage = ('He says "Training to failure is not necessary for growth; stopping one '
               'to three reps short produces nearly identical hypertrophy."')
    clean, removed = strip_fake_citations(passage, allowed)
    assert "nearly identical hypertrophy" in clean, clean
    assert removed == [], removed

    # Ordinary quoted prose is not a title and must not be eaten.
    clean, removed = strip_fake_citations(
        'He told me "this is going to hurt a lot tomorrow morning, sorry."', allowed)
    assert "going to hurt" in clean, clean

    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 0]) == 0.0  # no crash on a zero vector

    text, rem = strip_fake_citations("No citations here.", allowed)
    assert text == "No citations here." and rem == []
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if BACKEND == "gemini":
        print(WARN)
    Handler.pack = Pack()
    print(f"Coach ready — http://localhost:{PORT}\n"
          f"  backend : {BACKEND} ({GEMINI_MODEL if BACKEND=='gemini' else CHAT_MODEL})\n"
          f"  cited   : {len(Handler.pack.entries)} topics, {len(Handler.pack.titles)} sources\n"
          f"  data    : {'strong_workouts.csv found' if (ROOT/'data/strong_workouts.csv').exists() else 'NO CSV'}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
