@echo off
REM Reabre somente os servicos EVO-VISION em janelas cmd (sem setup).
cd /d "%~dp0"

start "EVO - MOCK ESP32" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && python mock_esp32.py --port 80"
start "EVO - API" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && uvicorn api:app --host 0.0.0.0 --port 8000"
start "EVO - Dashboard" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && streamlit run dashboard.py --server.port 8501"
start "EVO - Vision" cmd /k "cd /d "%~dp0" && call .venv\Scripts\activate.bat && python vision_monitor.py"
echo 4 janelas abertas.
