"""Deterministic expansion of the seed dataset.

Expands the base seeds into a larger, fully reproducible dataset:
  - merchants_seed.json (10)  -> expanded/merchants.json (50)
  - customers_seed.json (15)  -> expanded/customers.json (200)
  - triggers_seed.json (25)   -> expanded/triggers.json (100)

Expansion is a pure function of the seed index (no randomness), so every
team that runs this script against the same seeds gets byte-identical
output. It also emits 30 canonical (input -> expected-shape) test pairs
used by the judge simulator and by tests/test_compose.py.
"""
import argparse
import copy
import json
import os

CATEGORIES = ["dentists", "salons", "restaurants", "gyms", "pharmacies"]
TRIGGER_TYPES = ["recall", "spike", "dip", "research", "festival"]


def load(seed_dir, name):
    with open(os.path.join(seed_dir, name)) as f:
        return json.load(f)


def expand_merchants(seed, target=50):
    out = []
    n = len(seed)
    for i in range(target):
        base = copy.deepcopy(seed[i % n])
        cycle = i // n
        if cycle == 0:
            out.append(base)
            continue
        base["merchant_id"] = f"{base['merchant_id']}_v{cycle}"
        base["name"] = f"{base['name']} ({cycle + 1})"
        # deterministic perturbation of performance signals
        drift = ((i * 7) % 21) - 10  # -10..10, deterministic
        base["performance"]["footfall_trend_pct"] = base["performance"]["footfall_trend_pct"] + drift
        base["performance"]["bookings_last_7d"] = max(0, base["performance"]["bookings_last_7d"] + (i % 5))
        out.append(base)
    return out


def expand_customers(seed, merchants, target=200):
    out = []
    n = len(seed)
    m = len(merchants)
    for i in range(target):
        base = copy.deepcopy(seed[i % n])
        cycle = i // n
        base["customer_id"] = f"{base['customer_id']}_x{i}"
        base["merchant_id"] = merchants[i % m]["merchant_id"]
        if cycle > 0 and base.get("last_visit_days_ago") is not None:
            base["last_visit_days_ago"] = base["last_visit_days_ago"] + (cycle * 13) % 300
        out.append(base)
    return out


def expand_triggers(seed, merchants, target=100):
    out = []
    n = len(seed)
    m = len(merchants)
    for i in range(target):
        base = copy.deepcopy(seed[i % n])
        base["trigger_id"] = f"{base['trigger_id']}_x{i}"
        base["merchant_id"] = merchants[i % m]["merchant_id"]
        payload = base.get("payload", {})
        if "search_count" in payload:
            payload["search_count"] = payload["search_count"] + (i * 3) % 50
        if "change_pct" in payload:
            sign = 1 if payload["change_pct"] >= 0 else -1
            payload["change_pct"] = payload["change_pct"] + sign * ((i * 2) % 10)
        out.append(base)
    return out


def build_test_pairs(merchants, triggers, categories_dir, count=30):
    cat_ctx = {}
    for cat in CATEGORIES:
        with open(os.path.join(categories_dir, f"{cat}.json")) as f:
            cat_ctx[cat] = json.load(f)

    pairs = []
    for i in range(count):
        trigger = triggers[i % len(triggers)]
        merchant = next(mm for mm in merchants if mm["merchant_id"] == trigger["merchant_id"])
        category = cat_ctx[merchant["category"]]
        pairs.append({
            "test_id": f"tp_{i + 1:03d}",
            "input": {
                "category": category,
                "merchant": merchant,
                "trigger": trigger,
                "customer": None,
            },
            "expect": {
                "has_message": True,
                "has_single_cta": True,
                "has_suppression_key": True,
                "grounded_in_trigger": True,
            },
        })
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-dir", default="dataset")
    parser.add_argument("--out", default="expanded")
    args = parser.parse_args()

    seed_dir = args.seed_dir
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    merchants_seed = load(seed_dir, "merchants_seed.json")
    customers_seed = load(seed_dir, "customers_seed.json")
    triggers_seed = load(seed_dir, "triggers_seed.json")

    merchants = expand_merchants(merchants_seed, 50)
    customers = expand_customers(customers_seed, merchants, 200)
    triggers = expand_triggers(triggers_seed, merchants, 100)

    with open(os.path.join(out_dir, "merchants.json"), "w") as f:
        json.dump(merchants, f, indent=2)
    with open(os.path.join(out_dir, "customers.json"), "w") as f:
        json.dump(customers, f, indent=2)
    with open(os.path.join(out_dir, "triggers.json"), "w") as f:
        json.dump(triggers, f, indent=2)

    test_pairs = build_test_pairs(merchants, triggers, os.path.join(seed_dir, "categories"), 30)
    with open(os.path.join(out_dir, "test_pairs.json"), "w") as f:
        json.dump(test_pairs, f, indent=2)

    print(
        f"-> {out_dir}/  "
        f"{len(merchants)} merchants · {len(customers)} customers · "
        f"{len(triggers)} triggers · {len(test_pairs)} test pairs"
    )


if __name__ == "__main__":
    main()
