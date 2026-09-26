"""Deterministic keyword-based intent classification for inbound replies."""
from __future__ import annotations

from typing import Optional

ACCEPT_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "send", "go ahead", "do it", "confirm"}
DECLINE_WORDS = {"no", "nope", "not now", "later", "stop", "skip", "pass"}
OBJECTION_WORDS = {"expensive", "too much", "already did", "already have", "competitor", "costly"}
QUESTION_WORDS = {"how", "what", "why", "cost", "price", "details", "when", "?"}
AUTO_REPLY_MARKERS = {"out of office", "auto-reply", "automatic reply", "do not reply"}
HOSTILE_WORDS = {"stupid", "idiot", "shut up", "useless bot", "scam", "fraud", "get lost"}
OFF_TOPIC_MARKERS = {"weather", "cricket score", "who are you", "are you human", "tell me a joke"}


def classify_intent(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return "unclear"
    if any(marker in lowered for marker in AUTO_REPLY_MARKERS):
        return "auto_reply"
    if any(word in lowered for word in HOSTILE_WORDS):
        return "hostile"
    if any(marker in lowered for marker in OFF_TOPIC_MARKERS):
        return "off_topic"
    if any(word in lowered for word in OBJECTION_WORDS):
        return "objection"
    if any(word in lowered for word in DECLINE_WORDS):
        return "decline"
    if any(word in lowered for word in ACCEPT_WORDS):
        return "accept"
    if any(word in lowered for word in QUESTION_WORDS):
        return "question"
    return "unclear"


def _cheapest_alternate_offer(merchant: Optional[dict], current_offer_id: Optional[str]) -> Optional[dict]:
    if not merchant:
        return None
    offers = merchant.get("offers") or []
    alternates = [o for o in offers if o.get("offer_id") != current_offer_id]
    if not alternates:
        return None
    return min(alternates, key=lambda o: o.get("price_inr", float("inf")))


def compose_reply(intent: str, prior_action: dict, merchant: Optional[dict] = None) -> dict:
    offer_mention = prior_action.get("message", "")
    cta = prior_action.get("cta", "")

    if intent == "accept":
        return {
            "reply": "Great, sending it now. I'll follow up once it goes out.",
            "cta": "none",
            "resolved": True,
        }
    if intent == "decline":
        return {
            "reply": "No problem, I'll hold off and check back with a better moment.",
            "cta": "none",
            "resolved": True,
        }
    if intent == "objection":
        alt = _cheapest_alternate_offer(merchant, prior_action.get("offer_id"))
        if alt:
            return {
                "reply": f"Understood — how about {alt['label']} at ₹{alt['price_inr']} instead?",
                "cta": "Send this instead?",
                "resolved": False,
            }
        return {
            "reply": "Understood — I'll hold this and revisit with a lighter offer next time.",
            "cta": "none",
            "resolved": True,
        }
    if intent == "question":
        return {
            "reply": f"Sure — here are the details: {offer_mention}",
            "cta": cta or "Send now?",
            "resolved": False,
        }
    if intent == "hostile":
        return {
            "reply": "Understood — I'll stop here. Reach out anytime you'd like to pick this back up.",
            "cta": "none",
            "resolved": True,
        }
    if intent == "off_topic":
        return {
            "reply": "I'm here to help with growth updates for your business — want me to go ahead with the offer above?",
            "cta": cta or "Send now?",
            "resolved": False,
        }
    if intent == "auto_reply":
        return {
            "reply": "",
            "cta": "none",
            "resolved": False,
            "suppress_cooldown": True,
        }
    return {
        "reply": "Just to confirm — should I go ahead with this?",
        "cta": cta or "Send now?",
        "resolved": False,
    }
