# Design document

The rendered version lives here, and it is the one to show people:

**https://claude.ai/code/artifact/08f8fdbb-2d20-40e9-b6c7-b86a76b426db**

Source: [`docs/design.html`](design.html). Republish that file to update the
page; the URL stays the same.

This file is deliberately **not** a second copy of it. Two documents saying
the same thing drift apart — which is exactly what happened to `workflow.mmd`,
one file shared by two trees until it was wrong about both. What lives here
instead is the part worth having in plain text: the register of what was
tried and abandoned, and the technology choices, both of which people will
grep for long after they stop opening a web page.

Diagrams live in `workflow.mmd` in each tree (`docs/workflow.mmd` here,
`workflow.mmd` at the deployed tree's root) — **one per tree**, because the
prototype and the production build genuinely differ in provider and storage.

---

## Tried and dropped

Every item was working code. Keeping the reasons is the cheapest way to stop
the next person — or the next model — spending a day rediscovering them.

### Azure App Registration — policy wall, abandoned entirely
Three distinct errors across multiple attempts: `AADSTS50020` (tenant mismatch),
`AADSTS50058` (silent sign-in), then the real blocker — *"the ability to create
applications outside of a directory has been deprecated."* Microsoft removed app
registration for any account without its own Azure AD directory, which every
personal account tested falls into. Not account-specific and not fixable.
**Replaced by rclone**, which ships a pre-registered Microsoft-approved OAuth app.

### Folder-name matching — failed, replaced
Matched the Model Code against the OneDrive folder name. Real leaf folders are
named by colour and VIN, not model number, and depth is inconsistent between
brands (Toyota carries a level Kia does not). **Replaced by** searching for the
spec-sheet PDF by exact filename, since it sits alongside the photos.

### Models evaluated and ruled out
- **GPT-4o** — deprecated, 404 since 16 Feb 2026.
- **Gemini 1.5 Flash/Pro** — fully retired, 404 from Google's API.
- **Llama 3.3 70B** — deprecated by Groq June 2026, and text-only before that.
- **Mistral Pixtral** — no free tier anywhere, Mistral's own API or OpenRouter.
- **Groq Qwen3.6-27B** — fast on LPU hardware but capped at 3 images per request;
  a real spec sheet is 15–20 pages.
- **DeepInfra** — the original provider, abandoned early; reason not preserved,
  only a stale API key left in dead test code.

Every deprecation was checked live rather than recalled — the model landscape
moves faster than training data.

### One call for extraction + matching — unreliable
Asking the model to extract data *and* map it to a fixed checkbox vocabulary in a
single call proved unreliable. This is the documented reason the pipeline is two
separate passes.

### Removing the strict-schema-first attempt — considered, rejected
Would save a wasted call on today's flaky free model. But the strict-schema path
is the one that succeeds in a single call on any well-behaved paid model —
removing it trades a temporary problem for a permanent one.

### Prose instead of {label, value} for raw_specifications — rejected
Would make the matching step re-derive structure it already had, and lose
precision on rows carrying two numbers for two trims. It also did not address the
bug that prompted it.

### CrewAI and n8n for orchestration — evaluated, not chosen
- **CrewAI** is built for agents that dynamically negotiate tasks; this pipeline
  is a fixed known sequence, so the core value goes unused while the abstraction
  still costs control.
- **n8n** is a separate self-hosted visual workflow server — another service to
  run and secure, with workflows as exported JSON rather than testable code.

### Engineering bugs worth remembering
1. **Event-loop serialisation** — FastAPI endpoints called blocking functions
   directly from `async def` handlers, freezing the single event loop and
   serialising every request server-side even though the frontend fired them
   concurrently. Proven from real log timestamps. Fixed with `run_in_threadpool`.
2. **Trace-context propagation** — concurrent calls inside a `ThreadPoolExecutor`
   each appeared as disconnected top-level traces, because `submit()` does not
   carry the calling thread's `contextvars.Context`. Fixed by snapshotting and
   re-entering the context per task.
3. **CSS comment terminator** — a comment containing the literal `.sidebar2*/`
   was read as an early comment-close, silently dropping the next rule. Diagnosed
   by inspecting the parsed CSSOM rather than guessing from screenshots.
4. **Unwrapped media query** — a 2-column grid rule had lost its `@media` wrapper
   and applied at every width.
5. **"Doubled thumbnails"** — investigated, proved a false alarm from a duplicate
   click during manual testing. Recorded because "we checked and it was nothing"
   is worth knowing too.

### Batch photo downloads — 6× speedup
24 individual `rclone cat` calls took ~6 minutes per car; one batched
`rclone copy` does it in ~1.

### `app/onedrive.py` — removed
A complete rclone-based OneDrive integration, verified working against the
real account and folder tree. Dropped for a reason unrelated to whether it
worked: running it from a company laptop meant authenticating against a
personal Microsoft account. Replaced by reading a folder already synced by
IT-approved means, which also took a network dependency off the hot path.

### `public/filename-parser.js` — moved server-side
Parsed the Model Code out of an uploaded PDF's filename in the browser,
before any API call. Free and reliable. It stopped making sense when
auto-fill replaced manual upload — there is no longer a file for the browser
to parse a name from. The logic now runs server-side in `app/model_code.py`.

### Per-group feature matching — wrong question
The first design asked the model which section each feature belonged to and
matched within each bucket separately. That forced a decision the model
should never have been making: which section a feature appears in is a
page-layout fact, not a fact about the car. One flat vocabulary can only ever
match more, never less.

### Strict JSON schema on the folder call — disabled on the free tier
The right design, and on in production. On the free provider it failed *every
single time* across a full session of logs — a 100% failure rate, not a
flake — before recovering via the plain-language fallback. Skipping straight
to the fallback saves a wasted round trip on every ambiguous level.
`FOLDER_NAV_STRICT_SCHEMA_ENABLED` re-enables it.

### Web-search fallback (Tavily) — off by default
Built to answer what the Model Code cannot: a folder level split by body
type, when the code carries no body-type field. A cached full-path index made
it largely redundant, since the correct folder wins on brand and model tokens
without that branch being resolved as its own question. It also sends real
folder names to a third party. Kept, disabled.

### `seed_db.py` — deleted, destructive
Reset the database to a known state by truncating the live file. Cost real
listing data twice. Its baseline survives as `sample_listings.py`, data-only,
with no entry point that can write anything.

### `app/agent_graph.py` — built, deliberately not wired in
A graph-based orchestration of the same pipeline functions. It works and is
deliberately off the request path; the direct concurrent flow is simpler and
already tested. It earns its place the day the pipeline needs to resume
across steps in a queued deployment, and not before.

### Inventing dropdown options — actively harmful
When an extracted value was not in a dropdown, the form used to add it and
select it. That looked helpful and was worse than failing: the admin panel's
lists are closed — you may select an existing entry, never add one — so the
reviewer saw a filled field for a value that did not exist in the real
system, and moved on. An unmatched value now leaves the field blank and flags
it.

### Diagnosing truncation as model unreliability — the expensive one
Extraction kept failing with `invalid JSON`, and the error text blamed
provider flakiness and advised a retry. It was neither. The model was being
cut off at its output limit mid-string — `completion=7996` against an 8000
cap, and before that `3996` against 4000. The cap had already been raised
once for exactly this reason without anyone noticing the pattern.

The signal that would have identified it in seconds — the model's own stop
reason — was being read and discarded. A retry could never have helped: the
failure is deterministic. One car burned ten minutes retrying a second copy
of the same sheet into the same wall.

Now: detected, repaired where possible, and flagged in
`low_confidence_fields`. See `app/recovery.py`.

---

## Technology choices

| Decision | Chosen | Rejected, and why |
|---|---|---|
| Reading spec sheets | Vision model on rendered pages | Text extraction — the sheets are scanned, there is no text layer. Photo pages are dropped by white-pixel fraction: spec tables score 0.79–0.98, studio photos 0.19–0.36, regardless of paint colour. |
| Sorting photos | Local YOLO classifier | A vision-model call per photo. Benchmarked at 15 ms/photo against seconds and a per-photo cost; 5/5 correct on real dealer photos at 99.9–100% confidence, including hard crops. Runs with every provider down. |
| Matching to the form | Token overlap first, model on the remainder | Model-only. At a real level with two options the target was a near-exact match for the first; the model returned `NONE_MATCH` on both attempts and the branch was abandoned as a false dead end. Keyword overlap resolves it instantly and for free. |
| Talking to Claude | Native `anthropic` SDK | The OpenAI-compatible shim — cannot express prompt caching, streaming at a large output budget, or a faithful stop reason. That last one hid the truncation bug. |
| Reaching documents | SharePoint via Graph, `Sites.Selected` | Tenant-wide read. Scoping is the actual control; the code's path checks are defence in depth. Tenant-wide turns any navigation bug into a company-wide exposure. |
| Handling model answers | Enumerated list, membership re-checked | Trusting free text. The model never authors a path or invents an ID. |

Tuned values that should not be re-guessed without re-testing against a real
folder tree — both were set against real failures, and the reasoning is in
`app/config.py` beside each one:

- `FOLDER_NAV_DECISIVE_SCORE_FLOOR = 0.35`
- `FOLDER_NAV_DECISIVE_MARGIN = 0.10` — lowered from 0.15 after a real 404
  where the correct answer led 0.486 to 0.364 and the 0.122 margin fell just
  short.

---

## Known documentation debt

Found while writing this, not fixed here — a different genre of work:

- **`README.md` has a hard cutoff at 24 Aug, 14:54.** Everything built after
  that (`taxonomy.py`, `recovery.py`, the `/resolve-*` endpoints, the frontend
  changes) is absent. It documents a deleted file, two routes that do not
  exist, calls photo automation "not built yet", and omits 12 live endpoints.
- **`API.md` in the deployed tree omits all three `/resolve-*` endpoints** —
  in the one document written for Legend's dev team, and they are exactly
  what that team needs to map onto their dropdowns.
- **Nothing addresses the reviewer.** Every `.md` in both trees speaks to a
  developer, a deployer, or an integrator. There is no guide for the person
  who actually lists the cars, which is odd given that is the stated point.
