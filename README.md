# MVP: Manutenção Preditiva e Segurança Industrial (E-Stop) — Visão Computacional, IIoT & Full-Stack

> **Arquitetura:** Python 3 + OpenCV + NumPy | C++/Arduino ESP32 | FastAPI + SQLite + Streamlit
> **Versão:** 1.0.0 | **Data:** Nov 2026

---

## 1. Estrutura de Diretórios

```
evolution/
├── .env.example              # Template com todas as variáveis
├── config.py                 # Centralização de thresholds, IPs, URLs, ROI
├── vision_monitor.py         # Captura vídeo, ROI, detecção Farnebäck, estado
├── diagnostic_ai.py          # Motor de diagnóstico por IA (Gemini/OpenRouter/local)
├── api.py                    # Backend FastAPI (eventos, status, auth JWT, diagnóstico IA)
├── dashboard.py              # Frontend Streamlit (auto-refresh, HUD, gráficos, IA)
├── esp32_controller.ino      # Firmware ESP32 (HTTP REST, fail-safe latch, LED vermelho)
├── captures/                 # Arquivos de frames salvos (YYYYMMDD_HHMMSS.jpg/png)
├── logs/                     # Logs do monitoramento (vision_*.log)
└── requirements.txt          # Dependências
```

---

## 2. Configurações (`config.py`)

Todas as constantes são lidas de `.env`. Exemplo:

```bash
cp .env.example .env
# Editar .env com os valores reais:
VIDEO_SOURCE=0                      # 0 = webcam local, ou URL RTSP
ROI_COORDS=[100,100,320,240]       # [x,y,w,h]
CALIBRATION_SECONDS=3
ALERT_THRESHOLD=1.8
CRITICAL_THRESHOLD=3.5
ALERT_DEBOUNCE_FRAMES=10
CRITICAL_DEBOUNCE_FRAMES=5
FLOW_PYR_SCALE=0.5
FLOW_LEVELS=3
FLOW_WINSIZE=15
FLOW_ITERATIONS=3
FLOW_POLY_N=5
FLOW_POLY_SIGMA=1.2
FLOW_FLAGS=0
SHOW_HUD=true
TARGET_FPS=30
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_URL=sqlite:///./evolution.db
ESP32_IP=192.168.1.50
ESP32_PORT=80
RELAY_ACTIVE_LOW=true
WEBHOOK_URL=https://hooks.slack.com/...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ALERT_COOLDOWN_SECONDS=60
CRITICAL_COOLDOWN_SECONDS=0
DASHBOARD_REFRESH_SECONDS=2
MAX_TELEMETRY_POINTS=300
CSV_EXPORT_PATH=./exports
```

See `README.md` para passo a passo.

---

## 3. Arquitetura dos Módulos

### 3.1 `vision_monitor.py`
- **Captura resiliente**: Webcam local (`0`) ou stream RTSP via `cv2.VideoCapture(url)` com reconexão em loop.
- **ROI**: `[x, y, w, h]` para isolar a peça móvel; se `"auto"` → configurado interativamente.
- **Calibração**: primeiros `calibration_secs` (default 3 s) para baseline de movimento.
- **Detecção**: diferença de frames com filtragem Gaussiana + magnitude média como proxy vibração. (Farnebäck Farnebäck pode ser trocado por `cv2.calcOpticalFlowFarneback`).
- **Estado com Debounce**: NORMAL → ALERTA (≥ threshold × `alert_debounce` frames) → CRITICO (≥ critical × `critical_debounce`). Transições controladas.
- **Threads não-bloqueantes**: envio para API (`api_task`), notificações (`notif_task`) em `asyncio.create_task`.
- **HUD**: desenha ROI, FPS, status e magnitude na janela OpenCV.

### 3.2 `api.py`
- **FastAPI** no port 8000.
- Endpoints:
  - `POST /events` — recebe timestamp, status, value, frame_path.
  - `GET /events` — histórico paginado/filtrado.
  - `GET /status` — estado consolidado + ESP32 connected.
  - `POST /manual-stop` — proxy para E-Stop manual.
  - `POST /reset` — proxy para reset.
  - `GET /telemetry` — pontos em tempo real.
  - `GET /export/csv` — download CSV.
- Notificações assíncronas via **Webhook Discord/Slack** ou **Telegram Bot** com cooldown (60 s para ALERTA, imediato para CRITICO).
- Banco SQLite via SQLAlchemy ORM.

