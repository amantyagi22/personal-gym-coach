#!/usr/bin/env python3
"""Weekly training review: last 7 days vs the 7 before, from the Strong export.

Prints sets/muscle, session count, and per-exercise load progression so the coach
skill can ask about what the CSV *can't* see (sleep, joints, effort) instead of
what it can.

    python3 scripts/weekly_review.py [--weeks-ago N] [--csv PATH]

ponytail: muscle map is a substring table, not an exercise database. Add rows when
a new exercise shows up as "unmapped" in the output.
"""
import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Substring -> muscle. First match wins, so order matters: specific before generic.
MUSCLE_MAP = [
    ("lateral raise", "side delts"), ("side delt", "side delts"),
    ("rear delt", "rear delts"), ("face pull", "rear delts"), ("reverse fly", "rear delts"),
    ("shoulder press", "front delts"), ("overhead press", "front delts"),
    ("military press", "front delts"), ("arnold", "front delts"),
    ("front raise", "front delts"),
    ("bench press", "chest"), ("chest press", "chest"), ("chest fly", "chest"),
    ("cable fly", "chest"), ("crossover", "chest"), ("pec deck", "chest"),
    ("dip", "chest"), ("push up", "chest"), ("push-up", "chest"),
    ("pulldown", "lats"), ("pull up", "lats"), ("pull-up", "lats"),
    ("chin up", "lats"), ("chin-up", "lats"), ("pullover", "lats"),
    ("row", "upper back"), ("shrug", "traps"),
    ("curl (barbell)", "biceps"), ("bicep", "biceps"), ("preacher", "biceps"),
    ("hammer curl", "biceps"), ("concentration", "biceps"),
    ("tricep", "triceps"), ("pushdown", "triceps"), ("skull", "triceps"),
    ("overhead extension", "triceps"), ("close grip", "triceps"),
    ("squat", "quads"), ("leg press", "quads"), ("leg extension", "quads"),
    ("lunge", "quads"), ("split squat", "quads"), ("hack", "quads"),
    ("deadlift", "hamstrings"), ("rdl", "hamstrings"), ("romanian", "hamstrings"),
    ("leg curl", "hamstrings"), ("good morning", "hamstrings"),
    ("hip thrust", "glutes"), ("glute", "glutes"),
    ("calf", "calves"),
    ("crunch", "abs"), ("plank", "abs"), ("leg raise", "abs"),
    ("ab ", "abs"), ("cable woodchop", "abs"), ("sit up", "abs"),
]


def muscle_of(exercise):
    name = exercise.lower()
    for key, muscle in MUSCLE_MAP:
        if key in name:
            return muscle
    return None


