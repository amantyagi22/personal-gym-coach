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
from datetime import date, datetime
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
# Bump when what we embed changes, so cached vectors are not reused.
EMBED_SCHEME = "q-only-v2"
# Cap output: length is the latency and the heat, not prompt size. Raise if replies
# get cut off mid-sentence; COACH_THINK=1 restores qwen3 reasoning (slower).
MAX_TOKENS = int(os.environ.get("COACH_MAX_TOKENS", "420"))
THINK = os.environ.get("COACH_THINK", "") == "1"
# Drop topics far below the best match: they are a different subject, and handing
# them to the model invites it to answer from the wrong one.
RELEVANCE_FLOOR = 0.62
RELEVANCE_MARGIN = 0.12

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
2. The block marked "THE ANSWER TO THIS QUESTION IS BELOW" contains the notebook's actual \
researched answer. USE IT. Give its specific numbers and recommendations directly - do not say \
the sources "do not explicitly state" something that is written there, and NEVER write phrases \
like "outside the provided texts" or fall back on your own training knowledge. If it genuinely \
is not covered, say "The notebook doesn't cover this directly" and coach from Aman's own data \
WITHOUT any citation. Never invent a video title. Never cite Huberman, studies, or other experts.
3. Coaching from Aman's constraints (his schedule, his joints, his numbers) needs no citation - \
just never dress it up as Jeff's view.
4. Aman's RPE column is blank, so effort is ALWAYS inferred, never measured. Say so when it matters.
5. BE SHORT. Answer in AT MOST 150 words. Lead with the answer in one sentence, then at most \
three short bullets. No preamble, no restating the question, no closing summary, no "in \
conclusion". A long answer is a worse answer. Give at most three concrete changes.
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
            # scheme bumps when what we embed changes, so stale vectors aren't reused
            if (c.get("questions") == key and c.get("model") == EMBED_MODEL
                    and c.get("scheme") == EMBED_SCHEME):
                return c["vectors"]
        print(f"Embedding {len(self.entries)} pack entries with {EMBED_MODEL} (one time)...")
        # Question only: the long answer text dilutes the topic signal and measurably
        # lowers similarity against a user's short question.
        vecs = [embed(f"{e['topic']}: {e['question']}") for e in self.entries]
        cache.write_text(json.dumps(
            {"questions": key, "model": EMBED_MODEL, "scheme": EMBED_SCHEME, "vectors": vecs}))
        return vecs

    def search(self, query, k=TOP_K):
        qv = embed(query)
        ranked = sorted(((cosine(qv, v), e) for e, v in zip(self.entries, self.vectors)),
                        key=lambda p: p[0], reverse=True)
        best = ranked[0][0]
        # Only keep topics close to the best match. A 0.62 hit against a 0.89 best is a
        # different subject, and including it invites the model to answer from it -
        # which is how "best side delt exercises" got answered with Meadows rows.
        keep = [e for score, e in ranked[:k]
                if score >= max(RELEVANCE_FLOOR, best - RELEVANCE_MARGIN)]
        return keep or [ranked[0][1]]


def sources_block(entries):
    """Keep this SMALL. A 5k-token prompt makes qwen3:8b lose the answer that's sitting
    right in front of it and fall back on its own memory - the exact failure this app
    exists to prevent. The top topic carries the answer; the rest are context at most."""
    out = []
    for rank, e in enumerate(entries):
        if rank == 0:
            block = [f"THE ANSWER TO THIS QUESTION IS BELOW.\nTopic: {e['question']}\n"
                     f"{e['answer'][:2200]}"]
            titles = [s["title"] for s in e["sources"] if s.get("cited_text")][:4]
            if titles:
                block.append("Cite these for the claims above:\n"
                             + "\n".join(f"[[{t}]]" for t in titles))
        else:
            # Supporting topics: headline only. Full answers here crowd out the real one.
            block = [f"(also relevant) {e['question']}\n{e['answer'][:400]}"]
            t = next((s["title"] for s in e["sources"] if s.get("cited_text")), None)
            if t:
                block.append(f"[[{t}]]")
        out.append("\n".join(block))
    return "\n\n---\n\n".join(out) if out else "(no relevant sources found)"


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