### 3.3 `dashboard.py`
- **Streamlit** auto-refresh configurável (`DASHBOARD_REFRESH_SECONDS`, default 2 s).
- Painel visual de status: banners amarelo/verde/vermelho pulsante.
- Gráfico de linha Plotly em tempo real da telemetria com linhas tracejadas nos thresholds.
- Tabela de ocorrências recentes com miniaturas de frames (base64).
- Controles: PARADA DE EMERGÊNCIA MANUAL, RESET DO SISTEMA, exportar CSV.
- Exportação via `requests` para o endpoint `/export/csv`.

### 3.4 `esp32_controller.ino`
- Servidor HTTP leve (WiFi reconectável) expondo `/emergency-stop`, `/reset`, `/status`.
- **Fail-Safe Latch**: após `POST /emergency-stop`, o relé é desligado e LED vermelho acende. O sistema **não recomeça** mesmo que o script Python pare.
- **Reset seguro**: só via botão físico (INPUT_PULLUP) com debounce (50 ms) ou chamada explícita `POST /reset`.
- LED vermelho piscando quando latched.

---

## 4. Pré-requisitos

| Recurso | Versão |
|---|---|
| Python | 3.10+ |
| OpenCV | 4.12.0.80 |
| numpy | 2.2.3 |
| fastapi | 0.115.0 |
| uvicorn | 0.34.0 |
| sqlalchemy | 2.0.40 |
| pydantic v2 | 2.11.0 |
| aiohttp | 3.9.1 |
| pandas | 2.2.2 |
| plotly | 5.18.0 |
| websockets (para teste ESP32) | 13.x |

Installar:

```bash
cd evolution
pip install -r requirements.txt
```

---

## 5. Passo a Passo de Configuração

### 5.1 Criar `.env`

```bash
cp .env.example .env
nano .env   # preencher:
# VIDEO_SOURCE=0
# ROI_COORDS=[100,100,320,240]
# CALIBRATION_SECONDS=3
# ALERT_THRESHOLD=1.8
# CRITICAL_THRESHOLD=3.5
# ALERT_DEBOUNCE_FRAMES=10
# CRITICAL_DEBOUNCE_FRAMES=5
# FLOW_PYR_SCALE=0.5
# FLOW_LEVELS=3
# FLOW_WINSIZE=15
# FLOW_ITERATIONS=3
# FLOW_POLY_N=5
# FLOW_POLY_SIGMA=1.2
# FLOW_FLAGS=0
# SHOW_HUD=true
# TARGET_FPS=30
# API_HOST=0.0.0.0
# API_PORT=8000
# DATABASE_URL=sqlite:///./evolution.db
# ESP32_IP=192.168.1.50
# ESP32_PORT=80
# RELAY_ACTIVE_LOW=true
# WEBHOOK_URL=https://hooks.slack.com/services/...
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
# ALERT_COOLDOWN_SECONDS=60
# CRITICAL_COOLDOWN_SECONDS=0
# DASHBOARD_REFRESH_SECONDS=2
# MAX_TELEMETRY_POINTS=300
# CSV_EXPORT_PATH=./exports
```

### 5.2 Iniciar o Backend (API)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Verificar:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/status
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"timestamp":"2026-01-01T00:00:00","status":"NORMAL","value":0.0,"frame_path":"/captures/20260101_000000.jpg","source":"vision"}'
```

### 5.3 Iniciar o Monitor Visual

```bash
python vision_monitor.py
```

O monitor roda em um loop de captura. Para testar sem hardware:

```bash
# Simulação de webcam (substitua pela URL RTSP)
python vision_monitor.py
```

### 5.4 Iniciar o Dashboard (Streamlit)

```bash
streamlit run dashboard.py
```

Acesse `http://localhost:8501` (padrão Streamlit). Auto-refresh a cada 2 s.

### 5.5 Iniciar o Firmware ESP32

Copie `esp32_controller.ino` para o seu ESP32, ajuste os pinos (`RELAY_PIN`, `LED_RED`, `BTN_RESET`) e suba. O ESP32 deve estar conectado ao Wi-Fi do ambiente industrial.

---

## 6. Testando o Fluxo Completo

1. **Configuração** — edite `.env` com os parâmetros.
2. **Backend** — `uvicorn api:app --reload` → API rodando em 8000.
3. **Monitor** — `python vision_monitor.py` → captura vídeo, calibra baseline, detecta anomalias.
4. **Dashboard** — `streamlit run dashboard.py` → verifica status, gráficos, eventos recentes.
5. **Notificações** — configure `WEBHOOK_URL` ou `TELEGRAM_BOT_TOKEN` para receber alertas Discord/Slack/Telegram.
6. **Firmware ESP32** — suba `esp32_controller.ino` no ESP32 conectado ao Wi-Fi. Teste:
   - `POST /emergency-stop` → relé desliga, LED vermelho acende, latched.
   - `POST /reset` → libera latch.
   - Botão físico pressionado → reset seguro.

