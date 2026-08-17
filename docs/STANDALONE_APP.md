# The standalone coach app

A local chat coach you run outside Claude Code.
Free-form chat plus a weekly check-in, in a browser, on your own machine.

```bash
ollama serve &                    # once per boot
python3 scripts/coach_app.py      # -> http://localhost:8765
```

## Why it works the way it does

The coach's whole value is that **every Jeff claim is traceable to the notebook**.
Preserving that outside Claude Code drove every design decision here.

**The Gemini API cannot replace the citation engine.**
Gemini has never read Jeff's videos.
Ask it about weekly set volume and it answers from generic training knowledge, then invents a plausible-sounding video title.
That is precisely the failure `CLAUDE.md` calls non-negotiable.

**NotebookLM has no public API.**
Citations come from `nlm`, an unofficial CLI that drives NotebookLM through browser session cookies which expire roughly every 20 minutes.
A hosted app cannot hold that login.

So the citations are **extracted once, here, while logged in**, and retrieved offline forever after.

## The two pieces

### 1. `scripts/build_pack.py` — the one-time extraction

Runs 44 spine questions (30 training, 14 on the cut) through `ask_cited.py` and saves the real answers, real source titles, and verbatim quotes to `private/knowledge_pack.json`.

```bash
nlm login                              # required; expires every ~20 min
python3 scripts/build_pack.py          # ~30 min, resumes if interrupted
python3 scripts/build_pack.py --status # what's left
python3 scripts/build_pack.py --retry-empty   # re-ask ones that found no source
```

It checkpoints after **every single answer**, because auth can die between any two queries.
If it stops with `AUTH EXPIRED`, run `nlm login` again and re-run the same command — it picks up exactly where it left off.

Rebuild the pack occasionally to refresh citations, or after adding sources to the notebook.

### 2. `scripts/coach_app.py` — the app

- **Retrieval:** `nomic-embed-text` embeds the pack once (cached alongside it) and finds the passages relevant to your question.
- **Writing:** `qwen3:8b` via Ollama composes the reply from those passages plus your own data.
- **Your data:** it runs `weekly_review.py` live, and reads `private/protocol.md` and `private/weekly-log.md`.
- **Check-in button:** runs the weekly ritual — names the one thing that matters, then asks one question at a time.
- **Save to weekly log:** appends a reply to `private/weekly-log.md`, so next week can score whether you did what you said.

## Citation safety

Two mechanisms, because an 8B model follows instructions less reliably than Claude:

1. **Cited passages render as quote blocks**, visually separate from the model's prose, so you always see the notebook's actual words.
2. **Invented titles are stripped.** Every `[[Title]]` the model emits is checked against the real titles in the pack; anything it made up is removed before display, and the UI tells you how many were dropped.

If the pack doesn't cover something, the honest answer is "the notebook doesn't cover this directly" followed by uncited coaching from your own constraints.

## Backends

Ollama by default — nothing leaves your Mac.

```bash
COACH_BACKEND=gemini GEMINI_API_KEY=... python3 scripts/coach_app.py
```

**Read this before using Gemini mode.**
On the free tier, Google's terms state your prompts and responses are used to improve their products, and that human reviewers may read them.
Your prompts here include body weight, body fat, injuries, and your full training log.
The app prints a warning at startup and shows a banner in the UI when this mode is active.
Google has also withdrawn its published free-tier rate limits; they are now per-account in AI Studio.

Other knobs: `COACH_MODEL`, `COACH_EMBED_MODEL`, `COACH_PORT`, `GEMINI_MODEL`.

## Limits worth knowing

- Only runs while your Mac is on — it is not reachable from your phone elsewhere.
- The pack is a snapshot; questions outside its 44 topics get "not covered."
- `qwen3:8b` is a weaker writer than Claude. It is kept honest by retrieval and title-stripping, not by its own judgment.
- Everything under `private/` is gitignored. Never commit it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No knowledge pack at ...` | Run `nlm login`, then `python3 scripts/build_pack.py`. |
| `Could not reach the model` | Start Ollama: `ollama serve`. |
| `nlm login` hangs at "Waiting for sign-in" | Upgrade: `uv tool upgrade notebooklm-mcp-cli` (0.8.3 hung, 0.9.11 worked). Then `nlm login --clear`, or `nlm login --manual -f cookies.txt`. |
| `AUTH EXPIRED` mid-extraction | Normal. `nlm login`, re-run the same command; it resumes. |
| Replies cite nothing | Check `build_pack.py --status` — you may have entries without usable citations. Run `--retry-empty`. |
