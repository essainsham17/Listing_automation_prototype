# Where we left off — 2026-08-24

Everything below is committed to disk and verified. Nothing is half-finished.

## State: clean

- **`pytest -q` -> 221 passed, 0 failed.** (Was 136 passed / 4 failed.)
- **`pytest` is no longer destructive** — it leaves `data/listings.json` byte-for-byte
  identical. No need to back up before a routine run any more.
- Your 7 listings are intact and verified.

**Backup of your data before I touched anything** (this session's scratchpad):
`.../scratchpad/pre_port_backup/` — `data/`, plus the original
`extract.py`, `db.py`, `main.py`, `config.py`.

## What changed in this folder today

**Taxonomy resolution (new)** — `app/taxonomy.py`, catalogs in `data/reference/`,
endpoints `/resolve-taxonomy`, `/resolve-features`, `/resolve-specifications`.
All deterministic, no LLM calls. Maps our extracted text onto the real admin
panel's entry IDs, and never invents one.

**Lexus truncation fix** — `app/recovery.py` + `extract.py`. The failure was the
output cap, not model unreliability (`completion=7996` against an 8000 cap).
Now detected, repaired, flagged in `low_confidence_fields`, and the futile
second-PDF retry is skipped. On the free tier recovery is the *only* fix — gemma's
own ceiling is the binding constraint, so raising the cap can't help.

**Atomic DB writes** — `app/db.py`. temp → fsync → `os.replace`, rolling backups,
recovery from a corrupt file. Same API. (Adjacent to what you asked for; included
because the truncate-then-write pattern has cost you listings twice. Easy to
revert — the original is in the backup above.)

**Frontend** — feature search on all 5 groups (display-only filtering, so the AI
vocabulary is unaffected); `setSelectValue` no longer invents dropdown options;
`public/taxonomy.json` generated from your real stock (6 brands, 16 models).

## Open threads, in priority order

1. **Full exports to replace my transcriptions.** `data/reference/*.sample.json`
   are read off screenshots — ~104 of 107 spec values, 28 of 115 features. The
   wheel-size mistake showed why real data beats my reading of a paginated table.
2. **`taxonomy.json` is derived from your stock codes, not their tables** — names
   read as `LX7VIP`, `LC300`. Replace wholesale when their export arrives.
3. **Single-provider matching is unverified** in the deployed build (prototype
   still uses the free pair, correctly).
4. **Dev-team meeting** — `../listing_automation_deployed/API.md` is written for
   them; response shapes are marked provisional.

## Things not to forget

- The prototype stays on **free models** — your Anthropic key authenticates but
  has **no credits** ("credit balance is too low"). A Max/Pro subscription does
  not fund the API; that's separate billing.
- `listing_automation_deployed/` is the production build, kept aside. Not the
  thing being iterated on.


## Fixed on 2026-08-24 (after the network break)

**The 4 stale `test_stock_sync.py` failures — resolved, plus the destructive
behaviour behind them.**

Three separate faults, all in the test file rather than the product code:

1. It wrote to the LIVE DB as setup, so every `pytest` run replaced real
   listings with the seed. Both files now set `DB_PATH` to a temp path
   *before* importing `app.db` (which reads it at import time — setting it
   after would silently leave the module pointing at the real file).
2. Hardcoded POSIX `/tmp/`, which resolves to a non-existent `\tmp` on
   Windows. Now `tmp_path` / `tempfile`.
3. Assertions named cars deleted long ago (Kia Seltos, Nissan Patrol, Honda
   Civic, Land Cruiser GXR/VXR). Expectations now DERIVE from
   `sample_listings.LISTINGS` instead of restating it, so a future seed
   change cannot strand them the same way again.

Coverage also widened while rewriting: idempotency of `apply_hidden`, the
dry-run guarantee, missing-column errors, `updated_at` stamping, the restock
bucket, and a guard test that fails loudly if anyone reintroduces a direct
`save_listings()` against the real path.