### Simulando o ESP32 sem hardware

Se o hardware físico ainda não estiver conectado, use `curl`:

```bash
# Simular parada de emergência
curl -X POST http://localhost:8000/manual-stop \
  -H "Content-Type: application/json" \
  -d '{"user_id":"operator","reason":"simulação"}'

# Simular reset
curl -X POST http://localhost:8000/reset \
  -H "Content-Type: application/json" \
  -d '{"user_id":"operator","reason":"simulação"}'
```

---

## 6.1 IA de Diagnóstico (opcional)

O sistema inclui um **motor de diagnóstico por IA** (`diagnostic_ai.py`) que analisa a janela recente de telemetria e emite laudos estruturados (diagnóstico, causa provável, severidade, recomendação, confiança). Também inclui um **rastreio de erros** (janela deslizante) que dispara aviso automático quando a máquina ultrapassa `ERROR_BURST_LIMIT` erros/300s.

### Providers disponíveis

| Provider | Chave | Limite | Quando usar |
|---|---|---|---|
| `local` (padrão) | nenhuma | **Ilimitado** | Funciona offline; regras determinísticas de engenharia (ISO 10816) |
| `gemini` | `GEMINI_API_KEY` | free tier do Google (~15 RPM) | Melhor qualidade gratuita |
| `openrouter` | `OPENROUTER_API_KEY` | modelos `:free` | Alternativa gratuita |

### Ativar
```bash
# .env
AI_ENABLED=true
AI_PROVIDER=gemini          # ou openrouter / local
GEMINI_API_KEY=AIza...
# OPENROUTER_API_KEY=sk-or-v1-...
# AI_MODEL=gemini-2.0-flash
AI_COOLDOWN_SECONDS=30      # máximo 1 análise a cada 30s (evita spam)
ERROR_WINDOW_SECONDS=300    # janela de 5 min para contagem de erros
ERROR_BURST_LIMIT=5         # dispara aviso ao atingir 5 erros na janela
```

### Endpoints de diagnóstico (requer login JWT)
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r .access_token)

curl http://localhost:8000/diagnostics/stats -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/diagnostics/latest -H "Authorization: Bearer $TOKEN"
curl -X POST "http://localhost:8000/diagnostics/analyze?status_snapshot=CRITICO" \
  -H "Authorization: Bearer $TOKEN"
```

### Comportamento automático
- A cada evento **CRÍTICO**, a API agenda um diagnóstico em thread separada.
- Se a contagem de erros na janela atingir o limite, dispara análise automática e log `BURST de erros detectado`.

> **Verdade honesta:** nenhuma API de LLM é literalmente "ilimitada e grátis"; o provider `local` é a única opção ilimitada/gratuita permanente. Gemini/OpenRouter têm tiers grátis generosos, mas com limites.

---

## 7. Arquivo de Entrada de Execução

### `requirements.txt`
```text
uv==0.5
fastapi==0.115.0
uvicorn[standard]==0.34.0
sqlalchemy==2.0.40
pydantic>=2.11.0
starlette==0.45.2
aiohttp==3.9.1
pandas==2.2.2
plotly==5.18.0
opencv-python==4.12.0.80
numpy==2.2.3
python-telegram-bot==4.24.0
python-dotenv==1.1.0
structlog==23.2.0
```

### `config.py` — centraliza tudo.

### `vision_monitor.py` — visão computacional (OpenCV + NumPy).

### `api.py` — backend FastAPI + SQLite + Pydantic + notificações.

### `dashboard.py` — frontend Streamlit + Plotly/Pandas.

### `esp32_controller.ino` — firmware ESP32 (HTTP REST, fail-safe latch, LED vermelho).

---

## 8. Referências

- **Dense Optical Flow (Farnebäck)**: `cv2.calcOpticalFlowFarneback(prev, curr, None, iterations, flags, polyN, polySigma, winsize, level)`.
- **Debounce**: contar frames consecutivos acima do threshold antes de transição de estado.
- **Fail-Safe Latch**: uma vez `emergencyLatched = true`, o relé permanece desligado; só liberado via botão físico com debounce ou `POST /reset`.

---

*Documento gerado pelo Free Models Router — Engenheiro de Software Sênior & Arquiteto de Sistemas (Visão Computacional, IIoT, Full-Stack).*
