---
name: weekly-checkin
description: Use when the user says "weekly checkin", "/weekly-checkin", "weekly review", "how was my week", "check in on my training", or wants to review the last week of training and adjust the program. Runs a short cited review against their Strong log.
---

# Weekly Check-in (Jeff Nippard)

A five-minute coaching conversation, once a week. **Read the data first, then ask only what the data
can't tell you.** Ends with at most three concrete changes for next week, appended to the log.

**Core principle:** never ask what the CSV already knows. It knows sets, loads, reps, and frequency.
It does *not* know sleep, stress, joint pain, or how hard a set actually felt — Aman's RPE column is
blank, so **effort is always inferred, and you say so out loud.**

## Step 1 — Run the numbers (before you say anything)

```bash
python3 scripts/weekly_review.py
```

If they're reviewing an older week: `--weeks-ago 1`. Missing CSV → tell them to re-export from
Strong (*Settings → Export Data*) into `data/strong_workouts.csv`. **The export is a snapshot — if
the latest logged session is more than a week old, they forgot to re-export. Say so before
interpreting anything, or you'll review stale data as if it were this week.**

Read `private/protocol.md` for the targets you're scoring against, and `private/weekly-log.md` (if
it exists) for what you asked them to change last week — **closing that loop is the point of the
whole ritual.**

## Step 2 — Open with the one thing that stands out

One or two sentences, not a report dump. Lead with the single most important signal:
a muscle that fell below its protocol target, a lift that's been flat two weeks, a missed session.
They can read the table themselves — you're here to say what it *means*.

## Step 3 — Ask 3–5 questions, ONE at a time

Pick only questions the data raised. Never a fixed form, never a list dump. Good ones:

- Something flat/down two weeks → "Squat's been at the same weight three weeks. Fatigue, or is it
  form breaking down at the top set?"
- Volume dropped → "Only 3 sets for side delts this week vs 9 last. Skipped, or cut short?"
- A missed session → "Two sessions instead of four. What got in the way?"
- Always, once → sleep/stress this week, and any joint or tendon complaints.
- Always, last → "Which session felt hardest, and did any set genuinely get close to failure?"
  (This is your only read on effort. Their RPE is unlogged.)

Wait for each answer. React to it before moving on.

## Step 4 — At most three changes, cited

Give **three changes maximum** — a lifter who gets ten changes makes zero. For each: what to change,
and why, cited from the notebook.

**Every substantive Jeff claim MUST be backed by a real source returned this session** via:

```bash
python3 scripts/ask_cited.py "your question"
```

Cite as `📎 *Source: "Exact Title Returned"*` on its own line. **If the helper returns no relevant
source, drop the claim** — say "the notebook doesn't cover this directly" and coach it plainly
instead. Never cite a title from memory. Jeff/training only — no Huberman, no studies.

Constraint coaching (their sleep, their schedule, an angry elbow) needs no citation — just don't
dress it up as Jeff's view.

## Step 5 — Append to the log

Append to `private/weekly-log.md` (create if missing) so next week can score the loop:

```markdown
## Week ending 14 Aug 2026
**Data:** 4 sessions · 68 working sets · side delts 6 (target 10-20) · bench flat 2 weeks
**Reported:** sleep ~5h Tue-Thu, work crunch · left elbow tender on curls
**Changes for next week:**
1. Add 3 sets lateral raises to Pull day — side delts at 6 sets, under the 10-20 target 📎 *Source: "..."*
2. Bench: drop to 2 RIR and add a rep before adding load
3. Curls → supinated EZ bar only until the elbow settles (constraint, uncited)
**Last week's changes:** #1 done, #2 partially, #3 skipped
```

Keep it short — this file is read every week, so it must stay skimmable.

`private/` is gitignored. Never commit it, never paste it anywhere public.

## Red flags — STOP

- Typing a 📎 for a title `ask_cited.py` didn't return this session.
- Asking about volume/loads/frequency — **the script already told you.** Ask what it can't see.
- Handing over more than three changes.
- Treating a stale export as this week's training.
- Stating effort as fact. RPE is blank. It is always inferred.

## Common mistakes

| Mistake | Fix |
|--------|-----|
| Dumping the script output at them | Lead with the one thing that matters. |
| Asking all questions at once | One at a time. It's a conversation. |
| Ignoring last week's changes | Score them first — that's the improvement loop. |
| Ten adjustments | Three, max. |
| Citing from memory | Only what `ask_cited.py` returned this session. |
