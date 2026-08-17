# Spec: Standalone local coach app

**Status:** implemented, pack extraction in progress
**Labels intended:** `ready-for-agent` (not applied — see Further Notes)

## Problem Statement

The coach only exists inside a Claude Code session.
To ask it anything — whether an aching elbow should change tomorrow's session, whether a stalled bench means fatigue or a bad rep target — the user has to open a terminal, start Claude Code in this specific repo, and run a slash command.
That friction means the weekly check-in gets skipped, which is exactly the ritual that drives improvement.

The user wants their own chat interface: open it, type, get a coached answer grounded in their real training data.

The hard constraint is that this coach's value is **citation integrity**.
Every substantive Jeff Nippard claim must trace to the NotebookLM notebook.
A chat app that answers from a general-purpose model's memory is not a cheaper version of this coach — it is a different, untrustworthy product that invents plausible-sounding video titles.

Two facts make that constraint difficult outside Claude Code:

- NotebookLM has no public API. Citations come from `nlm`, an unofficial CLI driving NotebookLM through browser session cookies that expire roughly every 20 minutes.
- The Gemini API cannot substitute. Gemini has never read the notebook's 302 sources and will fabricate titles when asked to cite them.

## Solution

A local web app the user opens in a browser on their own machine.
It presents a chat box for free-form questions and a button that runs the structured weekly check-in.

Citation integrity is preserved by separating *when* citations are fetched from *when* they are used:

- **Once**, while `nlm` is authenticated, a build step harvests real answers, real source titles, and verbatim quotes for 44 core coaching questions into a knowledge pack on disk.
- **Forever after**, the app retrieves from that pack offline. No login, no expiry, no network dependency for citations.

The app composes replies from retrieved passages plus the user's own training data, and enforces citation honesty mechanically rather than trusting the model to behave.

By default everything runs locally through Ollama, so training logs, body stats, and injuries never leave the machine.

## User Stories

1. As a lifter, I want to open a chat window and ask my coach a question, so that I do not have to start a Claude Code session to get advice.
2. As a lifter, I want the coach to answer in the voice of someone who has studied Jeff Nippard's work, so that the advice is consistent with the program I already follow.
3. As a lifter, I want every Jeff claim to carry a real source title, so that I can verify the advice rather than trust it blindly.
4. As a lifter, I want to see the notebook's actual words quoted, so that I can judge whether the claim matches the source.
5. As a skeptical user, I want invented source titles removed automatically, so that a small local model cannot fabricate authority.
6. As a skeptical user, I want to be told when citations were stripped, so that I know the model attempted to overreach.
7. As a lifter, I want the coach to say plainly when the notebook does not cover something, so that silence is not mistaken for endorsement.
8. As a lifter, I want uncited coaching about my own constraints to be clearly separate from cited Jeff claims, so that I know which is which.
9. As a lifter, I want to press one button to start my weekly check-in, so that the ritual requires no recall of a command.
10. As a lifter, I want the check-in to open by naming the single most important thing in my data, so that I am not handed a table to interpret myself.
11. As a lifter, I want the check-in to ask one question at a time, so that it feels like a conversation and not a form.
12. As a lifter, I want the coach to ask only what my training log cannot answer, so that my time is spent on sleep, stress, joints, and effort.
13. As a lifter whose RPE column is empty, I want effort described as inferred rather than measured, so that I am not misled about what the data knows.
14. As a lifter, I want at most three concrete changes per check-in, so that I actually do them.
15. As a lifter, I want to save a check-in to my weekly log, so that next week can score whether I followed through.
16. As a lifter, I want the coach to read my previous weekly log, so that the loop closes instead of restarting each week.
17. As a lifter, I want the coach to know my current sets per muscle, so that advice reflects what I actually trained.
18. As a lifter, I want the coach to know which lifts have been flat, so that it can address stalls specifically.
19. As a lifter, I want the coach to know how many sessions I hit, so that a missed week is acknowledged.
20. As a lifter, I want bodyweight exercises tracked by reps, so that pull-up progress is visible rather than recorded as zero.
21. As a lifter, I want to be warned when my exported data is stale, so that I do not review month-old training as if it were this week.
22. As a cutting lifter, I want the coach to track my calorie and protein targets, so that nutrition and training are coached together.
23. As a cutting lifter, I want to be shown the notebook's cited intake figures when they conflict with my chosen targets, so that I can decide with evidence in front of me.
24. As a cutting lifter, I want my own decision respected after I have seen the evidence, so that the coach advises rather than overrides.
25. As a cutting lifter, I want to know when falling strength suggests I am cutting too aggressively, so that I can protect muscle.
26. As a privacy-conscious user, I want the app to run entirely on my machine by default, so that my health data never leaves it.
27. As a privacy-conscious user, I want an explicit warning before any data goes to a third-party model, so that the trade-off is a choice and not a default.
28. As a privacy-conscious user, I want the warning visible in the interface while the remote backend is active, so that I cannot forget mid-session.
29. As a user wanting better prose, I want to switch to a stronger remote model with one environment variable, so that I can trade privacy for quality deliberately.
30. As a user, I want the app to tell me which backend and model are active, so that I always know what is answering me.
31. As a user, I want a clear error when the local model is not running, so that I can start it rather than guess.
32. As a user building the knowledge pack, I want progress reported per question, so that I know a long extraction is advancing.
33. As a user building the knowledge pack, I want every answer saved immediately, so that an authentication expiry costs me nothing.
34. As a user building the knowledge pack, I want re-running the build to resume where it stopped, so that I never redo completed work.
35. As a user building the knowledge pack, I want to be told plainly when authentication expired and what command fixes it, so that I am not left diagnosing.
36. As a user, I want to check how complete my pack is at any time, so that I know whether it is ready to use.
37. As a user, I want to re-ask only the questions that returned no usable source, so that gaps can be filled without a full rebuild.
38. As a user, I want the pack to cover both training and cutting, so that one coach handles the whole goal.
39. As a user, I want the app to refuse to start without a knowledge pack, so that I never receive uncited coaching by accident.
40. As a user, I want embeddings cached after the first run, so that startup is fast on every subsequent launch.
41. As a user, I want the interface to work in both light and dark themes, so that it matches my system.
42. As a user, I want the chat readable on a narrow window, so that I can keep it open beside my training log.
43. As a user, I want the model's internal reasoning hidden, so that I read coaching rather than a monologue.
44. As a user, I want my private data excluded from version control, so that publishing this repo never leaks my health information.
45. As a returning user, I want documentation explaining why the design works this way, so that I can maintain it months later.
46. As a returning user, I want a troubleshooting table for the failure modes I will actually hit, so that I can fix them without re-deriving the cause.

