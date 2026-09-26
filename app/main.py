from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.compose import compose
from app.intent import classify_intent, compose_reply
from app.store import store

app = FastAPI(title="Vera Bot", version="1.0.0")

TICK_ACTION_CAP = 20


class ContextRequest(BaseModel):
    scope: str
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
        "tick_action_cap": TICK_ACTION_CAP,
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
        merchant = merchant_entry["payload"]
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
        actions.append({"merchant_id": merchant_id, "customer_id": customer_id, **composed})

    return {"tick_id": req.tick_id or f"tick_{tick_no}", "actions": actions, "count": len(actions)}


@app.post("/v1/reply")
def post_reply(req: ReplyRequest) -> dict:
    prior_action = store.get_pending(req.merchant_id) or {}
    intent = classify_intent(req.message)
    result = compose_reply(intent, prior_action)

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
