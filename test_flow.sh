#!/usr/bin/env bash
# test_flow.sh — teste E2E: cadastro, login, eventos, burst, diagnóstico IA
set -e
BASE="${1:-http://127.0.0.1:8000}"

echo "=== 1. Health ==="
curl -s "$BASE/health" && echo

echo "=== 2. Registrar usuário demo ==="
curl -s -X POST "$BASE/auth/register" -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234","email":"demo@demo.com","role":"operator"}' | head -c 200 && echo

echo "=== 3. Login ==="
TOKEN=$(curl -s -X POST "$BASE/auth/login" -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"demo1234"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Token: ${TOKEN:0:30}..."

echo "=== 4. Inserindo telemetria (6 normal + 5 critico) ==="
for i in $(seq 1 6); do
  curl -s -X POST "$BASE/events" -H "Content-Type: application/json" \
    -d "{\"status\":\"NORMAL\",\"value\":0.$i,\"source\":\"test\"}" > /dev/null
done
for i in $(seq 1 5); do
  curl -s -X POST "$BASE/events" -H "Content-Type: application/json" \
    -d "{\"status\":\"CRITICO\",\"value\":5.$i,\"source\":\"test\"}" > /dev/null
  sleep 0.2
done
echo "Eventos inseridos."

echo "=== 5. Stats de erros (burst) ==="
curl -s "$BASE/diagnostics/stats" -H "Authorization: Bearer $TOKEN" && echo

sleep 2
echo "=== 6. Último diagnóstico IA ==="
curl -s "$BASE/diagnostics/latest" -H "Authorization: Bearer $TOKEN" | python -m json.tool

echo "=== 7. Status consolidado ==="
curl -s "$BASE/status" | python -m json.tool

echo "=== 8. Telemetria (últimos 5 pontos) ==="
curl -s "$BASE/telemetry?limit=5" | python -m json.tool | head -40

echo ""
echo "=== FIM DO TESTE E2E ==="
