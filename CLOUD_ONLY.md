# EVO·VISION — 100% na nuvem sem este PC (runbook honesto)

**Estado atual (2026-11-18, verificado contra produção):**
- HMI + API (FastAPI serverless): https://evolution-beta-six.vercel.app — 200 em `/`, `/status`, `/events` (18.577), `/telemetry`, `/temperature`, `/export/csv`, `/auth/*`.
- IA: **provider `ml` em produção** — IsolationForest + RandomForest treinados com os dados reais; os `.joblib` sobem no cold start do bucket privado Neon (`models/`); verificado via `/ml/status` (trained:true, provider ml).
- ESP32 físico → nuvem sem portas abertas: firmware `esp32_cloud_agent.ino` faz polling HTTPS de saída a cada 2s em `/device/heartbeat` (header `X-Device-Token`). `/manual-stop` e `/reset`: se o dispositivo deu heartbeat nos últimos 15s → enfileira comando (200 `queued:true`); senão → **503 honesto**. ACK do ESP32 fecha o ciclo (`/device/ack` → system_logs). Verificado E2E com dispositivo simulado (heartbeat → comando enfileirado → entregue → ack; temperatura 61.5 ℃ reportada → `/temperature` mostrou `source:"device"`).
- Temperatura: o ESP32 reporta via heartbeat (DS18B20 no GPIO4); sem hardware, fica o último evento com leitura térmica.
- Snapshot ao vivo: `/snapshot` tenta arquivo local → senão, **redirect 302 pré-assinado** para `snapshots/latest.jpg` no bucket privado Neon (emissor: edge que roda o Vision Monitor com `NEON_STORAGE_ENABLED=true`). Verificado com frame sintético (302 → 200 JPEG).
- Correção de race serverless: `create_all` serializado com `pg_advisory_lock` no Postgres (cold starts concorrentes colidiam em `pg_type`).

## O que falta (física > software — só com hardware/contas suas)
1. **ESP32 físico**: flashear `esp32_cloud_agent.ino` (Arduino IDE; libs: ArduinoJson, OneWire, DallasTemperature), WiFi, `DEVICE_TOKEN` (valor no `.env` local deste PC — copie para `DEVICE_TOKEN` no firmware), relé no GPIO26 (active-low), botão no GPIO33, DS18B20 no GPIO4. → E-Stop e temperatura reais sem este PC.
2. **Visão ao vivo**: nenhuma nuvem "cria" câmera. Opções:
   a. ESP32-CAM (~R$60–100) flasheada para POST de frame a cada 2–5s → bucket (faço o sketch sob demanda) — visão limitada (frame-a-frame).
   b. Qualquer máquina sempre ligada com o Vision Monitor + `NEON_STORAGE_ENABLED=true` (ex.: PC velho, Pi 4/5) — visão completa, continua não sendo "a nuvem".
   c. Sem hardware: `/snapshot` fica 404 honesto; HMI cai no feed DEMO sintético.
3. **Dashboard Streamlit**: precisa de 1 serviço grátis — Streamlit Community Cloud (build lê `requirements.txt` da raiz — incompatível com manter o bundle da Vercel enxuto) **ou (recomendado) Render free** (build `pip install -r requirements-full.txt`, start `streamlit run dashboard.py`, sleep após inatividade) **ou** cole no `.env`/secrets: `API_BASE_URL=https://evolution-beta-six.vercel.app` (já implementado em `dashboard.py`).
4. **APK**: conferir se o wrapper Capacitor aponta para `evolution-beta-six.vercel.app` (senão, rebuildar o APK).
5. **Commit/push** pendente: mudanças em `api.py`, `ml_model.py`, `vercel.json`, `.vercelignore`, `frontend/app.js`, `dashboard.py`, `train_ai.py`, `esp32_cloud_agent.ino` (novo), `requirements*.txt`, `DEPLOY_VERCEL.md` — nada foi commitado.

## Verificação (comandos)
```bash
B=https://evolution-beta-six.vercel.app
curl -s $B/health
curl -s $B/status
curl -s -m 30 -X POST $B/device/heartbeat -H 'Content-Type: application/json' -H "X-Device-Token: $DEVICE_TOKEN" -d '{"device_id":"esp32-01","temperature":61.5}'
# dispositivo simulado: heartbeat -> POST /manual-stop -> heartbeat (comando) -> /device/ack
```

## Segredos
- `DEVICE_TOKEN` e `JWT_SECRET` no Vercel e no `.env` local (gitignored). Nada disso está no git.
- `DEVICE_TOKEN` também precisa ir para o firmware (copie do `.env`).
- Arquivos temporários de segredos em `%TEMP%\opencode\neon-secrets` foram apagados.
