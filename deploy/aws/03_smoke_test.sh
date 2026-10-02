#!/usr/bin/env bash
# Pruebas de humo contra el servicio desplegado (usa Claude real: cada caso tarda ~30 s).
# Uso: ./deploy/aws/03_smoke_test.sh https://<endpoint>
set -euo pipefail
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8

URL="${1:?Uso: $0 https://<endpoint>}"
URL="${URL%/}"
API="$URL/api/v1/onboarding"

field() { python -c "import sys, json; d = json.load(sys.stdin); print($1)"; }

start() {
  curl -sS --max-time 120 -X POST "$API/start" -H "Content-Type: application/json" \
    -d "{\"prospect_name\": \"$1\", \"document_id\": \"$2\", \"product\": \"$3\"}"
}

echo "== Health"
curl -sS --max-time 10 "$URL/health"
echo

echo "== Aprobado (Juan Perez)"
start "Juan Perez" 1712345678 cuenta_ahorros | field "d['status'], [m['code'] for m in d['mitigations']]"

echo "== Riesgo alto (Pedro Gomez)"
start "Pedro Gomez" 1705555555 tarjeta_credito | field "d['status'], [m['code'] for m in d['mitigations']]"

echo "== Identidad ambigua + prueba de vida (Maria Lopez)"
RESPONSE="$(start "Maria Lopez" 0923456789 cuenta_ahorros)"
echo "$RESPONSE" | field "d['status'], [m['code'] for m in d['mitigations']]"
SESSION_ID="$(echo "$RESPONSE" | field "d['session_id']")"
curl -sS --max-time 120 -X POST "$API/$SESSION_ID/messages" -H "Content-Type: application/json" \
  -d '{"message": "Ya complet\u00e9 la prueba de vida", "liveness_token": "LIVENESS-OK"}' |
  field "d['status'], d['customer_message'][:160]"

echo "== Auditoría de la sesión $SESSION_ID"
curl -sS --max-time 10 "$API/$SESSION_ID" |
  field "'tool_calls:', [(c['agent'], c['tool'], c['allowed']) for c in d['tool_calls']]"
