# Quickstart — run the coach app locally

A chat coach in your browser, running entirely on your own machine.
Ask it anything about training or your cut, or press one button for a weekly check-in.

Every claim it attributes to Jeff Nippard carries a **real source title and a verbatim quote** from the notebook.
Titles it makes up are stripped before you see them.

---

## What you need

| | |
|---|---|
| **Python 3** | Already on macOS. Check: `python3 --version` |
| **[Ollama](https://ollama.com)** | Runs the AI locally, so nothing leaves your machine |
| **A NotebookLM notebook** | Jeff's videos, loaded once. See [README](README.md) |

---

## 1. Install Ollama and pull two models

```bash
brew install ollama          # or download from ollama.com
ollama serve &               # leave this running
ollama pull qwen3:8b         # writes the coaching (5 GB)
ollama pull nomic-embed-text # finds the right quotes (274 MB)
```

## 2. Add your workout data

Export from the **Strong** app (*Settings → Export Data*) and save it as:

```
data/strong_workouts.csv
```

Skip this and the coach still works — it just can't talk about your actual training.

## 3. Build the knowledge pack (one time, ~30 min)

This harvests real cited answers from the notebook so the app can work offline forever after.

```bash
nlm login                        # expires every ~20 min - that's fine
python3 scripts/build_pack.py    # go make coffee
```

**It saves after every single answer.** If it stops saying `AUTH EXPIRED`, just run `nlm login` again and re-run the same command — it picks up exactly where it left off, and nothing is lost.

Check progress any time:

```bash
python3 scripts/build_pack.py --status
```

## 4. Start the coach

```bash
python3 scripts/coach_app.py
```

Open **http://localhost:8765**

---

## Using it

**Just chat.** "My elbow hurts on curls, what should I swap?" · "Is 3 sets of squats enough?" · "Should I add cardio?"

**Weekly check-in.** Press *Start weekly check-in*. It reads your training log, tells you the one thing that matters most this week, then asks a few questions the data can't answer — sleep, stress, joints, how hard sets actually felt. It finishes with at most three changes.

**Save what matters.** Press *Save to weekly log* under any reply. It appends to `private/weekly-log.md`, so next week the coach can check whether you actually did it.

---

## Your numbers

Put your current calorie and protein targets in `private/nutrition.txt` (gitignored, one line):

```
2200 kcal/day, 185 g protein
```

The coach treats these as yours and final. If the notebook's cited advice differs, it shows you the source and lets you decide — it won't silently override you, and it won't silently agree either.

---

## Privacy

**By default nothing leaves your Mac.** Ollama runs locally, and the knowledge pack is a file on disk.

There's an optional Gemini mode for sharper writing:

```bash
COACH_BACKEND=gemini GEMINI_API_KEY=... python3 scripts/coach_app.py
```

**Read this before you use it.** On Google's free tier, your prompts are used to train their models and human reviewers may read them. Your prompts here include your body weight, injuries, and full training history. The app warns you at startup and shows a banner while it's active. Leave it off unless you have a reason.

Everything under `private/` and `data/` is gitignored and never committed.

---

## When something breaks

| It says | Do this |
|---|---|
| `No knowledge pack at ...` | Run step 3. |
| `Could not reach the model` | `ollama serve` isn't running. Start it. |
| `AUTH EXPIRED` while building | Normal. `nlm login`, re-run — it resumes. |
| `nlm login` hangs forever | `uv tool upgrade notebooklm-mcp-cli`, then `nlm login --clear`. |
| Answers cite nothing | `python3 scripts/build_pack.py --retry-empty` |
| Replies are slow | ~25s is normal on 8B. Shorten with `COACH_MAX_TOKENS=250`, or use a smaller model. |
| Reply cut off mid-sentence | Raise the cap: `COACH_MAX_TOKENS=700` |
| Laptop runs hot | Generation is GPU work. Lower `COACH_MAX_TOKENS`, or `COACH_MODEL=qwen3:4b`. |
| UI looks stale after an update | The app sends no-cache headers, but hard-refresh with `Cmd+Shift+R` if needed. |

## Options

```bash
COACH_PORT=9000          # different port
COACH_MODEL=qwen3:4b     # smaller/faster/cooler model
COACH_MAX_TOKENS=250     # shorter replies (default 420)
COACH_THINK=1            # restore qwen3 reasoning - better, but slower
COACH_NUTRITION="..."    # targets via env instead of the file
```

## Starting and stopping Ollama

The coach needs Ollama running. Nothing works without it - you'll see
*"Could not reach the model"*.

```bash
ollama serve            # start (leave the terminal open)
ollama ps               # what's loaded in memory right now
```

To stop it and free the memory (and stop the heat), quit the `ollama serve`
process - `Ctrl+C` in its terminal, or quit the menu-bar app if you installed it
that way. Models unload from memory on their own after ~5 minutes idle, so a
warm laptop settles by itself between questions.

A backgrounded `ollama serve &` dies when its terminal closes. To keep it alive:

```bash
nohup ollama serve > /tmp/ollama.log 2>&1 &
```

## Check it still works

```bash
python3 scripts/test_coach_app.py   # 11 behaviour tests, no Ollama needed
```

---

## The honest limits

- Runs only while your Mac is on. It's not reachable from your phone elsewhere.
- The pack covers 44 core topics. Ask outside them and it says so rather than guessing.
- `qwen3:8b` is a decent writer, not a great one. It's kept honest by the quotes and the title-stripping, not by its own judgment.
- Your RPE is usually unlogged, so **effort is always inferred**. The coach says so.

More detail on why it's built this way: [docs/STANDALONE_APP.md](docs/STANDALONE_APP.md)
