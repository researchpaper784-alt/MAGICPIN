import json
import os

from fastapi.testclient import TestClient

from app.main import app, MAX_PAYLOAD_BYTES, TICK_ACTION_CAP

client = TestClient(app)
ROOT = os.path.dirname(os.path.dirname(__file__))


def _load(name):
    with open(os.path.join(ROOT, "dataset", name)) as f:
        return json.load(f)


def _load_category(name):
    with open(os.path.join(ROOT, "dataset", "categories", f"{name}.json")) as f:
        return json.load(f)


def _seed_context(scope, context_id, version, payload):
    resp = client.post(
        "/v1/context",
        json={"scope": scope, "context_id": context_id, "version": version, "payload": payload},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    return resp


def _seed_all_categories():
    for cat in ["dentists", "salons", "restaurants", "gyms", "pharmacies"]:
        _seed_context("category", cat, 1, _load_category(cat))


# ---------------------------------------------------------------------------
# Basic surface
# ---------------------------------------------------------------------------

def test_healthz():
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metadata_lists_endpoints_and_limits():
    resp = client.get("/v1/metadata")
    body = resp.json()
    assert body["name"] == "vera-bot"
    assert "POST /v1/context" in body["endpoints"]
    assert body["limits"]["tick_action_cap"] == TICK_ACTION_CAP
    assert body["limits"]["max_payload_bytes"] == MAX_PAYLOAD_BYTES


def test_context_rejects_unknown_scope():
    resp = client.post(
        "/v1/context",
        json={"scope": "bogus", "context_id": "x", "version": 1, "payload": {}},
    )
    assert resp.status_code == 422


def test_context_idempotent_and_monotonic():
    payload_v1 = {"category": "dentists"}
    payload_v2 = {"category": "dentists", "note": "updated"}

    r1 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": payload_v1})
    r2 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": {"category": "different"}})
    r3 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 2, "payload": payload_v2})

    assert r1.json()["accepted"] and r2.json()["accepted"] and r3.json()["accepted"]


def test_context_payload_over_cap_rejected():
    huge_payload = {"blob": "x" * (MAX_PAYLOAD_BYTES + 1024)}
    resp = client.post(
        "/v1/context",
        json={"scope": "merchant", "context_id": "m_huge", "version": 1, "payload": huge_payload},
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Full tick -> compose -> suppress -> reply flow
# ---------------------------------------------------------------------------

def test_full_tick_and_reply_flow():
    merchants = _load("merchants_seed.json")
    triggers = _load("triggers_seed.json")
    _seed_all_categories()

    merchant = merchants[0]
    trigger = next(t for t in triggers if t["merchant_id"] == merchant["merchant_id"])

    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)

    tick_resp = client.post("/v1/tick", json={"tick_id": "tick_1"})
    body = tick_resp.json()
    assert body["count"] >= 1
    action = next(a for a in body["actions"] if a["merchant_id"] == merchant["merchant_id"])
    assert action["cta"]
    assert action["suppression_key"]
    assert "topic_key" not in action  # internal field must not leak to the API contract
    assert merchant["category"] in action["rationale"] or trigger["type"] in action["rationale"]

    # a second tick before resolution must not re-send the same trigger
    tick_resp_2 = client.post("/v1/tick", json={"tick_id": "tick_2"})
    body_2 = tick_resp_2.json()
    assert not any(a["merchant_id"] == merchant["merchant_id"] for a in body_2["actions"])

    reply_resp = client.post(
        "/v1/reply",
        json={"merchant_id": merchant["merchant_id"], "message": "yes go ahead"},
    )
    reply_body = reply_resp.json()
    assert reply_body["intent"] == "accept"
    assert reply_body["cta"] == "none"


def test_tick_respects_action_cap():
    _seed_all_categories()
    merchants = _load("merchants_seed.json")
    for i, merchant in enumerate(merchants):
        _seed_context("merchant", merchant["merchant_id"], 1, merchant)
        for j in range(3):
            trigger = {
                "trigger_id": f"cap_trigger_{i}_{j}",
                "merchant_id": merchant["merchant_id"],
                "type": "research",
                "payload": {"search_term": f"service {j}", "search_count": 10 + j, "locality": "Test"},
            }
            _seed_context("trigger", trigger["trigger_id"], 1, trigger)

    resp = client.post("/v1/tick", json={"tick_id": "cap_tick"})
    body = resp.json()
    assert body["count"] <= TICK_ACTION_CAP
    assert len(body["actions"]) <= TICK_ACTION_CAP


def test_consent_false_customer_is_suppressed():
    _seed_all_categories()
    merchant = {
        "merchant_id": "m_consent_test",
        "name": "Consent Test Clinic",
        "category": "dentists",
        "identity": {"persona_name": "Vera, on behalf of Consent Test Clinic"},
        "performance": {"avg_rating": 4.8},
        "offers": [{"offer_id": "o1", "label": "checkup", "price_inr": 199, "tags": ["checkup_discount"]}],
    }
    customer = {"customer_id": "c_no_consent", "relationship": "returning", "consent": False, "status": "active"}
    trigger = {
        "trigger_id": "t_consent_test",
        "merchant_id": merchant["merchant_id"],
        "type": "recall",
        "payload": {"customer_id": customer["customer_id"], "last_visit_days_ago": 100},
    }

    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("customer", customer["customer_id"], 1, customer)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)

    resp = client.post("/v1/tick", json={"tick_id": "consent_tick"})
    body = resp.json()
    assert not any(a["merchant_id"] == merchant["merchant_id"] for a in body["actions"])


