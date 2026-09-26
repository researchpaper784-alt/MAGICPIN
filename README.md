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
   `(merchant_id, trigger_id, category, offer_id, customer_id)` so the same
   trigger/merchant/offer/customer combination is never sent twice —
   re-firing a tick for an already-suppressed trigger is a no-op.
5. **Customer-aware suppression.** If a customer context is present and
   `consent` is `false`, or `status` is `inactive`, the action is
   suppressed (returns `None`) instead of composing a message.
6. **Prior-conversation personalization.** A separate `topic_key`
   (merchant + category + offer + customer, independent of which trigger
   fired) lets the bot recognize when a *new* trigger concerns an offer it
   already reached out about, and frame the message as "Following up —"
   instead of a cold open — this is the rubric's "prior conversation
   behavior" dimension.
7. **No model in the loop.** The composer is 100% rule-based Python. Model
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
| `POST` | `/v1/context` | Idempotently store versioned context by `(scope, context_id)` for scopes `category`/`merchant`/`customer`/`trigger`/`digest`. Re-posting the same version is a no-op; a higher version replaces atomically; a lower/equal version is ignored. Rejects payloads over 500KB with `413`. |
| `POST` | `/v1/tick` | Simulated clock advance. Merges any mid-test `digest` update into the merchant context, scans all known triggers, composes an action per un-suppressed trigger via `compose()`, caps output at 20 actions/tick, and marks each sent suppression key so it isn't repeated. |
| `POST` | `/v1/reply` | Classifies an inbound reply's intent (`accept` / `decline` / `objection` / `question` / `hostile` / `off_topic` / `auto_reply` / `unclear`) via `app/intent.py` and returns the next reply, respecting the one-CTA-per-send rule. Objections get a cheaper alternate offer when the merchant has one. |
| `GET` | `/v1/healthz` | Liveness check. |
| `GET` | `/v1/metadata` | Bot name, version, determinism flag, endpoint list, supported scopes, and all technical limits (tick cap, payload cap, timeout). |

State (`app/store.py`) is in-memory and thread-safe: versioned contexts,
per-merchant pending action (for reply correlation), a suppression set,
and a per-merchant sent-message history (for the follow-up personalization
above). This is intentionally simple — the brief only requires the bot to
"stay stateful" for the duration of a test session, not to survive a
process restart.

### Handling mid-test adaptive injection

The judge harness pushes fresh digest items, metric shifts, new triggers,
and surprise customer scopes *after* submission (Package → "Adaptive
injection"). The bot handles each independently of the 30 canonical pairs:

- **New/changed merchant facts** → post `scope: "digest"` with
  `context_id` = the merchant id; `/v1/tick` merges it over the merchant's
  `performance`/`offers`/`identity` before composing, so a changed price or
  metric is reflected on the very next tick.
- **New triggers** → any trigger context posted before a tick is picked up
  automatically; nothing needs to be pre-registered.
- **Surprise customer scopes** → posting a `customer` context the bot has
  never seen works the same as one seeded at warmup — `consent`/`status`
  are honored the moment the context lands.
- **Hostile / off-topic replay input** → classified explicitly (see intent
  list above) instead of falling through to a generic "unclear" reply.

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

Covers (13 tests): health/metadata, unknown-scope rejection, context
idempotency + monotonic versioning, the 500KB payload cap, a full tick →
compose → suppress → reply flow (including that a repeated tick does not
re-send an already-composed trigger), the 20-actions-per-tick cap under
load, consent-based suppression, a mid-test digest override changing the
offer a merchant sends, hostile and off-topic reply handling, an
objection reply falling back to a cheaper alternate offer, and the
follow-up framing triggered by a repeat topic.

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

## Competition requirements checklist

Everything the challenge brief (`Challenge` / `Rubric` / `Testing` /
`Package` / `Submit` / `FAQ` pages) asks for, and where it's satisfied:

| Requirement | Status |
|---|---|
| `POST /v1/context`, `/v1/tick`, `/v1/reply`, `GET /v1/healthz`, `/v1/metadata` all live on one public URL | `app/main.py` — all 5 implemented |
| Deterministic `compose(category, merchant, trigger, customer?)` returning message, CTA, send-as, suppression key, rationale | `app/compose.py` |
| Context stored idempotently by scope + version; re-post same version = no-op; higher version replaces atomically | `app/store.py:upsert_context` + `test_context_idempotent_and_monotonic` |
| Use real numbers/offers/dates/local facts, no fake claims | `_benchmark_fact` / `_pick_offer` read only from given context, never invent values |
| Category voice + avoid-list respected per vertical | `dataset/categories/*.json`, consumed in `compose()` |
| Personalize to merchant metrics, offer catalog, and prior conversation behavior | `_proof_point` (rating), `_pick_offer` (catalog), `_history_prefix`/`topic_key` (conversation history) |
| One clear CTA per send | every `compose()` result has exactly one `cta` string |
| 30-second response timeout | no blocking I/O or network calls in the request path |
| 10 requests/sec from judge | stateless-per-request compute, no shared lock contention beyond simple dict ops |
| 500KB context payload cap | `PayloadCapMiddleware` in `app/main.py`, enforced with `test_context_payload_over_cap_rejected` |
| 20 actions per tick cap | `TICK_ACTION_CAP` in `app/main.py`, enforced with `test_tick_respects_action_cap` |
| Adaptive injection: fresh digest items, metric shifts, new triggers, surprise customer scopes mid-test | `scope: "digest"` merge in `_apply_digest`; new `trigger`/`customer` contexts work without pre-registration |
| Replay test: auto-replies, objections, hostile/off-topic input, intent handoffs | `app/intent.py` classifies `accept`/`decline`/`objection`/`question`/`hostile`/`off_topic`/`auto_reply` |
| Dataset: 5 categories, 10→50 merchants, 15→200 customers, 25→100 triggers, 30 canonical test pairs, deterministic generator | `dataset/generate_dataset.py`, verified reproducible (no randomness) |
| One-page README explaining approach, model choice, tradeoffs | this file |
| Keep bot live and reachable after submission | stateless container, no external dependencies to keep alive |

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
