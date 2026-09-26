# Vera Bot — magicpin AI Challenge submission

A deterministic message engine for **Vera**, magicpin's AI assistant for
merchant growth. Given a merchant's category, live merchant context, a
trigger, and (optionally) a customer, the bot decides the next outbound
message, its CTA, the send-as identity, a suppression key, and a rationale
— without calling any LLM.

## Approach

`compose(category, merchant, trigger, customer=None)` in `app/compose.py`
is a pure function:

1. **Grounding first.** The message always cites a concrete fact pulled
   from the trigger payload — a search-volume benchmark (`research`), a
   footfall/booking swing (`spike`/`dip`), a seasonal moment (`festival`),
   or a dormancy gap (`recall`). Nothing is invented.
2. **Real offer, not a generic one.** `_pick_offer` matches the merchant's
   live offers against the category's `offer_patterns`; if nothing matches
   it falls back to the merchant's first live offer rather than fabricating
   a price.
3. **Category voice.** Each of the 5 verticals (dentists, salons,
   restaurants, gyms, pharmacies) carries its own tone and an explicit
   "avoid" list (`dataset/categories/*.json`), so a dentist never gets
   restaurant-style urgency language.
4. **One CTA, one send.** Every composed action has exactly one call to
   action. `suppression_key` is a deterministic hash of
   `(merchant_id, trigger_id, category, offer_id)` so the same
   trigger/merchant/offer combination is never sent twice — re-firing a
   tick for an already-suppressed trigger is a no-op.
5. **Customer-aware suppression.** If a customer context is present and
   `consent` is `false`, or `status` is `inactive`, the action is
   suppressed (returns `None`) instead of composing a message.
6. **No model in the loop.** The composer is 100% rule-based Python. Model
   choice: none — determinism was the explicit constraint ("Your output
   should stay deterministic for the same input and simulator settings"),
   so an LLM call would only add latency and non-determinism risk for a
   task that a scoped rules engine handles exactly.

### Why not an LLM?

An LLM could produce more varied prose, but the rubric and FAQ are explicit
that grounding, determinism, and reliable execution are what's scored —
not stylistic variety. A rules engine guarantees every message is grounded
in the exact context it received and reproducible for scoring/replay. If
richer phrasing were required later, the same `compose()` contract could
be kept and only the message-string templating step swapped for an LLM
call, with the grounding/suppression logic unchanged.

## Endpoints (`app/main.py`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/context` | Idempotently store versioned context by `(scope, context_id)`. Re-posting the same version is a no-op; a higher version replaces atomically; a lower/equal version is ignored. |
| `POST` | `/v1/tick` | Simulated clock advance. Scans all known triggers, composes an action per un-suppressed trigger via `compose()`, caps output at 20 actions/tick, and marks each sent suppression key so it isn't repeated. |
| `POST` | `/v1/reply` | Classifies an inbound reply's intent (`accept` / `decline` / `objection` / `question` / `auto_reply` / `unclear`) via `app/intent.py` and returns the next reply, respecting the one-CTA-per-send rule. |
| `GET` | `/v1/healthz` | Liveness check. |
| `GET` | `/v1/metadata` | Bot name, version, determinism flag, endpoint list, tick action cap. |

State (`app/store.py`) is in-memory and thread-safe: versioned contexts,
per-merchant pending action (for reply correlation), and the suppression
set. This is intentionally simple — the brief only requires the bot to
"stay stateful" for the duration of a test session, not to survive a
process restart.

## Dataset (`dataset/`)

Matches the structure described in the challenge brief:

```
dataset/
├── categories/            # 5 verticals: dentists, salons, restaurants, gyms, pharmacies
├── merchants_seed.json    # 10 seeds -> expands to 50
├── customers_seed.json    # 15 seeds -> expands to 200
├── triggers_seed.json     # 25 seeds -> expands to 100
└── generate_dataset.py    # deterministic expansion + 30 canonical test pairs
```

Run the generator:

```bash
python3 dataset/generate_dataset.py --seed-dir dataset --out expanded
# -> expanded/  50 merchants · 200 customers · 100 triggers · 30 test pairs
```

The expansion is a pure function of seed index — no randomness — so every
run produces byte-identical output.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Load context, then tick:

```bash
curl -s localhost:8000/v1/context -H 'Content-Type: application/json' -d '{
  "scope": "category", "context_id": "dentists", "version": 1,
  "payload": {"category": "dentists", "voice": {"tone": "clinical"}, "offer_patterns": ["checkup_discount"]}
}'

curl -s localhost:8000/v1/context -H 'Content-Type: application/json' -d @dataset/merchants_seed.json
curl -s localhost:8000/v1/tick -H 'Content-Type: application/json' -d '{"tick_id": "tick_1"}'
```

## Tests

```bash
pip install pytest httpx
pytest tests/ -q
```

Covers: health/metadata, context idempotency + monotonic versioning, and a
full tick → compose → suppress → reply flow (including that a repeated
tick does not re-send an already-composed trigger).

## Deployment

The submission requires **one public base URL** exposing all five
endpoints. Containerize with the included `Dockerfile` and deploy to any
cloud provider (Render, Railway, Fly.io, a VM, etc.):

```bash
docker build -t vera-bot .
docker run -p 8000:8000 vera-bot
```

Point the challenge's submission form's **Submission URL** field at the
deployed base URL (e.g. `https://your-bot.example.com`) — this repository
does not itself host a public endpoint.

## Known tradeoffs

- **In-memory state only** — simplest correct implementation for a
  session-scoped test harness; would move to Redis/Postgres for a
  production merchant base.
- **Keyword-based intent classifier** — deterministic and fast, but
  coarser than an NLU model on ambiguous phrasing; acceptable given the
  same determinism constraint that rules out an LLM in `compose()`.
- **Offer selection is first-tag-match** — a merchant with multiple
  offers matching a category's patterns always gets the first match;
  ranking by margin/relevance is a natural next step.