## Implementation Decisions

**Two components, deliberately separated by time.**
Citation extraction and citation use are decoupled because their dependencies differ: extraction needs a live authenticated `nlm` session, use needs nothing but local files. This separation is the core architectural decision and everything else follows from it.

**The knowledge pack is the trust boundary.**
A JSON document holding, per question: topic, question, the notebook's answer, and a list of sources each carrying a real title and verbatim cited text. Entries lacking cited text are excluded at load time. The set of titles in the pack forms the allowlist against which all model output is validated. Stored under the private, gitignored directory.

**Extraction checkpoints after every single answer.**
Authentication can expire between any two queries, so the pack is rewritten to disk after each success. Reruns diff the question list against completed entries and fetch only the remainder. Auth failures are detected and reported with the exact remedy; other per-question failures are skipped without aborting the run. A status mode reports completion, and a retry mode re-asks only entries that produced no usable citation.

**Question set covers training and the cut**, organised by topic key so retrieval can bias on it: volume, reps, effort, exercise selection, progression, frequency, recovery, technique, injury, and cutting. Questions live in the extraction module rather than external configuration — editing a list is simpler than parsing one.

**Retrieval is local embedding similarity.**
Pack entries are embedded once with a local embedding model and cached beside the pack, keyed by question list and model name so the cache invalidates when either changes. Queries embed the user's message and rank entries by cosine similarity, taking the top handful. The check-in supplies its own retrieval query describing review topics, rather than embedding its instruction text, which would retrieve poorly.

**Generation is backend-swappable behind one function.**
A single call site selects between the local model and the remote API based on an environment variable, so both paths share one prompt, one retrieval result, and one enforcement step. The remote path translates the shared message list into that provider's request shape, including system instructions. Failures on either backend return a readable message naming the likely fix rather than raising.

**Citation enforcement is mechanical, not prompted.**
The system prompt instructs the model to cite only from supplied sources using a bracketed title convention, but compliance is not assumed. Every emitted citation marker is matched against the pack's allowlist; unrecognised titles are removed from the text before display and returned separately so the interface can report the count. This is the decisive safeguard, because a small local model follows instructions less reliably than a frontier model.

**User context is assembled per request** from three sources: a live run of the existing weekly analysis, an excerpt of the protocol document, and recent weekly log entries. Current cut targets are injected explicitly with a note that they override the protocol's cited figures, instructing the coach to surface the cited number on conflict rather than silently adopting or overriding either.

**The weekly check-in is a prompt, not a separate code path.**
Pressing the button clears conversation history and sends a kickoff instruction describing the ritual's first two steps: name the single most important signal, then ask exactly one question. Subsequent turns are ordinary chat, so the conversation continues naturally.

