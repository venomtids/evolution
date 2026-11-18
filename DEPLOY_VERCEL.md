# Deploy no Vercel — EVO·VISION full-stack (API serverless + Postgres)

## ✅ NO AR (2026-11-18): https://evolution-beta-six.vercel.app
- Projeto Vercel: `visonard/evolution` (org `visonard`, id prj_uQmCO7zXmdMllEOFenbEzunUUPXn), deploy via CLI (projeto NÃO conectado ao GitHub; `vercel git connect` é opcional).
- Arquitetura final: **tudo passa pela função `api.py`** — `vercel.json` roteia `/` → FastAPI, que serve API + frontend (StaticFiles, same-origin). O build `@vercel/static` do frontend foi removido (o `/` não resolveria para `index.html` de forma confiável).
- Env vars (Production + Preview): `DATABASE_URL` (Neon pooler), `JWT_SECRET`, `TEMP_SOURCE=mock`, `AI_ENABLED=true`, `AI_PROVIDER=ml`, `ESP32_IP=127.0.0.1`, `ESP32_PORT=80`, `EVIDENCE_CAPTURE_ENABLED=false`, `CORS_ORIGINS=[localhost:8000, 127.0.0.1:8000, frontend-azure-nu-92.vercel.app]`.
- Verificação pós-deploy: `/health` 200 · `/` HTML 200 · `/app.js`, `/demo.js`, `/style.css`, `/favicon.svg` 200 · `/status` (esp32_connected=false) · `/events` total=18577 · `/telemetry` · `/temperature` (60/75, mock) · `/export/csv?status=CRITICO` (histórico real) · login senha errada → 401 · `/diagnostics/stats` sem token → 401 · `/snapshot` → 404 · `/manual-stop` e `/reset` → **503 honesto** (ESP32 inalcançável da nuvem).
- Nota: o JSON do deploy reporta `deploymentProtection: vercel_authentication`, mas curl externo obtém 200 em todas as rotas — o site está público.
- O que NÃO vai pra nuvem: webcam, ESP32/E-Stop, Vision Monitor, dashboard Streamlit — continuam no PC/LAN.
- Ver também: checklist local vs nuvem abaixo e o fluxo Neon no fim deste arquivo.

## O que vai pra nuvem
`api.py` (FastAPI) como função Python serverless (`@vercel/python`) + banco Postgres (Neon) + o frontend estático.
**O que NÃO vai:** webcam, ESP32/E-Stop e o Vision Monitor — continuam no seu PC/LAN. Quando o PC está ligado, o monitor publica eventos direto no backend da nuvem (`API_BASE_URL=https://evolution-beta-six.vercel.app` no `.env` local).

## Arquitetura
- `https://evolution-beta-six.vercel.app/` → frontend estático servido pela própria função (`StaticFiles html=True` no `/`)
- `/status`, `/telemetry`, `/events`, `/temperature`, `/motion`, `/stream`, `/export/csv`, `/auth/*`, `/diagnostics/*`, `/manual-stop`, `/reset` → função serverless `api.py` (mesmas rotas, mesmo código)
- Banco: Postgres no Neon (connection string em `DATABASE_URL`, `sslmode=require`)
- Sem `DATABASE_URL`, a função cai no SQLite local **efêmero** da serverless (não use em produção)

## Passo a passo
1. Crie o Postgres: https://neon.tech → projeto grátis → copie a connection string
   (`postgresql://user:senha@ep-xxx.neon.tech/neondb?sslmode=require`)
2. Migre os dados locais (18 mil eventos + usuários + laudos):
   ```powershell
   set MIGRATE_DATABASE_URL=postgresql://user:senha@ep-xxx.neon.tech/neondb?sslmode=require
   .\.venv\Scripts\python.exe migrate_to_neon.py
   ```
   Saída esperada: `[OK] events: 18577 ...` etc.
