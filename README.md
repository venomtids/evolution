# MVP: Manutenção Preditiva e Segurança Industrial (E-Stop) — Visão Computacional, IIoT & Full-Stack

> **Arquitetura:** Python 3 + OpenCV + NumPy | C++/Arduino ESP32 | FastAPI + SQLite + Streamlit
> **Versão:** 1.0.0 | **Data:** Nov 2026

---

## ⚡ Início rápido no VS Code (computador local — recomendado)

> O **Codespaces não tem acesso à sua webcam** nem à sua rede (ESP32). Para o teste real, rode na sua máquina:

```bash
git clone https://github.com/venomtids/evolution
cd evolution
```

**Windows:** duplo clique em **`INICIAR.bat`** (ou no terminal do VS Code: `INICIAR.bat`)
**Linux / macOS:** `chmod +x INICIAR.sh && ./INICIAR.sh`

O script faz tudo: cria `.venv`, instala dependências, copia `.env`, troca para o OpenCV desktop (HUD), sobe **API + Dashboard + Vision Monitor na sua webcam** e abre o navegador.

| Atalho | URL |
|---|---|
| 🌐 **Site ao vivo (HMI "EVO·VISION")** | http://localhost:8000/ |
| 📊 **Dashboard Streamlit** | http://localhost:8501 |
| 📚 API Docs | http://localhost:8000/docs |

Login do site: `demo` / `demo1234` (ou registre o seu em `POST /auth/register`).

> Sem webcam ou quer simular? Use `./start_all.sh mock [normal|alerta|critico]`.
> Para parar tudo: `INICIAR` fecha as janelas (Windows) ou `./start_all.sh stop` (Linux/macOS).

---

## 1. Estrutura de Diretórios

