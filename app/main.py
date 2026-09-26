from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.compose import compose
from app.intent import classify_intent, compose_reply
from app.store import store

MAX_PAYLOAD_BYTES = 500 * 1024  # 500 KB context cap
TICK_ACTION_CAP = 20  # actions per tick cap

app = FastAPI(title="Vera Bot", version="1.0.0")


class PayloadCapMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > MAX_PAYLOAD_BYTES:
            return JSONResponse(
                {"error": "payload_too_large", "max_bytes": MAX_PAYLOAD_BYTES},
                status_code=413,
            )
        return await call_next(request)


app.add_middleware(PayloadCapMiddleware)


class ContextRequest(BaseModel):
    scope: Literal["category", "merchant", "customer", "trigger", "digest"]
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: Optional[str] = None


class TickRequest(BaseModel):
    tick_id: Optional[str] = None
    simulated_time: Optional[str] = None


class ReplyRequest(BaseModel):
    merchant_id: str
    customer_id: Optional[str] = None
    message: str
    in_reply_to: Optional[str] = None
    timestamp: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@app.get("/v1/healthz")
def healthz() -> dict:
    return {"status": "ok", "time": _now_iso()}


@app.get("/v1/metadata")
def metadata() -> dict:
    return {
        "name": "vera-bot",
        "version": app.version,
        "deterministic": True,
        "endpoints": [
            "POST /v1/context",
            "POST /v1/tick",
            "POST /v1/reply",
            "GET /v1/healthz",
            "GET /v1/metadata",
        ],
        "scopes": ["category", "merchant", "customer", "trigger", "digest"],
        "limits": {
            "tick_action_cap": TICK_ACTION_CAP,
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "response_timeout_seconds": 30,
        },
    }


@app.post("/v1/context")
def post_context(req: ContextRequest) -> dict:
    delivered_at = req.delivered_at or _now_iso()
    result = store.upsert_context(req.scope, req.context_id, req.version, req.payload, delivered_at)
    return {k: v for k, v in result.items() if k != "no_op"}


def _resolve_category(merchant_payload: dict) -> Optional[dict]:
    category_name = merchant_payload.get("category")
    entry = store.get_context("category", category_name) if category_name else None
    return entry["payload"] if entry else None


def _apply_digest(merchant: dict, merchant_id: str) -> dict:
    """Merge a mid-test digest update (fresh offers/metric shifts pushed via
    scope="digest", keyed by merchant_id) on top of the base merchant context."""
    digest_entry = store.get_context("digest", merchant_id)
    if not digest_entry:
        return merchant
    merged = dict(merchant)
    digest_payload = digest_entry["payload"]
    for key in ("performance", "offers", "identity"):
        if key in digest_payload:
            merged[key] = digest_payload[key]
    return merged


def _with_conversation_history(merchant: dict, merchant_id: str) -> dict:
    merged = dict(merchant)
    merged["conversation_history"] = list(merchant.get("conversation_history") or []) + store.get_sent_history(
        merchant_id
    )
    return merged


@app.post("/v1/tick")
def post_tick(req: TickRequest) -> dict:
    tick_no = store.next_tick()
    merchants = store.all_contexts("merchant")
    triggers = store.all_contexts("trigger")

    actions: list[dict] = []
    for _trigger_id, trigger_entry in triggers.items():
        if len(actions) >= TICK_ACTION_CAP:
            break
        trigger = trigger_entry["payload"]
        merchant_id = trigger.get("merchant_id")
        merchant_entry = merchants.get(merchant_id)
        if merchant_entry is None:
            continue
        merchant = _apply_digest(merchant_entry["payload"], merchant_id)
        merchant = _with_conversation_history(merchant, merchant_id)
        category = _resolve_category(merchant)
        if category is None:
            continue

        customer = None
        customer_id = trigger.get("payload", {}).get("customer_id")
        if customer_id:
            customer_entry = store.get_context("customer", customer_id)
            customer = customer_entry["payload"] if customer_entry else None

        composed = compose(category, merchant, trigger, customer)
        if composed is None:
            continue
        if store.is_suppressed(composed["suppression_key"]):
            continue

        store.suppress(composed["suppression_key"])
        store.set_pending(merchant_id, composed)
        store.record_sent(merchant_id, composed["suppression_key"], composed["topic_key"])
        actions.append(
            {
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                **{k: v for k, v in composed.items() if k != "topic_key"},
            }
        )

    return {"tick_id": req.tick_id or f"tick_{tick_no}", "actions": actions, "count": len(actions)}


@app.post("/v1/reply")
def post_reply(req: ReplyRequest) -> dict:
    prior_action = store.get_pending(req.merchant_id) or {}
    merchant_entry = store.get_context("merchant", req.merchant_id)
    merchant = _apply_digest(merchant_entry["payload"], req.merchant_id) if merchant_entry else None

    intent = classify_intent(req.message)
    result = compose_reply(intent, prior_action, merchant)

    if result.get("resolved") or result.get("suppress_cooldown"):
        store.clear_pending(req.merchant_id)

    return {
        "intent": intent,
        "reply": result.get("reply", ""),
        "cta": result.get("cta", "none"),
        "send_as": prior_action.get("send_as"),
        "suppression_key": prior_action.get("suppression_key"),
        "rationale": f"Classified inbound reply as '{intent}' and responded accordingly.",
    }
