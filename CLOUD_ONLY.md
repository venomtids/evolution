# EVO·VISION — 100% na nuvem sem este PC (runbook honesto)

**Estado atual (2026-11-18, verificado contra produção):**
- HMI + API (FastAPI serverless): https://evolution-beta-six.vercel.app — 200 em `/`, `/status`, `/events` (18.577), `/telemetry`, `/temperature`, `/export/csv`, `/auth/*`.
- IA: **provider `ml` em produção** — IsolationForest + RandomForest treinados com os dados reais; os `.joblib` sobem no cold start do bucket privado Neon (`models/`); verificado via `/ml/status` (trained:true, provider ml).
- ESP32 físico → nuvem sem portas abertas: firmware `esp32_cloud_agent.ino` faz polling HTTPS de saída a cada 2s em `/device/heartbeat` (header `X-Device-Token`). `/manual-stop` e `/reset`: se o dispositivo deu heartbeat nos últimos 15s → enfileira comando (200 `queued:true`); senão → **503 honesto**. ACK do ESP32 fecha o ciclo (`/device/ack` → system_logs). Verificado E2E com dispositivo simulado (heartbeat → comando enfileirado → entregue → ack; temperatura 61.5 ℃ reportada → `/temperature` mostrou `source:"device"`).
- Temperatura: o ESP32 reporta via heartbeat (DS18B20 no GPIO4); sem hardware, fica o último evento com leitura térmica.
- Snapshot ao vivo: `/snapshot` tenta arquivo local → senão, **redirect 302 pré-assinado** para `snapshots/latest.jpg` no bucket privado Neon (emissor: edge que roda o Vision Monitor com `NEON_STORAGE_ENABLED=true`). Verificado com frame sintético (302 → 200 JPEG).
- Correção de race serverless: `create_all` serializado com `pg_advisory_lock` no Postgres (cold starts concorrentes colidiam em `pg_type`).

## O que falta (física > software — só com hardware/contas suas)
1. **ESP32 físico**: clique duplo em `flash_esp32.bat` (Windows; baixa arduino-cli/core na 1ª vez, detecta a porta, pergunta SSID/senha, grava — firmware já compilado/validado). Relé no GPIO26 (active-low), botão no GPIO33, DS18B20 no GPIO4. → E-Stop e temperatura reais sem este PC.
2. **Visão ao vivo**: nenhuma nuvem "cria" câmera. Opções:
   a. ESP32-CAM (~R$60–100) flasheada para POST de frame a cada 2–5s → bucket (faço o sketch sob demanda) — visão limitada (frame-a-frame).
   b. Qualquer máquina sempre ligada com o Vision Monitor + `NEON_STORAGE_ENABLED=true` (ex.: PC velho, Pi 4/5) — visão completa, continua não sendo "a nuvem".
   c. Sem hardware: `/snapshot` fica 404 honesto; HMI cai no feed DEMO sintético.
3. **Dashboard Streamlit**: Streamlit Community Cloud (grátis, sem cartão). Entrypoint `streamlit_app/app.py` + `streamlit_app/requirements.txt` (a raiz segue enxuta para a Vercel); `dashboard.py` também lê `API_BASE_URL` de `st.secrets` (Secrets da app no Cloud). Deploy em 5 passos: ver DEPLOY_CLIENTE.md §4b. IA dos laudos: provider `ml` (IsolationForest + RandomForest locais, treinados com os dados reais) — nenhuma chave de IA externa é necessária em lugar nenhum.
4. ~~**APK**~~ — resolvido em 2026-11-18 (commit 3f38fc1): padrão de fábrica agora é `evolution-beta-six.vercel.app`; precisa rebuildar o APK para o novo padrão valer.
5. **Commit/push** pendente: mudanças em `api.py`, `ml_model.py`, `vercel.json`, `.vercelignore`, `frontend/app.js`, `dashboard.py`, `train_ai.py`, `esp32_cloud_agent.ino` (novo), `requirements*.txt`, `DEPLOY_VERCEL.md` — nada foi commitado.

## Câmera sem este PC
- Via cabo (PC/Pi do cliente): `VIDEO_SOURCE=auto` no `.env` (detecta webcam nos índices 0-3) ou índice fixo (`VIDEO_SOURCE=0`); publica no bucket via `NEON_STORAGE_ENABLED=true`.
- Via permissões (celular/PC de qualquer pessoa): aba WEB CAM do HMI → getUserMedia (HTTPS).
- Ver `DEPLOY_CLIENTE.md`.

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
