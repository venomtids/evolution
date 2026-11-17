#!/usr/bin/env bash
# start_all.sh — sobe API + Dashboard + Vision Monitor (modo mock ou webcam)
# Uso:
#   ./start_all.sh mock        # simula câmera (Codespaces / sem hardware)
#   ./start_all.sh webcam      # usa a webcam local (roda na sua máquina)
#   ./start_all.sh rtsp        # usa fonte RTSP configurada no .env
#   ./start_all.sh stop        # para tudo

set -e
cd "$(dirname "$0")"

MODE="${1:-mock}"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

stop_all() {
  echo "[stop] Finalizando serviços..."
  pkill -f "[u]vicorn api:app" 2>/dev/null || true
  pkill -f "[s]treamlit run dashboard.py" 2>/dev/null || true
  pkill -f "[v]ision_monitor.py" 2>/dev/null || true
  sleep 1
  echo "[stop] Pronto."
  exit 0
}

if [ "$MODE" = "stop" ]; then stop_all; fi

echo "=============================================="
echo " Iniciando sistema (modo: $MODE)"
echo "=============================================="

# 1) API
echo "[1/3] API FastAPI na porta 8000..."
nohup uvicorn api:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/api.log" 2>&1 &
echo $! > "$LOG_DIR/api.pid"

# 2) Dashboard Streamlit
echo "[2/3] Dashboard Streamlit na porta 8501..."
nohup streamlit run dashboard.py --server.port 8501 --server.headless true > "$LOG_DIR/dashboard.log" 2>&1 &
echo $! > "$LOG_DIR/dashboard.pid"

# 3) Vision Monitor
echo "[3/3] Vision Monitor..."
case "$MODE" in
  mock)
    nohup python vision_monitor.py --mock --mode normal > "$LOG_DIR/vision.log" 2>&1 &
    ;;
  webcam)
    nohup python vision_monitor.py > "$LOG_DIR/vision.log" 2>&1 &
    ;;
  rtsp)
    nohup python vision_monitor.py > "$LOG_DIR/vision.log" 2>&1 &
    ;;
  *)
    echo "Modo inválido: $MODE (use mock|webcam|rtsp|stop)"
    exit 1
    ;;
esac
echo $! > "$LOG_DIR/vision.pid"

sleep 3
echo ""
echo "Serviços no ar:"
echo "  - API:       http://localhost:8000/docs"
echo "  - Dashboard: http://localhost:8501"
echo "  - Vision:    logs/vision.log"
echo ""
echo "Para parar: ./start_all.sh stop"
