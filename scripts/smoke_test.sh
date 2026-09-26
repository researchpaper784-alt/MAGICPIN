#!/usr/bin/env bash
# Exercises all 5 required endpoints against a deployed bot URL.
# Usage: BASE_URL=https://your-bot.example.com ./scripts/smoke_test.sh
set -euo pipefail

BASE_URL="${BASE_URL:?Set BASE_URL to your deployed bots public base URL}"
FAIL=0

check() {
  local label="$1" method="$2" path="$3" data="${4:-}"
  local code
  if [ -n "$data" ]; then
    code=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X "$method" "$BASE_URL$path" \
      -H "Content-Type: application/json" -d "$data")
  else
    code=$(curl -s -o /tmp/resp.json -w "%{http_code}" -X "$method" "$BASE_URL$path")
  fi
  if [ "$code" = "200" ]; then
    echo "OK   $label ($code)"
  else
    echo "FAIL $label ($code)"
    cat /tmp/resp.json
    FAIL=1
  fi
}

echo "== Smoke testing $BASE_URL =="

check "GET /v1/healthz"  GET  /v1/healthz
check "GET /v1/metadata" GET  /v1/metadata

check "POST /v1/context (category)" POST /v1/context '{
  "scope": "category", "context_id": "dentists", "version": 1,
  "payload": {"category": "dentists", "voice": {"tone": "clinical"}, "offer_patterns": ["checkup_discount"]}
}'

check "POST /v1/context (merchant)" POST /v1/context '{
  "scope": "merchant", "context_id": "m_smoke_test", "version": 1,
  "payload": {
    "merchant_id": "m_smoke_test", "name": "Smoke Test Clinic", "category": "dentists",
    "identity": {"persona_name": "Vera, on behalf of Smoke Test Clinic"},
    "performance": {"avg_rating": 4.7},
    "offers": [{"offer_id": "o_smoke", "label": "discounted check up", "price_inr": 299, "tags": ["checkup_discount"]}]
  }
}'

check "POST /v1/context (trigger)" POST /v1/context '{
  "scope": "trigger", "context_id": "t_smoke_test", "version": 1,
  "payload": {
    "trigger_id": "t_smoke_test", "merchant_id": "m_smoke_test", "type": "research",
    "payload": {"search_term": "Dental Check Up", "search_count": 190, "locality": "Andheri West"}
  }
}'

check "POST /v1/tick" POST /v1/tick '{"tick_id": "smoke_tick_1"}'
echo "   tick response:"; cat /tmp/resp.json; echo

check "POST /v1/reply" POST /v1/reply '{"merchant_id": "m_smoke_test", "message": "yes go ahead"}'
echo "   reply response:"; cat /tmp/resp.json; echo

if [ "$FAIL" = "1" ]; then
  echo "== One or more checks FAILED =="
  exit 1
fi
echo "== All checks passed =="
