"""
Configuração centralizada do sistema de manutenção preditiva e segurança industrial.
Este módulo centraliza todos os parâmetros, URLs e constantes usadas pelos demais módulos.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env (se existir)
load_dotenv()

class Config:
    """
    Classe de configuração com todos os parâmetros do sistema.
    """
    
    # ===========================================================================
    # VISÃO COMPUTACIONAL (vision_monitor.py)
    # ===========================================================================
    
    # Fonte de vídeo: 0 para webcam local, ou URL RTSP (ex: rtsp://user:pass@192.168.1.100:554/stream)
    @property
    def VIDEO_SOURCE(self) -> int:
        return int(os.getenv("VIDEO_SOURCE", "0"))
    
    @property 
    def ROI_COORDS(self) -> list:
        """Região de Interesse (ROI) para isolar a peça móvel da máquina."""
        coords_str = os.getenv("ROI_COORDS", "auto")
        if coords_str.lower() == "auto":
            return None  # será configurado na inicialização
        try:
            return eval(coords_str)  # tipo: [x, y, width, height]
        except:
            raise ValueError(f"ROI_COORDS inválido: {coords_str}")
    
    @property
    def CALIBRATION_SECONDS(self) -> int:
        return int(os.getenv("CALIBRATION_SECONDS", "3"))
    
    @property
    def ALERT_THRESHOLD(self) -> float:
        return float(os.getenv("ALERT_THRESHOLD", "1.8"))
    
    @property
    def CRITICAL_THRESHOLD(self) -> float:
        return float(os.getenv("CRITICAL_THRESHOLD", "3.5"))
    
    @property
    def ALERT_DEBOUNCE_FRAMES(self) -> int:
        return int(os.getenv("ALERT_DEBOUNCE_FRAMES", "10"))
    
    @property
    def CRITICAL_DEBOUNCE_FRAMES(self) -> int:
        return int(os.getenv("CRITICAL_DEBOUNCE_FRAMES", "5"))
    
    @property
    def FLOW_PYR_SCALE(self) -> float:
        return float(os.getenv("FLOW_PYR_SCALE", "0.5"))
    
    @property
    def FLOW_LEVELS(self) -> int:
        return int(os.getenv("FLOW_LEVELS", "3"))
    
    @property
    def FLOW_WINSIZE(self) -> int:
        return int(os.getenv("FLOW_WINSIZE", "15"))
    
    @property
    def FLOW_ITERATIONS(self) -> int:
        return int(os.getenv("FLOW_ITERATIONS", "3"))
    
    @property
    def FLOW_POLY_N(self) -> int:
        return int(os.getenv("FLOW_POLY_N", "5"))
    
    @property
    def FLOW_POLY_SIGMA(self) -> float:
        return float(os.getenv("FLOW_POLY_SIGMA", "1.2"))
    
    @property
    def FLOW_FLAGS(self) -> int:
        return int(os.getenv("FLOW_FLAGS", "0"))
    
    @property
    def SHOW_HUD(self) -> bool:
        return os.getenv("SHOW_HUD", "true").lower() == "true"
    
    @property
    def TARGET_FPS(self) -> int:
        return int(os.getenv("TARGET_FPS", "30"))
    
    # ===========================================================================
    # BACKEND / API (api.py)
    # ===========================================================================
    
    @property
    def API_HOST(self) -> str:
        return os.getenv("API_HOST", "0.0.0.0")
    
    @property
    def API_PORT(self) -> int:
        return int(os.getenv("API_PORT", "8000"))
    
    @property
    def DATABASE_URL(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./evolution.db")
    
    @property
    def CORS_ORIGINS(self) -> list:
        origins_str = os.getenv("CORS_ORIGINS", "")
        if not origins_str:
            return []
        return [origin.strip() for origin in origins_str.split(",") if origin.strip()]
    
    # ===========================================================================
    # ESP32 CONTROLLER (esp32_controller.ino)
    # ===========================================================================
    
    @property
    def ESP32_IP(self) -> str:
        return os.getenv("ESP32_IP", "192.168.1.50")
    
    @property
    def ESP32_PORT(self) -> int:
        return int(os.getenv("ESP32_PORT", "80"))
    
    @property
    def ESP32_TIMEOUT_SECONDS(self) -> int:
        return int(os.getenv("ESP32_TIMEOUT_SECONDS", "3"))
    
    @property
    def RELAY_ACTIVE_LOW(self) -> bool:
        return os.getenv("RELAY_ACTIVE_LOW", "true").lower() == "true"
    
    # ===========================================================================
    # NOTIFICAÇÕES (Webhook Discord/Slack ou Telegram)
    # ===========================================================================
    
    @property
    def WEBHOOK_URL(self) -> str:
        return os.getenv("WEBHOOK_URL", "")
    
    @property
    def TELEGRAM_BOT_TOKEN(self) -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    @property
    def TELEGRAM_CHAT_ID(self) -> str:
        return os.getenv("TELEGRAM_CHAT_ID", "")
    
    @property
    def ALERT_COOLDOWN_SECONDS(self) -> int:
        return int(os.getenv("ALERT_COOLDOWN_SECONDS", "60"))
    
    @property
    def CRITICAL_COOLDOWN_SECONDS(self) -> int:
        return int(os.getenv("CRITICAL_COOLDOWN_SECONDS", "0"))
    
    # ===========================================================================
    # DASHBOARD (dashboard.py)
    # ===========================================================================
    
    @property
    def DASHBOARD_REFRESH_SECONDS(self) -> int:
        return int(os.getenv("DASHBOARD_REFRESH_SECONDS", "2"))
    
    @property
    def MAX_TELEMETRY_POINTS(self) -> int:
        return int(os.getenv("MAX_TELEMETRY_POINTS", "300"))
    
    @property
    def CSV_EXPORT_PATH(self) -> str:
        return os.getenv("CSV_EXPORT_PATH", "./exports")

    # ===========================================================================
    # DIRETÓRIOS
    # ===========================================================================

    @property
    def CAPTURES_DIR(self) -> str:
        return os.getenv("CAPTURES_DIR", "captures")

    @property
    def LOGS_DIR(self) -> str:
        return os.getenv("LOGS_DIR", "logs")

    # ===========================================================================
    # AUTENTICAÇÃO JWT (site logável + APK)
    # ===========================================================================

    @property
    def JWT_SECRET(self) -> str:
        return os.getenv("JWT_SECRET", "change-me-in-production-use-a-long-random-string")

    @property
    def JWT_EXPIRE_MINUTES(self) -> int:
        return int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

    # ===========================================================================
    # IA DE DIAGNÓSTICO (Gemini / OpenRouter / local ilimitado)
    # ===========================================================================

    @property
    def AI_ENABLED(self) -> bool:
        return os.getenv("AI_ENABLED", "true").lower() == "true"

    @property
    def AI_PROVIDER(self) -> str:
        # "ml" (open-source treinado) é o padrão; cai p/ "local" se não treinado
        return os.getenv("AI_PROVIDER", "ml")

    @property
    def GEMINI_API_KEY(self) -> str:
        return os.getenv("GEMINI_API_KEY", "")

    @property
    def OPENROUTER_API_KEY(self) -> str:
        return os.getenv("OPENROUTER_API_KEY", "")

    @property
    def AI_MODEL(self) -> str:
        return os.getenv("AI_MODEL", "")

    @property
    def AI_COOLDOWN_SECONDS(self) -> int:
        return int(os.getenv("AI_COOLDOWN_SECONDS", "30"))

    @property
    def ERROR_WINDOW_SECONDS(self) -> int:
        return int(os.getenv("ERROR_WINDOW_SECONDS", "300"))

    @property
    def ERROR_BURST_LIMIT(self) -> int:
        return int(os.getenv("ERROR_BURST_LIMIT", "5"))

    @property
    def ERROR_BURST_COOLDOWN(self) -> int:
        return int(os.getenv("ERROR_BURST_COOLDOWN", "120"))

# Instância global para uso em todo o sistema
config = Config()