def one_question_only(text):
    """The check-in is one question at a time. qwen3:8b keeps stacking two or three
    however the prompt is worded, so trim after the first - instructions alone don't
    hold on an 8B model, and a wall of questions is the thing that makes it a form."""
    parts = text.split("?")
    if len(parts) <= 2:
        return text
    return parts[0] + "?"


def data_freshness(summary):
    """State the export's age as a fact. The model has no clock, so asked to judge
    staleness it invents an answer ("only 1 day old" for a six-week-old export)."""
    m = re.search(r"latest logged session: (\d{1,2} \w{3} \d{4})", summary)
    if not m:
        return "The training log has no dated sessions."
    logged = datetime.strptime(m.group(1), "%d %b %Y").date()
    days = (date.today() - logged).days
    if days > 10:
        return (f"TODAY IS {date.today():%d %b %Y}. The most recent logged session is "
                f"{logged:%d %b %Y} - {days} DAYS AGO. THE EXPORT IS STALE: this is not "
                f"recent training. Say so FIRST, before any analysis, and tell him to "
                f"re-export from Strong (Settings -> Export Data).")
    return (f"Today is {date.today():%d %b %Y}; the most recent logged session is "
            f"{logged:%d %b %Y} ({days} days ago). The export is current.")


def user_context():
    summary = training_summary()
    parts = [f"DATA FRESHNESS: {data_freshness(summary)}",
             f"TRAINING DATA FROM THE LOG:\n{summary}"]
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
    # Measured: unconstrained, qwen3:8b wrote 1,735 tokens (~108s) for an answer that
    # needs ~250. Generation length IS the latency and the heat - prompt processing was
    # only 1,272 tokens. think=False skips the reasoning pass we strip anyway.
    return post_json(f"{OLLAMA}/api/chat",
                     {"model": CHAT_MODEL, "messages": messages, "stream": False,
                      "think": THINK,
                      "options": {"temperature": 0.4,
                                  "num_predict": MAX_TOKENS}})["message"]["content"]


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

CHECKIN_KICKOFF = """Run Aman's weekly check-in. Write at most 60 words total.

First: if DATA FRESHNESS says the export is stale, say so in one sentence with how many days old \
it is, tell him to re-export from Strong, and then ask whether he has been training at all since \
then - do NOT analyse the old numbers and do NOT ask what a specific session felt like, because \
you do not know that it happened. Otherwise name in ONE sentence the single most important \
thing in the data: a muscle below the 10-20 sets/week target, a lift flat two weeks, or a missed \
session. Use the actual set counts shown; do not guess at them, and do not comment on the date \
yourself - DATA FRESHNESS already states it.

Then: ask exactly ONE question - the most useful thing the data cannot tell you. Ask it in plain \
language about how training FELT: sleep, stress, joint pain, or whether a particular set actually \
got close to failure. Never ask him for an RPE or RIR number - he does not log those, which is \
why you are asking in words instead. ONE question mark in your whole reply.

No changes or advice yet; those come after he answers. Do not label your reply with "Step 1" or \
"Step 2"."""


def build_messages(history, pack, user_msg, retrieval_query=None, step=None):
    """step(name, status, detail) reports pipeline progress to the caller, if given."""
    def report(*a):
        if step:
            step(*a)

    report("retrieve", "run", "embedding your question")
    hits = pack.search(retrieval_query or user_msg)
    titles = [s["title"] for e in hits for s in e["sources"] if s.get("cited_text")]
    report("retrieve", "ok",
           f"{len(hits)} topics · {len(set(titles))} sources")

    report("data", "run", "reading your training log")
    ctx = user_context()
    sessions = re.search(r"Sessions: (\d+)", ctx)
    sets = re.search(r"Working sets: (\d+)", ctx)
    report("data", "ok", (f"{sessions.group(1)} sessions · {sets.group(1)} sets"
                          if sessions and sets else "context loaded"))
    # Sources go LAST, nearest the question: a small model answers from whatever is most
    # recent and confident in its context, and the protocol excerpt otherwise wins.
    system = (f"{SYSTEM}\n\n=== AMAN'S DATA ===\n{ctx}\n\n"
              f"=== CITED SOURCES — the ONLY citable material, and where your answer "
              f"must come from ===\n{sources_block(hits)}\n\n"
              f"Answer the question using the CITED SOURCES above. Cite each Jeff claim as "
              f"[[Exact Title]].")
    return [{"role": "system", "content": system}] + history[-8:] + \
           [{"role": "user", "content": user_msg}]