```
evolution/
├── .env.example              # Template com todas as variáveis
├── .gitignore
├── INICIAR.bat               # ⚡ Setup + execução local (Windows/VS Code)
├── INICIAR.sh                # ⚡ Setup + execução local (Linux/macOS)
├── start_all.sh              # Orquestra API + Dashboard + Vision (mock|webcam|rtsp|stop)
├── test_flow.sh              # Teste E2E (register → login → eventos → burst → laudo IA)
├── train_ai.py               # CLI: treina a IA scikit-learn com os dados locais
├── learn_pattern.py          # CLI: aprende o padrão da máquina sem rótulos
├── pattern_learner.py        # IA não supervisionada do padrão de movimento
├── config.py                 # Centralização de thresholds, IPs, URLs, ROI
├── vision_monitor.py         # Captura vídeo, ROI, detecção Farnebäck, estado
├── diagnostic_ai.py          # Motor de diagnóstico por IA (ml/gemini/openrouter/local)
├── ml_model.py               # IA open-source treinada (IsolationForest + RandomForest)
├── api.py                    # Backend FastAPI (eventos, status, JWT, IA, /ml/*, HMI estática)
├── dashboard.py              # Frontend Streamlit (auto-refresh, HUD, gráficos, IA, treino)
├── esp32_controller.ino      # Firmware ESP32 (HTTP REST, fail-safe latch, LED vermelho)
├── frontend/                 # Site HMI "EVO·VISION" (HTML/CSS/JS, servido pela API na porta 8000)
├── captures/                 # Frames de evidência (YYYYMMDD_HHMMSS.jpg) + latest.jpg
├── logs/                     # Logs do monitoramento (vision_monitor.log, api.log...)
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
- **Detecção**: **Dense Optical Flow Farnebäck real** (`cv2.calcOpticalFlowFarneback`) restrito à ROI; índice de vibração = percentil 99 da magnitude do fluxo (robusto ao fundo estático e ruído).
- **Estado com Debounce**: NORMAL → ALERTA (≥ threshold × `alert_debounce` frames) → CRITICO (≥ critical × `critical_debounce`). Transições controladas.
- **Threads não-bloqueantes**: envio para API (`_send_to_api`), notificações (`_notify`), E-Stop ESP32 (`_trigger_esp32_stop`) em threads separadas.
- **HUD**: desenha ROI, FPS, status e magnitude na janela OpenCV (desativado automaticamente em ambientes headless; force com `SHOW_HUD`).
- **Snapshot ao vivo**: salva `captures/latest.jpg` a ~5 Hz para servir a câmera via API (`/snapshot`, `/stream`).

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
  - `GET /snapshot` — **frame atual da câmera** (JPEG, `Cache-Control: no-store`). `404` se o Vision Monitor não estiver rodando; `503` se o frame estiver desatualizado (>2 s).
  - `GET /stream` — **stream MJPEG** (`multipart/x-mixed-replace`) para embutir ao vivo em site/APK: `<img src="http://<host>:8000/stream">`.
  - `GET /diagnostics` / `GET /diagnostics/latest` / `GET /diagnostics/stats` / `POST /diagnostics/analyze` — laudos da IA (requer login JWT).
  - `POST /auth/register` / `POST /auth/login` / `GET /auth/me` — autenticação JWT para site logável e APK.
  - `GET /health` — health check.

> **Nota de segurança:** `/snapshot` e `/stream` são públicos por design (câmeras IP expostas costumam ser públicas; tags `<img>` não enviam headers `Authorization`). Em produção, proteja por rede/VLAN, firewall ou reverse proxy com autenticação.
- Notificações assíncronas via **Webhook Discord/Slack** ou **Telegram Bot** com cooldown (60 s para ALERTA, imediato para CRITICO).
- Banco SQLite via SQLAlchemy ORM.

### 3.3 `dashboard.py`
- **Streamlit** auto-refresh configurável (`DASHBOARD_REFRESH_SECONDS`, default 2 s).
- Painel visual de status: banners amarelo/verde/vermelho pulsante.
- Gráfico de linha Plotly em tempo real da telemetria com linhas tracejadas nos thresholds.
- Tabela de ocorrências recentes com miniaturas de frames (base64).
- **auto-refresh** de 2 s com o frame da câmera ao vivo (`/snapshot`) no painel principal e card de diagnóstico IA na barra lateral.
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

## 6.2 Vídeo ao vivo para o site logável / APK

Com o Vision Monitor rodando localmente (`./start_all.sh webcam` na sua máquina), o frame da câmera fica disponível na rede:

```html
<!-- Embutir direto em qualquer página do seu site -->
<img src="http://192.168.1.10:8000/stream" alt="Câmera ao vivo" />
<!-- ou capturar frames individuais -->
<img src="http://192.168.1.10:8000/snapshot" alt="Frame atual" />
```

O endpoint `/stream` devolve MJPEG (`multipart/x-mixed-replace`), compatível com browsers, VLC e players de MJPEG em Android/iOS. Para o APK (Flutter/React Native), use um componente de `Image`/`VideoView` apontando para `/stream`, ou faça polling de `/snapshot` a cada 500 ms.

> Troque `192.168.1.10` pelo IP da máquina que está rodando o Vision Monitor. Garanta que a máquina e o cliente estejam na mesma rede, ou exponha a API com um reverse proxy (nginx + TLS + autenticação).

---

## 6.3 IA open-source treinada para a nossa situação

Além da heurística local e dos provedores de nuvem, o projeto inclui uma **IA real treinada com os seus dados** (`ml_model.py`):

| Componente | Biblioteca (BSD) | Papel |
|---|---|---|
| **IsolationForest** | scikit-learn | Detector de anomalias **não-supervisionado** — aprende o comportamento normal de vibração e isola padrões anormais |
| **RandomForestClassifier** | scikit-learn | Classificador NORMAL/ALERTA/CRITICO treinado nos rótulos do histórico |

Features por janela de 60 amostras do índice Farnebäck (p99 da magnitude):
`mean, std, min, max, p90, p95, p99, range, iqr, delta_mean, alert_rate, critico_rate`.

### Treinar
```bash
python train_ai.py                  # usa o histórico do SQLite local
python train_ai.py --synthetic-only # bootstrap 100% sintético (instalação nova)
python train_ai.py --limit 5000     # só os últimos N eventos
```

Ou pela API/dashboard (requer JWT):
```bash
curl -X POST "http://localhost:8000/ml/train?synthetic_bootstrap=true" \
  -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/ml/status -H "Authorization: Bearer $TOKEN"