3. No projeto Vercel (raiz do repo), configure as variáveis de ambiente (Production):
   - `DATABASE_URL` = a mesma connection string do Neon
   - `JWT_SECRET` = o mesmo valor do seu `.env` (senão tokens antigos invalidam)
   - `AI_PROVIDER` = `local` (heurística ISO-10816; modelos `models/*.joblib` não vão pro bundle)
   - `AI_ENABLED` = `true`
   - `ESP32_IP` = vazio/`127.0.0.1` → E-Stop/Reset na nuvem respondem **503 honesto** (nunca fingem sucesso)
   - `CAPTURES_DIR` = `/tmp/captures`
   - `CORS_ORIGINS` = `*` (ou o domínio do frontend, se preferir)
4. Deploy: `vercel --prod` na raiz do repo.
5. Opcional (PC publicando na nuvem): no `.env` do PC,
   `API_BASE_URL=https://<app>.vercel.app` → o Vision Monitor passa a enviar eventos para a nuvem.
6. O frontend "espelhado" (somente demo) continua em `https://frontend-azure-nu-92.vercel.app`.

## Limites honestos
- **E-Stop/Reset na nuvem:** a ESP32 está no seu PC/LAN — a API responde 503 e o HMI mostra o erro. O latch NÃO é liberado remotamente (proposital).
- **/stream (câmera ao vivo):** sem webcam na nuvem; o botão MONITOR cai no feed de demonstração (`MONITOR · DEMO`).
- **Dados novos** só existem na nuvem quando o seu PC (Vision Monitor) publica; com PC desligado, o site mostra o último estado sincronizado.
- Push para GitHub ainda depende de você: adicione `Creator-Gm` como colaborador do repo `venomtids/evolution` e aceite o convite.

## Neon (branch production, projeto `visionard`)
- Postgres 18 (`DATABASE_URL` puxado via `neon deploy` / `neon env pull`)
- Function `hello`: status público da frota → https://br-still-rain-bboojij8-hello.compute.c-15.us-east-1.aws.neon.tech/ (lê o Postgres)
- Object Storage: bucket privado `assets` (teste: `python storage.py` — upload/head/download/delete)
- Realtime: habilitado no branch (`neon realtime enable --allowed-origin https://frontend-azure-nu-92.vercel.app --allowed-origin http://localhost:8000`). Prova E2E: `node realtime-smoke.mjs` (assina os 5 últimos eventos, insere um 'smoke' e observa via WebSocket).
- O HMI ainda NÃO consome Realtime (usa polling de 5s); adotar exige endpoint que sela a query + cliente com `@neon/realtime/client` — próximo passo natural.

## Atualização 2026-11-18 (tarde): nuvem completa
- IA 100% na nuvem: provider `ml` em produção (modelos treinados com os 18.577 eventos; `.joblib` baixados do bucket Neon no cold start via `models/`, ver `ml_model.py` `_bootstrap_from_storage`). Verificado: `/ml/status` → `trained:true`, `/diagnostics/analyze` → `provider:"ml"`.
- E-Stop pela nuvem: `/device/heartbeat` + `/device/ack` + enfileiramento em `/device/heartbeat`→`/manual-stop`/`/reset` (200 `queued:true`) quando o ESP32 deu heartbeat ≤15s; caso contrário, 503 honesto. Testado E2E com dispositivo simulado (comando enfileirado → entregue → ack). Firmware pronto: `esp32_cloud_agent.ino` (copiar `DEVICE_TOKEN` do `.env`).
- `/snapshot` na nuvem: redirect 302 pré-assinado para `snapshots/latest.jpg` no bucket; o HMI cai para polling de 2,5s quando `/stream` falha (`app.js`).
- Race serverless corrigida: `create_all` serializado com `pg_advisory_lock` (cold starts concorrentes colidiam no Neon em `pg_type`).
- Temperatura real: heartbeat do ESP32 (DS18B20 GPIO4) flui para `/temperature` (`source:"device"`) e `/temperature/history`.
- Ver `CLOUD_ONLY.md` para o que falta (hardware + 1 serviço grátis).
