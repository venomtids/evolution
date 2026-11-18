@echo off
REM EVO.VISION - Gravar firmware no ESP32 (1 clique)
REM Detecta a porta USB, pergunta o Wi-Fi e grava. Nao instala nada permanente.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash_esp32.ps1"