```

O dashboard exibe um card **🧠 IA Treinada (open-source)** com métricas e o botão **Treinar IA agora**.

### Ativar no laudo automático
```bash
# .env
AI_PROVIDER=ml      # sem modelo treinado → fallback automático para a heurística local
```

### Notas honestas
- Uma instalação nova tem zero histórico → o treino completa automaticamente com **dados sintéticos calibrados** (ou use `POST /ml/train` com `synthetic_bootstrap=false` para exigir dados reais).
- O `.joblib` treinado fica em `models/` (ignorado pelo git) — cada máquina treina o seu modelo com os seus dados.
- A acurácia reportada é sobre janelas do próprio treino; para validação honesta, separe um conjunto de teste ou reavalie após operar com dados reais.
- Quanto mais dias de telemetria real você acumula, melhor o IsolationForest aprende o "normal" da sua máquina.

---

## 6.4 Aprender o padrão da máquina SEM rótulos (só observando)

O `pattern_learner.py` é uma IA **não supervisionada**: ela não recebe
`NORMAL/ALERTA/CRITICO`, thresholds, diagnóstico nem qualquer informação
prévia. Ela só vê a série de movimento (índice Farnebäck amostrado a 4 Hz) e
descobre sozinha:

| Etapa | O que a IA faz |
|---|---|
| 1. Features | 22 features por janela: amplitude, dinâmica (tendência, autocorrelação), ritmo (espectro/faixas) e forma |
| 2. Padrão | Descobre as janelas densas (comportamento típico) por k-NN + recorte robusto mediana/MAD |
| 3. Regimes | KMeans separa os padrões normais recorrentes (ex.: parada, vazio, carga nominal) |
| 4. Limites | Calibra alerta/crítico pelos percentis do **próprio normal** (z-score robusto) |
| 5. Detecção | IsolationForest + z-score comparam cada nova janela com o padrão aprendido |

### Treinar agora

```bash
# 1) A partir do histórico do SQLite (IGNORA a coluna status)
python learn_pattern.py

# 2) Observando a câmera/vídeo ao vivo (o jeito mais fiel)
python learn_pattern.py --source 0 --seconds 120
python learn_pattern.py --source captures/simulation.mp4 --seconds 90
python learn_pattern.py --source mock --seconds 30

# 3) Pela API (JWT) ou botão do dashboard
curl -X POST "http://localhost:8000/ml/pattern/train?limit=50000" \
  -H "Authorization: Bearer $TOKEN"
curl http://localhost:8000/ml/pattern/status -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8000/ml/pattern/reset -H "Authorization: Bearer $TOKEN"
```

O modelo fica em `models/pattern_learner.joblib` (ignorado pelo git) e os
metadados legíveis em `models/pattern_metadata.json`.

### Aprendizado automático no Vision Monitor

Com `PATTERN_LEARNING_ENABLED=true` (padrão), na primeira execução o monitor
observa a máquina por `PATTERN_LEARNING_SECONDS` e aprende o padrão sozinho
(HUD mostra `PADRAO: APRENDENDO`). Nas próximas execuções ele carrega o modelo
salvo; `PATTERN_RELEARN_ON_START=true` força reaprender.

```bash
# .env
PATTERN_LEARNING_ENABLED=true
PATTERN_LEARNING_SECONDS=60      # observação inicial
PATTERN_WINDOW=40                # 10 s a 4 Hz
PATTERN_SAMPLE_HZ=4
PATTERN_SENSITIVITY=1.0          # >1 detecta desvios mais sutis
PATTERN_ESCALATE_ALERTA=true     # padrão anômalo gera ALERTA (aviso precoce)
PATTERN_ESCALATE_CRITICO=false   # true = padrão crítico também aciona E-Stop
PATTERN_ADAPT_ENABLED=true       # acompanha drift natural lentamente
```

### Notas honestas

- Aprenda o padrão com a máquina **em operação normal**. Se você treinar
  durante uma falha, a IA aprende aquilo como "normal" — ela não tem como
  saber o que é defeito sem alguém dizer (ou sem dados rotulados).
- Aprenda com a **mesma fonte** que será monitorada (mesma câmera/enquadramento/
  resolução). A escala do movimento muda de uma fonte para outra, então um
  padrão aprendido na webcam não vale para o vídeo de simulação, e vice-versa.
- O vídeo `captures/simulation_normal.mp4` (gerado por
  `python generate_simulation.py --mode normal --duration 60`) serve como
  gravação de referência de movimento padrão para testar o fluxo completo.
- Se a máquina tiver regimes normais muito diferentes (parada x carga x
  velocidade), todos podem ser aprendidos; o KMeans mostra quantos regimes
  foram encontrados.
- O padrão é um **aviso precoce**. O E-Stop por thresholds absolutos
  (`ALERT_THRESHOLD` / `CRITICAL_THRESHOLD`) continua ativo e independente.
- Para trocar de máquina/peça, rode `python learn_pattern.py --reset` e
  aprenda de novo.

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