**Log writing is user-initiated.**
Each coach reply carries an action that appends that reply to the weekly log, creating the file with a header if absent. The model never writes to disk on its own; the user decides what is worth keeping.

**The server is standard-library only.**
A single request handler serves one HTML page, an info endpoint reporting backend and pack size, a chat endpoint, and a save endpoint. No framework, no build step, no dependency beyond what local inference requires. It binds to loopback only. Conversation history is process-global and in memory, which is correct for a single local user and is trimmed to recent turns before each request.

**The interface is one inline HTML string** with theme-aware tokens defined for light and dark, citation markers rendered as visually distinct quote blocks separate from prose, and the remote-backend warning shown as a persistent banner. Model reasoning blocks are stripped from replies before display.

**Existing modules are reused unchanged.** The weekly analysis script and the cited-query helper are invoked as subprocesses rather than reimplemented or refactored.

## Testing Decisions

**What makes a good test here:** it asserts externally observable behaviour — what a user sees or what lands on disk — and never reaches into private helpers or asserts on prompt wording. Tests must not require a running inference server, a built knowledge pack, or network access, so that they are fast and deterministic. Model output is nondeterministic and therefore stubbed; what is tested is the machinery around it.

**One seam, at the HTTP boundary.** Tests start the real server on an ephemeral loopback port with generation and embedding replaced by deterministic stubs, then exercise it over real HTTP. This single seam covers routing, mode selection, citation enforcement, response contracts, and log writing together, and mirrors exactly how the user reaches the code. Introducing separate seams around retrieval and generation was considered and rejected as more brittle for no additional coverage.

**Behaviour to cover at that seam:**
- A chat request returns a reply, and a recognised citation survives to the response.
- A model reply containing an unknown title has it removed, with the stripped title reported.
- Check-in mode returns a reply without requiring a user message and resets prior history.
- The save endpoint appends to the weekly log, creates the file when missing, and rejects empty input.
- The info endpoint reports the active backend and pack size.
- An unreachable model yields a readable error rather than a stack trace or hang.

**Pure functions remain directly unit-tested**, as they are today: citation stripping including the no-citation and all-valid cases, cosine similarity including the zero-vector case, exercise-to-muscle mapping including the deliberately unmapped case, and best-set ranking including bodyweight sets where weight is absent and reps decide.

**Extraction is tested for its resumption contract** with a stubbed query function: a completed question is not re-asked, an authentication failure stops the run with a nonzero exit while preserving prior answers, and a non-auth failure skips one question without aborting.

**Prior art:** the repository's existing convention is an assert-based self-check invoked by a flag on the module itself, with no test framework or fixtures. New tests follow that convention rather than introducing a runner.

## Out of Scope

- Remote or hosted deployment, and therefore phone access away from the machine. The app is loopback-only by deliberate choice.
- Authentication, user accounts, and multi-user support. Single local user is assumed throughout.
- Live NotebookLM querying from the app. This is the central constraint, not an omission.
- Automatic pack refresh or scheduling. Rebuilds are manual and occasional.
- Streaming responses. Replies arrive whole.
- Persistent conversation history across restarts.
- Automatic ingestion of new Strong exports; the user replaces the file.
- Nutrition logging or meal tracking. The coach reasons about targets, it does not record intake.
- Editing the protocol document from the app.
- Changing the existing Claude Code skills, which continue to work as before.

## Further Notes

**This spec was not published to an issue tracker, deliberately.** The repository's `origin` points at a third party's GitHub repository where the current user holds read-only permission, so issue creation would fail. More importantly, publishing would expose body composition, injury history, and cut targets on a public repository the user does not control — directly contrary to this project's stated rule that personal data stays private and gitignored. The `ready-for-agent` label also does not exist in that repository's label set. Publishing destination is left to the user.

**Status at time of writing:** both components are implemented and the application has been run successfully end to end against a temporary pack. The real knowledge pack extraction is in progress; entries returned so far carry genuine source titles and verbatim quotes. The application refuses to start until a pack exists, so no uncited coaching is possible in the interim.

**A nutrition conflict is encoded intentionally.** The user's current targets sit substantially below the protocol's cited figures, both in calories and protein. Rather than resolve this in code, the system surfaces the cited figure whenever the topic arises and leaves the decision with the user. The extraction question set includes protein intake while cutting specifically so a real source is available for that conversation.

**Known limitations accepted by design:** the pack is a snapshot and questions outside its topics receive an explicit not-covered response; the local model is a weaker writer than a frontier model and is kept honest by retrieval and title-stripping rather than its own judgment; the app is unavailable when the machine is off.
