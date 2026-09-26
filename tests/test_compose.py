import json
import os

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ROOT = os.path.dirname(os.path.dirname(__file__))


def _load(name):
    with open(os.path.join(ROOT, "dataset", name)) as f:
        return json.load(f)


def _seed_context(scope, context_id, version, payload):
    resp = client.post(
        "/v1/context",
        json={"scope": scope, "context_id": context_id, "version": version, "payload": payload},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_healthz():
    resp = client.get("/v1/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metadata_lists_endpoints():
    resp = client.get("/v1/metadata")
    body = resp.json()
    assert body["name"] == "vera-bot"
    assert "POST /v1/context" in body["endpoints"]


def test_context_idempotent_and_monotonic():
    payload_v1 = {"category": "dentists"}
    payload_v2 = {"category": "dentists", "note": "updated"}

    r1 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": payload_v1})
    r2 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 1, "payload": {"category": "different"}})
    r3 = client.post("/v1/context", json={"scope": "merchant", "context_id": "m_test", "version": 2, "payload": payload_v2})

    assert r1.json()["accepted"] and r2.json()["accepted"] and r3.json()["accepted"]


def test_full_tick_and_reply_flow():
    merchants = _load("merchants_seed.json")
    triggers = _load("triggers_seed.json")

    for cat in ["dentists", "salons", "restaurants", "gyms", "pharmacies"]:
        with open(os.path.join(ROOT, "dataset", "categories", f"{cat}.json")) as f:
            _seed_context("category", cat, 1, json.load(f))

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
