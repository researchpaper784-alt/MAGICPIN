"""Deterministic keyword-based intent classification for inbound replies."""
from __future__ import annotations

ACCEPT_WORDS = {"yes", "yeah", "yep", "sure", "ok", "okay", "send", "go ahead", "do it", "confirm"}
DECLINE_WORDS = {"no", "nope", "not now", "later", "stop", "skip", "pass"}
OBJECTION_WORDS = {"expensive", "too much", "already did", "already have", "competitor", "costly"}
QUESTION_WORDS = {"how", "what", "why", "cost", "price", "details", "when", "?"}
AUTO_REPLY_MARKERS = {"out of office", "auto-reply", "automatic reply", "do not reply"}


def classify_intent(text: str) -> str:
    lowered = (text or "").strip().lower()
    if not lowered:
        return "unclear"
    if any(marker in lowered for marker in AUTO_REPLY_MARKERS):
        return "auto_reply"
    if any(word in lowered for word in OBJECTION_WORDS):
        return "objection"
    if any(word in lowered for word in DECLINE_WORDS):
        return "decline"
    if any(word in lowered for word in ACCEPT_WORDS):
        return "accept"
    if any(word in lowered for word in QUESTION_WORDS):
        return "question"
    return "unclear"


def compose_reply(intent: str, prior_action: dict) -> dict:
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
        return {
            "reply": "Understood — I can hold this and revisit with a lighter offer next time.",
            "cta": "none",
            "resolved": True,
        }
    if intent == "question":
        return {
            "reply": f"Sure — here are the details: {offer_mention}",
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