# ---------- web ----------

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coach</title><style>
:root{--bg:#f7f6f3;--fg:#22201d;--mut:#77736c;--card:#fff;--line:#e4e1db;--acc:#c2410c;
--acc-soft:#fdf0e7;--shadow:0 1px 3px rgba(0,0,0,.06),0 4px 16px rgba(0,0,0,.04)}
@media(prefers-color-scheme:dark){:root{--bg:#171614;--fg:#eae7e2;--mut:#9d9891;--card:#211f1c;
--line:#332f2a;--acc:#fb923c;--acc-soft:#2a1d13;--shadow:0 1px 3px rgba(0,0,0,.3)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 ui-sans-serif,-apple-system,system-ui,sans-serif;
display:flex;flex-direction:column;height:100vh;-webkit-font-smoothing:antialiased}
header{padding:12px 22px;border-bottom:1px solid var(--line);display:flex;
justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;background:var(--card)}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:-.01em}
.meta{font-size:12px;color:var(--mut);margin-top:1px}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#16a34a;margin-right:5px}
button{font:inherit;font-size:13.5px;font-weight:500;padding:8px 15px;border-radius:9px;
border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;
transition:all .15s}
button:hover:not(:disabled){border-color:var(--acc);color:var(--acc)}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
button.primary:hover:not(:disabled){opacity:.88;color:#fff}
button:disabled{opacity:.45;cursor:default}
#log{flex:1;overflow-y:auto;padding:26px 22px;max-width:780px;width:100%;margin:0 auto}
.msg{margin-bottom:22px;overflow-wrap:anywhere}
.msg.user{display:flex;justify-content:flex-end}
.msg.user span{background:var(--acc);color:#fff;padding:9px 14px;border-radius:15px 15px 4px 15px;
max-width:82%;line-height:1.5}
.msg.bot{animation:fade .25s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
.msg.bot p{margin:0 0 11px}
.msg.bot ul,.msg.bot ol{margin:0 0 11px;padding-left:22px}
.msg.bot li{margin:3px 0}
.msg.bot strong{font-weight:640}
.msg.bot h3{font-size:15px;margin:16px 0 7px;font-weight:640}
.msg.bot code{background:var(--acc-soft);padding:1px 5px;border-radius:4px;font-size:.9em}
/* Inline chip: a citation belongs INSIDE the sentence it supports, not as a block
   that guillotines the paragraph in half. */
.cite{display:inline-flex;align-items:center;gap:4px;background:var(--acc-soft);
color:var(--acc);border:1px solid transparent;border-radius:20px;padding:1px 9px;
font-size:12px;font-weight:520;line-height:1.7;cursor:default;vertical-align:baseline;
max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cite::before{content:"📎";font-size:10px}
.cite:hover{border-color:var(--acc);max-width:none;white-space:normal}
/* Pipeline panel: what's actually running, live. */
.steps{border:1px solid var(--line);border-radius:11px;background:var(--card);
padding:5px 0;margin-bottom:12px;font-size:13.5px;box-shadow:var(--shadow);overflow:hidden}
.steps.done{opacity:.62}
.steps summary{list-style:none;cursor:pointer;padding:6px 14px;color:var(--mut);
font-size:12.5px;display:flex;align-items:center;gap:7px;font-weight:500}
.steps summary::-webkit-details-marker{display:none}
.steps summary b{color:var(--fg);font-weight:600}
.step{display:flex;align-items:center;gap:10px;padding:5px 15px}
.step .ic{width:16px;height:16px;flex:0 0 16px;display:grid;place-items:center;
border-radius:50%;font-size:10px;font-weight:700}
.step.run .ic{border:1.7px solid var(--acc);border-top-color:transparent;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.step.ok .ic{background:#16a34a;color:#fff}
.step.warn .ic{background:#d97706;color:#fff}
.step.err .ic{background:#dc2626;color:#fff}
.step.wait .ic{border:1.7px solid var(--line)}
.step .nm{font-weight:530;min-width:74px}
.step.wait .nm,.step.wait .dt{color:var(--mut);opacity:.6}
.step .dt{color:var(--mut);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.typing{display:inline-flex;gap:4px;padding:4px 0}
.typing i{width:6px;height:6px;border-radius:50%;background:var(--mut);animation:bounce 1.2s infinite}
.typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-4px)}}
.stripped{font-size:12px;color:var(--mut);margin-top:9px;font-style:italic}
.savebtn{font-size:12px;padding:4px 11px;margin-top:10px;border-radius:7px}
.empty{text-align:center;color:var(--mut);margin-top:16vh;line-height:1.9}
.empty b{display:block;color:var(--fg);font-size:17px;font-weight:600;margin-bottom:6px}
.chip{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:6px 13px;margin:4px 3px;font-size:13px;cursor:pointer;transition:all .15s}
.chip:hover{border-color:var(--acc);color:var(--acc)}
form{display:flex;gap:9px;padding:14px 22px 18px;border-top:1px solid var(--line);
max-width:780px;width:100%;margin:0 auto;background:var(--bg)}
input{flex:1;font:inherit;font-size:15px;padding:12px 16px;border-radius:11px;
border:1px solid var(--line);background:var(--card);color:var(--fg);outline:none;transition:.15s}
input:focus{border-color:var(--acc)}
.warn{background:#9a3412;color:#fff;padding:8px 22px;font-size:13px}
</style></head><body>
<header><div><h1>Personal Coach</h1><div class="meta" id="meta"></div></div>
<button id="checkin">Weekly check-in</button></header>
<div id="warn"></div><div id="log"></div>
<form id="f"><input id="q" placeholder="Ask anything - training, the cut, an aching elbow..."
autocomplete="off"><button class="primary" id="send">Send</button></form>
<script>
const log=document.getElementById('log'),q=document.getElementById('q'),f=document.getElementById('f');
const STARTERS=["How many sets per muscle per week?","How close to failure should I train?",
  "How much protein while cutting?","Best exercises for side delts?"];
fetch('/info').then(r=>r.json()).then(d=>{
  document.getElementById('meta').innerHTML=
    `<span class="dot"></span>${d.backend} · ${d.model} · ${d.entries} cited topics`;
  if(d.backend==='gemini')document.getElementById('warn').innerHTML=
    '<div class="warn">Gemini mode: your data goes to Google and free-tier prompts are used for training.</div>';
  log.innerHTML=`<div class="empty" id="empty"><b>What do you want to work on?</b>
    Grounded in ${d.entries} cited topics from your notebook.<br><br>`+
    STARTERS.map(s=>`<span class="chip">${s}</span>`).join('')+`</div>`;
  document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>send(c.textContent));
});
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
// Minimal markdown: the model writes **bold**, ### headings and - lists, and raw
// asterisks on screen look broken. Not a full parser - just what actually shows up.
function render(t){
  let h=esc(t)
    .replace(/\\[\\[([^\\]]+)\\]\\]/g,(m,x)=>`<span class="cite" title="${x}">${x}</span>`)
    .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>')
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/^###\\s*(.+)$/gm,'<h3>$1</h3>')
    .replace(/^\\s*[-*]\\s+(.+)$/gm,'<li>$1</li>')
    .replace(/^\\s*(\\d+)\\.\\s+(.+)$/gm,'<li>$2</li>');
  h=h.replace(/(<li>[\\s\\S]*?<\\/li>)(?!\\s*<li>)/g,'<ul>$1</ul>');
  // Split into blocks, then wrap only the loose prose. A heading followed by text on
  // the next line must not swallow that text into the heading block.
  return h.split(/\\n{2,}/).map(b=>b.split(/(<\\/h3>|<\\/ul>)/).map(part=>{
    if(!part.trim()||/^(<\\/h3>|<\\/ul>)$/.test(part))return part;
    if(/^\\s*<(ul|h3)/.test(part))return part;
    return `<p>${part.trim().replace(/\\n/g,'<br>')}</p>`;
  }).join('')).join('');
}
function add(role,text,raw){const e=document.getElementById('empty');if(e)e.remove();
  const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=role==='user'?`<span>${esc(text)}</span>`
    :(raw?text:render(text));
  log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
function addSave(el,text){const b=document.createElement('button');
  b.textContent='Save to weekly log';b.className='savebtn';
  b.onclick=async()=>{b.disabled=true;
    const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})});
    b.textContent=(await r.json()).saved?'Saved to weekly-log.md':'Save failed';};
  el.appendChild(document.createElement('br'));el.appendChild(b)}
// The pipeline, in the order it runs. Shown up-front as pending so you can see
// what's coming, not just what's finished.
const STEPS=[["retrieve","Retrieve","find cited passages"],
             ["data","Your data","training log + program"],
             ["generate","Generate","write the coaching"],
             ["verify","Verify","check every source title"]];
const ICON={ok:"✓",warn:"!",err:"×",run:"",wait:""};
function stepPanel(){
  const d=document.createElement('details');d.className='steps';d.open=true;
  d.innerHTML=`<summary><b>Working</b><span id="sm"></span></summary>`+
    STEPS.map(([k,n,h])=>`<div class="step wait" data-k="${k}">
      <span class="ic"></span><span class="nm">${n}</span><span class="dt">${h}</span></div>`).join('');
  return d;
}
function setStep(panel,name,status,detail){
  const el=panel.querySelector(`[data-k="${name}"]`);if(!el)return;
  el.className='step '+status;
  el.querySelector('.ic').textContent=ICON[status]||'';
  if(detail)el.querySelector('.dt').textContent=detail;
}
async function send(text,mode){
  if(text)add('user',text);
  const holder=add('bot','',true);
  const panel=stepPanel();holder.appendChild(panel);
  const body=document.createElement('div');holder.appendChild(body);
  body.innerHTML='<div class="typing"><i></i><i></i><i></i></div>';
  q.value='';q.disabled=true;
  document.getElementById('send').disabled=true;document.getElementById('checkin').disabled=true;
  const t0=Date.now();
  try{
    const r=await fetch('/chat-stream',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text,mode:mode||'chat'})});
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const {value,done}=await rd.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      const parts=buf.split('\\n\\n');buf=parts.pop();
      for(const p of parts){
        if(!p.startsWith('data: '))continue;
        const d=JSON.parse(p.slice(6));
        if(d.event==='step'){setStep(panel,d.name,d.status,d.detail);
          log.scrollTop=log.scrollHeight;}
        else if(d.event==='done'){
          body.innerHTML=render(d.reply);
          if(d.stripped&&d.stripped.length)body.innerHTML+=
            `<div class="stripped">${d.stripped.length} invented source title(s) removed</div>`;
          addSave(body,d.reply);
          panel.open=false;panel.classList.add('done');
          const secs=((Date.now()-t0)/1000).toFixed(1);
          panel.querySelector('summary b').textContent='4 steps';
          document.getElementById('sm').textContent=` · ${secs}s`;
          panel.querySelector('#sm').removeAttribute('id');
        }
      }
    }
  }catch(e){body.innerHTML=`<p>Error: ${esc(e.message)}</p>`}
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

    # The page is a single inline string that changes whenever the app is edited. Without
    # this, the browser serves a cached copy and you debug an answer the server never sent.
    def _send(self, code, body, ctype="application/json", no_cache=True):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if no_cache:
            self.send_header("Cache-Control", "no-store, must-revalidate")
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
        if self.path not in ("/chat", "/chat-stream"):
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or "{}")
        mode, msg = req.get("mode", "chat"), (req.get("message") or "").strip()
        if self.path == "/chat-stream":
            return self.stream_chat(mode, msg)
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
                f"Ollama isn't running, so I can't answer. Start it with `ollama serve` "
                f"in a terminal, then send this again.\n\n({e})"}))

        # qwen3 emits <think> blocks; the user wants the coaching, not the monologue.
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
        reply, stripped = strip_fake_citations(raw, self.pack.titles)
        if mode == "checkin":
            reply = one_question_only(reply)
        Handler.history = Handler.history[-6:] + [
            {"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        if stripped:
            print(f"  stripped invented citations: {stripped}")
        self._send(200, json.dumps({"reply": reply, "stripped": stripped}))

    def stream_chat(self, mode, msg):
        """Same pipeline as /chat, but each step is pushed to the browser as it happens,
        so a 90-second local generation isn't a blank wait."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(event, **data):
            try:
                self.wfile.write(f"data: {json.dumps({'event': event, **data})}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise                      # browser navigated away; abandon the request

        if mode == "checkin":
            Handler.history = []
            msg, retrieval_query = CHECKIN_KICKOFF, \
                "weekly review volume progression recovery cut adherence"
        elif msg:
            retrieval_query = msg
        else:
            return emit("done", reply="Empty message.", stripped=[])

        try:
            step = lambda name, status, detail="": emit(
                "step", name=name, status=status, detail=detail)
            messages = build_messages(Handler.history, self.pack, msg,
                                      retrieval_query, step=step)
            backend = GEMINI_MODEL if BACKEND == "gemini" else CHAT_MODEL
            emit("step", name="generate", status="run", detail=f"{backend} is writing")
            raw = generate(messages)
            emit("step", name="generate", status="ok",
                 detail=f"{len(raw.split())} words")

            emit("step", name="verify", status="run", detail="checking every source title")
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
            reply, stripped = strip_fake_citations(raw, self.pack.titles)
            if mode == "checkin":
                reply = one_question_only(reply)
            cited = len(set(re.findall(r"\[\[([^\]]+)\]\]", reply)))
            emit("step", name="verify", status="warn" if stripped else "ok",
                 detail=(f"{len(stripped)} invented removed" if stripped
                         else f"{cited} source(s) verified" if cited else "no claims cited"))

            Handler.history = Handler.history[-6:] + [
                {"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
            if stripped:
                print(f"  stripped invented citations: {stripped}")
            emit("done", reply=reply, stripped=stripped)
        except (urllib.error.URLError, OSError) as e:
            emit("step", name="generate", status="err", detail=str(e)[:60])
            emit("done", reply=f"Ollama isn't running, so I can't answer. Start it with `ollama serve` "
                f"in a terminal, then send this again.\n\n({e})",
                 stripped=[])

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
    # Bind BEFORE announcing: printing "Coach ready" and then dying on a taken port
    # tells the user the opposite of what happened.
    try:
        server = HTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        if e.errno != 48:                      # EADDRINUSE
            raise
        sys.exit(f"The coach is already running at http://localhost:{PORT} — just open it.\n"
                 f"(If you meant to restart it: pkill -f coach_app.py, then run this again.\n"
                 f" To run a second copy on another port: COACH_PORT=8766 "
                 f"python3 scripts/coach_app.py)")

    Handler.pack = Pack()
    print(f"Coach ready — http://localhost:{PORT}\n"
          f"  backend : {BACKEND} ({GEMINI_MODEL if BACKEND=='gemini' else CHAT_MODEL})\n"
          f"  cited   : {len(Handler.pack.entries)} topics, {len(Handler.pack.titles)} sources\n"
          f"  data    : {'strong_workouts.csv found' if (ROOT/'data/strong_workouts.csv').exists() else 'NO CSV'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