def load(csv_path):
    """Return working sets only: [(date, exercise, weight, reps), ...]."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("set_type", "").lower() == "warmup":
                continue
            try:
                date = datetime.strptime(r["start_time"].split(",")[0].strip(), "%d %b %Y")
            except (ValueError, KeyError):
                continue
            try:
                weight = float(r.get("weight_kg") or 0)
                reps = int(float(r.get("reps") or 0))
            except ValueError:
                continue
            if reps == 0:
                continue  # timed/cardio entry, not a lifting set
            rows.append((date, r["exercise_title"], weight, reps))
    return rows


def window(rows, end, days=7):
    start = end - timedelta(days=days)
    return [r for r in rows if start < r[0] <= end]


def score(w, reps):
    """Epley 1RM estimate. Ranks sets so bodyweight work (w=0) compares on reps."""
    return w * (1 + reps / 30) if w else reps / 30


def fmt_set(w, reps):
    return f"{w:g}kg x {reps}" if w else f"bodyweight x {reps}"


def summarize(rows):
    sets, tonnage, sessions = defaultdict(int), defaultdict(float), set()
    top = {}  # exercise -> (weight, reps) of the best set, by estimated 1RM
    unmapped = set()
    for date, ex, w, reps in rows:
        sessions.add(date.date())
        m = muscle_of(ex)
        if m:
            sets[m] += 1
            tonnage[m] += w * reps
        else:
            unmapped.add(ex)
        if ex not in top or score(w, reps) > score(*top[ex]):
            top[ex] = (w, reps)
    return sets, tonnage, sessions, top, unmapped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/strong_workouts.csv")
    ap.add_argument("--weeks-ago", type=int, default=0,
                    help="0 = week ending today, 1 = the week before that")
    args = ap.parse_args()

    path = Path(args.csv)
    if not path.exists():
        sys.exit(f"No log at {path}. Export from Strong -> Settings -> Export Data.")

    rows = load(path)
    if not rows:
        sys.exit(f"No working sets parsed from {path}.")

    latest = max(r[0] for r in rows)
    end = latest - timedelta(days=7 * args.weeks_ago)
    this_week, last_week = window(rows, end), window(rows, end - timedelta(days=7))

    cur_sets, cur_ton, cur_sess, cur_top, unmapped = summarize(this_week)
    prev_sets, _, prev_sess, prev_top, _ = summarize(last_week)

    print(f"# Weekly review — 7 days ending {end:%d %b %Y}")
    print(f"(latest logged session: {latest:%d %b %Y})\n")

    if not this_week:
        print("No sessions logged in this window.")
        return

    print(f"Sessions: {len(cur_sess)} (prev week: {len(prev_sess)})")
    print(f"Working sets: {sum(cur_sets.values())} (prev week: {sum(prev_sets.values())})\n")

    print("## Sets per muscle (this week vs last)")
    for m in sorted(set(cur_sets) | set(prev_sets), key=lambda x: -cur_sets.get(x, 0)):
        c, p = cur_sets.get(m, 0), prev_sets.get(m, 0)
        arrow = "=" if c == p else ("up" if c > p else "down")
        print(f"  {m:<12} {c:>3} sets  (prev {p:>3}, {arrow})  {cur_ton.get(m, 0):>7.0f} kg volume")

    print("\n## Load progression (best working set, this week vs last)")
    for ex, (w, reps) in sorted(cur_top.items()):
        if ex not in prev_top:
            note = "new/not trained last week"
        else:
            pw, preps = prev_top[ex]
            if score(w, reps) > score(pw, preps):
                note = f"UP from {fmt_set(pw, preps)}"
            elif score(w, reps) < score(pw, preps):
                note = f"DOWN from {fmt_set(pw, preps)}"
            else:
                note = "flat"
        print(f"  {ex:<34} {fmt_set(w, reps):<18} {note}")

    stalled = [ex for ex, (w, r) in cur_top.items()
               if ex in prev_top and (w, r) == prev_top[ex]]
    if stalled:
        print(f"\n## Flat for 2 weeks ({len(stalled)}): " + ", ".join(sorted(stalled)))

    if unmapped:
        print("\n## Unmapped exercises (not counted in muscle totals)")
        print("  " + ", ".join(sorted(unmapped)))
        print("  -> add a substring to MUSCLE_MAP in this script to count them.")


def selftest():
    assert muscle_of("Lateral Raise (Dumbbell)") == "side delts"
    assert muscle_of("Bent Over Row (Barbell)") == "upper back"
    assert muscle_of("Romanian Deadlift (Barbell)") == "hamstrings"
    assert muscle_of("Sled Push") is None, "unknown exercises must report as unmapped"

    # Bodyweight sets (weight blank -> 0) must rank on reps, not collapse to 0.
    d = datetime(2026, 7, 1)
    _, _, _, top, _ = summarize([(d, "Pull Up", 0, 5), (d, "Pull Up", 0, 8)])
    assert top["Pull Up"] == (0, 8), top
    assert fmt_set(0, 8) == "bodyweight x 8"

    # More reps at lighter weight can still be the better set.
    assert score(60, 12) > score(70, 5)
    # Heavier top set wins at equal reps.
    _, _, _, top, _ = summarize([(d, "Squat", 60, 5), (d, "Squat", 80, 5)])
    assert top["Squat"] == (80, 5)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
