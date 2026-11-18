# EVO·VISION — PC do cliente (visão ao vivo publicando na nuvem)

A nuvem **não tem câmera**. A visão ao vivo roda neste PC do cliente (edge)
e publica na nuvem (Neon + Vercel) a cada ~2 s: frames no bucket privado e
eventos/telemetria na API. Sem este processo, `/snapshot` fica 404 honesto e o
HMI cai no feed DEMO sintético.

## 1. Pré-requisitos
- Windows/Linux com Python 3.12, webcam (USB ou integrada)
- Rede do cliente com saída HTTPS para `*.vercel.app` e `*.neon.tech`
- (Opcional) ESP32 E-Stop no estado: firmware `esp32_cloud_agent.ino` flasheado
  (ver `CLOUD_ONLY.md`) — sem ele, `/manual-stop`/`/reset` respondem 503 honesto.

## 2. Instalação
```bash
git clone <repo> && cd evolution
git checkout Visionard
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements-full.txt
```

## 3. Configuração (`.env` — nunca commitar)
Copie o `.env.example` (ou monte um `.env`) com:
```ini
DATABASE_URL=postgresql://...neon.tech/...?sslmode=require   # mesmo valor do servidor
JWT_SECRET=<o mesmo JWT_SECRET da Vercel>
TEMP_SOURCE=mock
API_BASE_URL=https://evolution-beta-six.vercel.app            # publica eventos na NUVEM
NEON_STORAGE_ENABLED=true                                     # sobe frames p/ bucket Neon
```
As credenciais `AWS_*` do bucket Neon (`AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) também são necessárias para o upload de frames
— pegue no painel Neon (Object Storage) ou peça ao integrador.
`JWT_SECRET` precisa ser igual ao da Vercel, senão o login no HMI funciona
mas chamadas autenticadas quebram.

## 4a. Alternativa de câmera
- Webcam USB/integrada (auto): `VIDEO_SOURCE=auto` no `.env` — detecta índices 0-3.
- Simulação sem hardware: `VIDEO_SOURCE=mock`.
- **ESP32 E-Stop + temperatura**: clique duplo em `flash_esp32.bat` (Windows)
  na pasta do repo — detecta a porta USB do ESP32, pergunta SSID/senha do Wi-Fi
  e grava o firmware (baixa arduino-cli/core ~300 MB só na 1ª vez). O firmware
  `esp32_cloud_agent.ino` compilado e validado. O ESP32 então aparece na nuvem
  em ~2 s (heartbeat), sem nenhuma porta aberta nem este PC.

## 4b. Dashboard na nuvem (sem cartão — Streamlit Community Cloud)
1. https://share.streamlit.io → Sign in com o **GitHub** (sua conta)
2. New app → repo `venomtids/evolution`, branch `Visionard`
3. Main file path: `streamlit_app/app.py`
   (o Cloud lê `streamlit_app/requirements.txt` — a raiz fica enxuta p/ a Vercel)
4. Advanced settings → Python 3.12 → em **Secrets**: `API_BASE_URL="https://evolution-beta-six.vercel.app"`
5. Deploy → URL pública do dashboard (grátis, sem cartão)

## 4. Rodar
```bash
python vision_monitor.py
```
Deixe como serviço (Task Scheduler no Windows / systemd no Linux) se quiser
que suba sozinho após reboot.

## 5. Verificar (de qualquer lugar)
```bash
B=https://evolution-beta-six.vercel.app
curl -s $B/health
curl -s $B/snapshot -L -o frame.jpg   # quando o edge está publicando
```

## 6. Comportamento quando o PC do cliente desliga
- `/events`, `/telemetry`, CSV, login, laudos de IA: **funcionam** (dados históricos).
- `/snapshot`: 404 honesto → HMI mostra feed DEMO sintético.
- `/manual-stop`, `/reset`: 503 honesto (sem ESP32) — com ESP32 flasheado e
  contatando, volta a enfileirar na nuvem mesmo sem PC.
- Temperatura: último valor do edge (o ESP32 físico, se flasheado, continua reportando).
