"""Deterministic message composition for Vera.

compose(category, merchant, trigger, customer=None) is a pure function:
same inputs always produce the same output. No LLM calls, no randomness,
no wall-clock reads (any "now" comes from the trigger/context payload).
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

DEFAULT_SEND_AS = "Vera, magicpin growth assistant"


def _pick_offer(merchant: dict, category: dict, trigger: dict) -> Optional[dict]:
    offers = merchant.get("offers") or []
    if not offers:
        return None
    wanted_tags = set(category.get("offer_patterns", []))
    for offer in offers:
        if wanted_tags.intersection(offer.get("tags", [])):
            return offer
    return offers[0]


def _benchmark_fact(category: dict, trigger: dict) -> str:
    ttype = trigger.get("type")
    payload = trigger.get("payload", {}) or {}
    phrase = category.get("voice", {}).get("benchmark_phrase") or category.get(
        "benchmark_phrase", "people nearby searched for"
    )

    if ttype == "research":
        count = payload.get("search_count", 0)
        term = payload.get("search_term", "your service")
        return f'{count} {phrase} "{term}"'
    if ttype == "spike":
        pct = payload.get("change_pct", 0)
        metric = payload.get("metric", "footfall")
        window = payload.get("window", "recently")
        return f"Your {metric} is up {pct}% this {window}"
    if ttype == "dip":
        pct = abs(payload.get("change_pct", 0))
        metric = payload.get("metric", "bookings")
        window_days = payload.get("window_days", 14)
        return f"Your {metric} dropped {pct}% over the last {window_days} days"
    if ttype == "festival":
        moment = payload.get("moment", "this season").replace("_", " ")
        return f"{moment.capitalize()} is coming up"
    if ttype == "recall":
        days = payload.get("last_visit_days_ago", 0)
        return f"A customer hasn't visited in {days} days"
    return "There's a fresh signal worth acting on"


def _suppression_key(
    merchant: dict, trigger: dict, category: dict, offer: Optional[dict], customer: Optional[dict]
) -> str:
    raw = "|".join(
        [
            merchant.get("merchant_id", ""),
            trigger.get("trigger_id", ""),
            category.get("category", ""),
            offer.get("offer_id", "none") if offer else "none",
            customer.get("customer_id", "") if customer else "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _send_as(merchant: dict, customer: Optional[dict]) -> str:
    persona = merchant.get("identity", {}).get("persona_name")
    return persona or DEFAULT_SEND_AS


def _proof_point(merchant: dict, trigger: dict) -> str:
    """A trust-building fact drawn from merchant performance, used only where
    it strengthens the case (research/recall) so messages stay concise elsewhere."""
    if trigger.get("type") not in ("research", "recall"):
        return ""
    rating = merchant.get("performance", {}).get("avg_rating")
    if rating and rating >= 4.5:
        return f" You're rated {rating}★ by customers."
    return ""


def _topic_key(merchant: dict, category: dict, offer: Optional[dict], customer: Optional[dict]) -> str:
    """Identifies a merchant/offer/customer combination independent of which
    trigger fired it, so a fresh trigger about the same underlying offer is
    still recognized as a follow-up rather than a cold open."""
    raw = "|".join(
        [
            merchant.get("merchant_id", ""),
            category.get("category", ""),
            offer.get("offer_id", "none") if offer else "none",
            customer.get("customer_id", "") if customer else "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _history_prefix(merchant: dict, topic_key: str) -> str:
    """Prior conversation behavior is part of the rubric's personalization
    dimension: a merchant/offer/customer combination already touched once
    gets a follow-up framing instead of a cold open."""
    history = merchant.get("conversation_history") or []
    prior_topics = {h.get("topic_key") for h in history if isinstance(h, dict)}
    if topic_key in prior_topics:
        return "Following up — "
    return ""


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> Optional[dict[str, Any]]:
    """Return a composed message action, or None if the send should be suppressed."""

    if customer is not None and customer.get("consent") is False:
        return None
    if customer is not None and customer.get("status") == "inactive":
        return None

    offer = _pick_offer(merchant, category, trigger)
    fact = _benchmark_fact(category, trigger)
    suppression_key = _suppression_key(merchant, trigger, category, offer, customer)
    topic_key = _topic_key(merchant, category, offer, customer)

    if offer:
        offer_clause = f"Should I send them {offer['label']} at ₹{offer['price_inr']}?"
    else:
        offer_clause = "Should I send a check-in message with your current best offer?"

    subject = "them"
    if customer is not None:
        if customer.get("relationship") == "dormant":
            subject = "this customer"
        elif customer.get("relationship") == "returning":
            subject = "this returning customer"
        else:
            subject = "this new customer"

    offer_clause = offer_clause.replace("them", subject, 1) if offer else offer_clause

    prefix = _history_prefix(merchant, topic_key)
    proof = _proof_point(merchant, trigger)
    message = f"{prefix}{fact}.{proof} {offer_clause}".replace("  ", " ")
    cta = "Send now?" if offer else "Reach out now?"

    rationale_bits = [f"trigger={trigger.get('type')}", f"category={category.get('category')}"]
    if offer:
        rationale_bits.append(f"offer={offer['offer_id']}")
    if customer is not None:
        rationale_bits.append(f"customer_relationship={customer.get('relationship')}")
    if prefix:
        rationale_bits.append("prior_touch=true")
    rationale = "Grounded in " + ", ".join(rationale_bits) + "."

    return {
        "message": message,
        "cta": cta,
        "send_as": _send_as(merchant, customer),
        "suppression_key": suppression_key,
        "topic_key": topic_key,
        "offer_id": offer.get("offer_id") if offer else None,
        "rationale": rationale,
    }
