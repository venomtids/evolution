#!/usr/bin/env bash
# =============================================================================
# EVO-VISION — Instalação e execução local (Linux / macOS / Git Bash)
# Uso:  chmod +x INICIAR.sh && ./INICIAR.sh
# Faz TUDO: venv, dependências, .env, sobe API + Dashboard + Vision (webcam).
# =============================================================================
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  EVO-VISION — Setup e Execução Local"
echo "============================================"

PYTHON_BIN="$(command -v python3 || command -v python)" || {
  echo "[ERRO] Python 3.10+ não encontrado. Instale em https://www.python.org/downloads/"
  exit 1
}

# ---- 1) Ambiente virtual ----
if [ ! -d .venv ]; then
  echo "[1/4] Criando ambiente virtual .venv..."
  "$PYTHON_BIN" -m venv .venv
else
  echo "[1/4] Ambiente virtual .venv já existe."
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ---- 2) Dependências (uma vez) ----
if [ ! -f .venv/.deps_installed ]; then
  echo "[2/4] Instalando dependências..."
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  # Em desktop (com display), HUD precisa do OpenCV completo:
  if [ "${HEADLESS:-0}" != "1" ]; then
    pip uninstall -y opencv-python-headless >/dev/null 2>&1 || true
    pip install opencv-python
  fi
  echo ok > .venv/.deps_installed
else
  echo "[2/4] Dependências já instaladas."
fi

# ---- 3) .env ----
if [ ! -f .env ]; then
  echo "[3/4] Copiando .env.example para .env (edite-o se necessário)..."
  cp .env.example .env
else
  echo "[3/4] .env já existe."
fi

# ---- 4) Iniciar todos os serviços ----
echo "[4/4] Iniciando API, Dashboard e Vision Monitor (webcam)..."
./start_all.sh webcam

echo ""
echo "============================================"
echo "  PRONTO!"
echo "  - Site ao vivo (HMI): http://localhost:8000/"
echo "  - Dashboard:          http://localhost:8501"
echo "  - API Docs:           http://localhost:8000/docs"
echo ""
echo "  Webcam definida em VIDEO_SOURCE no .env (0 = primária)."
echo "  Para parar tudo: ./start_all.sh stop"
echo "============================================"

# Abre o navegador se possível
if command -v xdg-open >/dev/null; then xdg-open http://localhost:8501 >/dev/null 2>&1 || true
elif command -v open >/dev/null; then open http://localhost:8501 >/dev/null 2>&1 || true
fi
