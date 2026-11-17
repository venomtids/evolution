@echo off
REM ==========================================================================
REM  EVO-VISION - Instalacao e execucao local (Windows + VS Code)
REM  Uso: duplo clique ou, no terminal do VS Code:  INICIAR.bat
REM  Abre 3 janelas: API (8000), Dashboard (8501), Vision (webcam).
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"
title EVO-VISION Setup

echo ============================================
echo   EVO-VISION - Setup e Execucao Local
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Python nao encontrado.
  echo        Instale o Python 3.10+ em https://www.python.org/downloads/
  echo        (marque "Add python.exe to PATH" no instalador)
  pause
  exit /b 1
)

REM ---- 1) Ambiente virtual ----
if not exist .venv (
  echo [1/5] Criando ambiente virtual .venv...
  python -m venv .venv
) else (
  echo [1/5] Ambiente virtual .venv ja existe.
)
call .venv\Scripts\activate.bat

REM ---- 2) Dependencias (uma vez) ----
if not exist .venv\.deps_installed (
  echo [2/5] Instalando dependencias (pode demorar alguns minutos)...
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  REM Desktop Windows tem display: troca headless por openCV completo (HUD)
  pip uninstall -y opencv-python-headless >nul 2>&1
  pip install opencv-python
  echo ok > .venv\.deps_installed
) else (
  echo [2/5] Dependencias ja instaladas.
)

REM ---- 3) .env ----
if not exist .env (
  echo [3/5] Copiando .env.example para .env (edite-o se necessario)...
  copy .env.example .env >nul
) else (
  echo [3/5] .env ja existe.
)

REM ---- 4) Iniciar servicos ----
echo [4/5] Iniciando API (porta 8000)...
start "EVO - API" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && uvicorn api:app --host 0.0.0.0 --port 8000"
echo [4/5] Iniciando Dashboard (porta 8501)...
start "EVO - Dashboard" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && streamlit run dashboard.py --server.port 8501"
echo [4/5] Iniciando Vision Monitor (webcam padrao, indice 0)...
start "EVO - Vision" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && python vision_monitor.py"

echo [5/5] Aguardando servicos subirem...
timeout /t 8 /nobreak >nul
start http://localhost:8000/
start http://localhost:8501

echo.
echo ============================================
echo   PRONTO!
echo   - Site ao vivo (HMI): http://localhost:8000/
echo   - Dashboard:          http://localhost:8501
echo   - API Docs:           http://localhost:8000/docs
echo.
echo   A webcam usada e a definida em VIDEO_SOURCE no .env (0 = primaria).
echo   Feche as 3 janelas pretas para parar tudo.
echo ============================================
pause
