@echo off
REM EVO·VISION — Vision Monitor (PC do cliente) — 1 clique
REM Usa VIDEO_SOURCE=auto do .env (detecta webcam via cabo).
REM Publica na nuvem quando NEON_STORAGE_ENABLED=true e API_BASE_URL apontam para a Vercel.
cd /d "%~dp0"
if not exist .env (
  echo .env nao encontrado. Copie .env.example para .env e ajuste primeiro.
  pause
  exit /b 1
)
if exist .venv\Scripts\python.exe (set PY=.venv\Scripts\python.exe) else (set PY=python)
echo Iniciando Vision Monitor... (Ctrl+C para encerrar)
%PY% vision_monitor.py
pause