def test_digest_update_overrides_merchant_offer_mid_test():
    _seed_all_categories()
    merchant = {
        "merchant_id": "m_digest_test",
        "name": "Digest Test Salon",
        "category": "salons",
        "identity": {"persona_name": "Vera, on behalf of Digest Test Salon"},
        "performance": {"avg_rating": 4.0},
        "offers": [{"offer_id": "o_old", "label": "old combo", "price_inr": 999, "tags": ["weekday_special"]}],
    }
    trigger = {
        "trigger_id": "t_digest_test",
        "merchant_id": merchant["merchant_id"],
        "type": "spike",
        "payload": {"metric": "footfall", "change_pct": 20, "window": "weekend"},
    }
    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)

    # mid-test digest swaps in a fresh, cheaper offer
    digest_payload = {"offers": [{"offer_id": "o_new", "label": "new fresh combo", "price_inr": 499, "tags": ["weekday_special"]}]}
    _seed_context("digest", merchant["merchant_id"], 1, digest_payload)

    resp = client.post("/v1/tick", json={"tick_id": "digest_tick"})
    action = next(a for a in resp.json()["actions"] if a["merchant_id"] == merchant["merchant_id"])
    assert "new fresh combo" in action["message"]
    assert "₹499" in action["message"]


def test_hostile_reply_ends_conversation_politely():
    _seed_all_categories()
    merchant = _load("merchants_seed.json")[0]
    trigger = next(t for t in _load("triggers_seed.json") if t["merchant_id"] == merchant["merchant_id"])
    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)
    client.post("/v1/tick", json={"tick_id": "hostile_setup_tick"})

    resp = client.post(
        "/v1/reply",
        json={"merchant_id": merchant["merchant_id"], "message": "this bot is useless, scam"},
    )
    body = resp.json()
    assert body["intent"] == "hostile"
    assert body["cta"] == "none"


def test_off_topic_reply_redirects_without_dropping_cta():
    _seed_all_categories()
    merchant = _load("merchants_seed.json")[0]
    trigger = next(t for t in _load("triggers_seed.json") if t["merchant_id"] == merchant["merchant_id"])
    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)
    client.post("/v1/tick", json={"tick_id": "off_topic_setup_tick"})

    resp = client.post(
        "/v1/reply",
        json={"merchant_id": merchant["merchant_id"], "message": "lol are you human?"},
    )
    body = resp.json()
    assert body["intent"] == "off_topic"
    assert body["cta"] != "none"


def test_objection_reply_offers_cheaper_alternate():
    _seed_all_categories()
    merchant = {
        "merchant_id": "m_objection_test",
        "name": "Objection Test Gym",
        "category": "gyms",
        "identity": {"persona_name": "Vera, on behalf of Objection Test Gym"},
        "performance": {"avg_rating": 4.2},
        "offers": [
            {"offer_id": "o_pricey", "label": "annual lock-in", "price_inr": 8999, "tags": ["annual_lockin"]},
            {"offer_id": "o_cheap", "label": "7-day trial pass", "price_inr": 99, "tags": ["trial_pass"]},
        ],
    }
    trigger = {
        "trigger_id": "t_objection_test",
        "merchant_id": merchant["merchant_id"],
        "type": "research",
        "payload": {"search_term": "Gym Membership", "search_count": 50, "locality": "Test"},
    }
    _seed_context("merchant", merchant["merchant_id"], 1, merchant)
    _seed_context("trigger", trigger["trigger_id"], 1, trigger)
    client.post("/v1/tick", json={"tick_id": "objection_setup_tick"})

    resp = client.post(
        "/v1/reply",
        json={"merchant_id": merchant["merchant_id"], "message": "too expensive for me"},
    )
    body = resp.json()
    assert body["intent"] == "objection"
    assert "99" in body["reply"]


def test_repeat_topic_gets_follow_up_framing():
    _seed_all_categories()
    merchant = {
        "merchant_id": "m_followup_test",
        "name": "Followup Test Pharmacy",
        "category": "pharmacies",
        "identity": {"persona_name": "Vera, on behalf of Followup Test Pharmacy"},
        "performance": {"avg_rating": 4.9},
        "offers": [{"offer_id": "o_essentials", "label": "essentials bundle", "price_inr": 349, "tags": ["essentials_discount"]}],
        "conversation_history": [],
    }
    _seed_context("merchant", merchant["merchant_id"], 1, merchant)

    trigger_1 = {
        "trigger_id": "t_followup_1",
        "merchant_id": merchant["merchant_id"],
        "type": "research",
        "payload": {"search_term": "Vitamins", "search_count": 40, "locality": "Test"},
    }
    _seed_context("trigger", trigger_1["trigger_id"], 1, trigger_1)
    first_tick = client.post("/v1/tick", json={"tick_id": "followup_tick_1"}).json()
    first_action = next(a for a in first_tick["actions"] if a["merchant_id"] == merchant["merchant_id"])
    assert "Following up" not in first_action["message"]

    # a new trigger (different trigger_id) about the same offer/topic arrives later
    trigger_2 = {
        "trigger_id": "t_followup_2",
        "merchant_id": merchant["merchant_id"],
        "type": "research",
        "payload": {"search_term": "Vitamins", "search_count": 55, "locality": "Test"},
    }
    _seed_context("trigger", trigger_2["trigger_id"], 1, trigger_2)
    second_tick = client.post("/v1/tick", json={"tick_id": "followup_tick_2"}).json()
    second_action = next(a for a in second_tick["actions"] if a["merchant_id"] == merchant["merchant_id"])
    assert second_action["message"].startswith("Following up")
