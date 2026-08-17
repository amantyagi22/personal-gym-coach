#!/usr/bin/env python3
"""Extract a cited knowledge pack from the NotebookLM notebook, once, for offline use.

The standalone coach app can't reach NotebookLM: `nlm` rides browser cookies that expire
every ~20 min. So we harvest real answers + real source titles + verbatim quotes here,
while logged in, into private/knowledge_pack.json. The app then cites ONLY from that file.

Auth dies mid-run, so every answer is checkpointed immediately and reruns skip what's done:

    python3 scripts/build_pack.py            # resume until complete
    python3 scripts/build_pack.py --status   # what's left
    python3 scripts/build_pack.py --retry-empty   # re-ask questions that returned no source

ponytail: questions live in this file, not a config. Editing a list is simpler than parsing one.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PACK = Path("private/knowledge_pack.json")
ASK = ["python3", "scripts/ask_cited.py", "--json"]

# The coaching spine: what a weekly check-in and a free-chat coach actually need.
# Keyed by topic so retrieval can boost on it. Training + the cut, per the repo's rules.
QUESTIONS = [
    ("volume", "How many sets per muscle per week does Jeff recommend for hypertrophy?"),
    ("volume", "What are the signs of too much training volume and junk volume?"),
    ("volume", "How many sets per muscle in a single session before it becomes junk volume?"),
    ("volume", "Should training volume change when cutting in a calorie deficit?"),
    ("reps", "What rep ranges does Jeff recommend for muscle growth?"),
    ("reps", "Are light weights as effective as heavy weights for hypertrophy?"),
    ("effort", "How close to failure should you train, and what does RIR mean?"),
    ("effort", "Should you train to failure on compound lifts or isolation exercises?"),
    ("effort", "How do you know if you are training hard enough?"),
    ("selection", "How does Jeff recommend selecting exercises for a program?"),
    ("selection", "Why does training a muscle in the lengthened or stretched position matter?"),
    ("selection", "Are machines or free weights better for building muscle?"),
    ("selection", "What are the best exercises for side delts and shoulder width?"),
    ("selection", "What are the best exercises for back width and thickness?"),
    ("selection", "What are the best chest exercises and pressing angles?"),
    ("selection", "What are the best biceps and triceps exercises?"),
    ("selection", "Are isolation exercises necessary or are compounds enough?"),
    ("progression", "How should you progress load and reps over time for progressive overload?"),
    ("progression", "What is double progression and how do you apply it?"),
    ("progression", "What should you do when a lift stalls or plateaus?"),
    ("progression", "How fast should beginners and intermediates expect to add weight?"),
    ("frequency", "How many times per week should you train each muscle?"),
    ("frequency", "Is an upper lower split better than push pull legs?"),
    ("frequency", "How many days per week should you train to build muscle?"),
    ("recovery", "How long should you rest between sets?"),
    ("recovery", "How important is sleep for muscle growth and recovery?"),
    ("recovery", "What is a deload and when do you need one?"),
    ("recovery", "How does stress affect recovery and training progress?"),
    ("recovery", "How long does it take to recover between training sessions?"),
    ("technique", "How important is exercise technique and range of motion for growth?"),
    ("technique", "Should you use partial reps or full range of motion?"),
    ("technique", "How should you warm up before training?"),
    ("injury", "What should you do if a joint or tendon hurts during a lift?"),
    ("injury", "How do you train around an elbow or shoulder injury?"),
    ("injury", "Is lower back pain from deadlifts and squats normal?"),
    ("cut", "How large should a calorie deficit be when cutting to lose fat?"),
    ("cut", "How much protein per day do you need when cutting to keep muscle?"),
    ("cut", "How fast should you lose weight per week without losing muscle?"),
    ("cut", "Can you build muscle while losing fat at the same time?"),
    ("cut", "What is a diet break or refeed and when should you use one?"),
    ("cut", "How do you know if you are cutting too fast and losing muscle?"),
    ("cut", "How should you set carbs and fats when cutting?"),
    ("cut", "Does cardio interfere with muscle growth, and how much should you do?"),
    ("cut", "Why does strength drop during a cut and what should you do about it?"),
]


def load():
    if PACK.exists():
        return json.loads(PACK.read_text())
    return {"entries": []}


def save(pack):
    PACK.parent.mkdir(parents=True, exist_ok=True)
    PACK.write_text(json.dumps(pack, indent=2, ensure_ascii=False))


def ask(question):
    """Return (answer, sources) or raise RuntimeError on auth failure."""
    p = subprocess.run(ASK + [question], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    d = json.loads(p.stdout)
    return d["answer"], d["sources"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--retry-empty", action="store_true",
                    help="re-ask questions that returned no usable source")
    args = ap.parse_args()

    pack = load()
    done = {e["question"]: e for e in pack["entries"]}

    def usable(e):
        return any(s.get("cited_text") and "UNKNOWN" not in s.get("title", "")
                   for s in e["sources"])

    empty = [q for q in done if not usable(done[q])]
    todo = [(t, q) for t, q in QUESTIONS if q not in done]
    if args.retry_empty:
        todo += [(t, q) for t, q in QUESTIONS if q in empty]

    if args.status or not todo:
        print(f"pack: {len(done)}/{len(QUESTIONS)} answered, "
              f"{len(done) - len(empty)} with usable citations, {len(todo)} to go")
        if not todo:
            print(f"complete -> {PACK}")
        return

    print(f"{len(todo)} to fetch (~25s each). Checkpointing after each; safe to re-run.\n")
    for i, (topic, q) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {topic}: {q[:60]}...", flush=True)
        try:
            answer, sources = ask(q)
        except RuntimeError as e:
            msg = str(e)
            if "uth" in msg:  # auth expired / authentication
                print(f"\nAUTH EXPIRED after {len(done)} answers.\n"
                      f"Run `nlm login`, then re-run this script — it resumes here.")
                sys.exit(1)
            print(f"    failed, skipping: {msg[:100]}")
            continue

        entry = {"topic": topic, "question": q, "answer": answer, "sources": sources}
        pack["entries"] = [e for e in pack["entries"] if e["question"] != q] + [entry]
        save(pack)  # checkpoint every single time; auth can die between any two calls
        done[q] = entry
        cites = sum(1 for s in sources if s.get("cited_text"))
        print(f"    ok — {cites} cited passage(s)")

    usable_n = sum(1 for e in pack["entries"] if usable(e))
    print(f"\ndone: {len(pack['entries'])} answers, {usable_n} with usable citations -> {PACK}")


if __name__ == "__main__":
    main()
