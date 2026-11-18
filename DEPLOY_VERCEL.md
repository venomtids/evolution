# Deploy no Vercel — EVO·VISION full-stack (API serverless + Postgres)

## O que vai pra nuvem
`api.py` (FastAPI) como função Python serverless (`@vercel/python`) + banco Postgres (Neon) + o frontend estático.
**O que NÃO vai:** webcam, ESP32/E-Stop e o Vision Monitor — continuam no seu PC/LAN. Quando o PC está ligado, o monitor publica eventos direto no backend da nuvem (`API_BASE_URL=https://<seu-app>.vercel.app` no `.env`).

## Arquitetura
- `https://<app>.vercel.app/` → frontend estático (`frontend/`, servido de `/`)
- `/status`, `/telemetry`, `/events`, `/temperature`, `/motion`, `/stream`, `/export/csv`, `/auth/*`, `/diagnostics/*`, `/manual-stop`, `/reset` → função serverless `api.py`
- Banco: Postgres no Neon (connection string em `DATABASE_URL`, com `sslmode=require`)
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
