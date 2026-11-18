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
    
    # Fonte de vídeo: 0 para webcam local, URL RTSP, caminho de arquivo de vídeo,
    # ou "mock" para simulação (Codespaces/sem hardware).
    @property
    def VIDEO_SOURCE(self):
        raw = os.getenv("VIDEO_SOURCE", "0").strip()
        if raw.lower() in ("mock", "auto"):  # auto = detecta camera via cabo (indices 0-3)
            return raw.lower()
        try:
            return int(raw)
        except ValueError:
            return raw  # caminho de arquivo (ex: captures/simulacao.mp4) ou URL RTSP
    
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
    # APRENDIZADO DO PADRÃO DA MÁQUINA (pattern_learner.py — sem rótulos)
    # ===========================================================================

    @property
    def PATTERN_LEARNING_ENABLED(self) -> bool:
        """Liga o aprendizado/pontuação não supervisionada no Vision Monitor."""
        return os.getenv("PATTERN_LEARNING_ENABLED", "true").lower() == "true"

    @property
    def PATTERN_LEARNING_SECONDS(self) -> int:
        """Duração da observação inicial quando ainda não existe padrão salvo."""
        return int(os.getenv("PATTERN_LEARNING_SECONDS", "60"))

    @property
    def PATTERN_RELEARN_ON_START(self) -> bool:
        """Reaprende o padrão a cada inicialização (ignora modelo salvo)."""
        return os.getenv("PATTERN_RELEARN_ON_START", "false").lower() == "true"

    @property
    def PATTERN_WINDOW(self) -> int:
        return int(os.getenv("PATTERN_WINDOW", "40"))

    @property
    def PATTERN_STRIDE(self) -> int:
        return int(os.getenv("PATTERN_STRIDE", "5"))

    @property
    def PATTERN_SAMPLE_HZ(self) -> float:
        """Taxa fixa de amostragem do movimento (alinha treino histórico e ao vivo)."""
        return float(os.getenv("PATTERN_SAMPLE_HZ", "4"))

    @property
    def PATTERN_EVAL_SECONDS(self) -> float:
        return float(os.getenv("PATTERN_EVAL_SECONDS", "0.5"))

    @property
    def PATTERN_ALERT_EVALS(self) -> int:
        """Avaliações consecutivas em ALERTA para escalar o estado (equivale a segundos)."""
        return int(os.getenv("PATTERN_ALERT_EVALS", "4"))

    @property
    def PATTERN_CRITICAL_EVALS(self) -> int:
        return int(os.getenv("PATTERN_CRITICAL_EVALS", "8"))

    @property
    def PATTERN_ESCALATE_ALERTA(self) -> bool:
        """Padrão anômalo pode elevar o estado para ALERTA (aviso precoce)."""
        return os.getenv("PATTERN_ESCALATE_ALERTA", "true").lower() == "true"

    @property
    def PATTERN_ESCALATE_CRITICO(self) -> bool:
        """Padrão anômalo pode elevar para CRÍTICO/E-Stop (use com cautela)."""
        return os.getenv("PATTERN_ESCALATE_CRITICO", "false").lower() == "true"

    @property
    def PATTERN_SENSITIVITY(self) -> float:
        """>1 = mais sensível (limites menores); <1 = mais tolerante."""
        return float(os.getenv("PATTERN_SENSITIVITY", "1.0"))

    @property
    def PATTERN_MAX_CORE_FRACTION(self) -> float:
        """
        Fração máxima das janelas tratada como comportamento padrão.
        Menor = mais seletivo (descarta caudas/regimes raros de alta vibração).
        """
        return float(os.getenv("PATTERN_MAX_CORE_FRACTION", "0.99"))

    @property
    def PATTERN_MIN_CORE_FRACTION(self) -> float:
        """Fração mínima do padrão (evita modelo treinado com poucas janelas)."""
        return float(os.getenv("PATTERN_MIN_CORE_FRACTION", "0.50"))

    @property
    def PATTERN_ADAPT_ENABLED(self) -> bool:
        """Adapta o padrão ao drift natural (carga/velocidade/temperatura)."""
        return os.getenv("PATTERN_ADAPT_ENABLED", "true").lower() == "true"

    @property
    def PATTERN_ADAPT_INTERVAL_SECONDS(self) -> int:
        return int(os.getenv("PATTERN_ADAPT_INTERVAL_SECONDS", "300"))

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
    def API_BASE_URL(self) -> str:
        """Se definido (ex.: https://evolution-xyz.vercel.app), o Vision Monitor
        publica eventos direto nesse backend (nuvem) em vez de API_HOST:API_PORT."""
        return os.getenv("API_BASE_URL", "").strip()
    
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
    def ESP32_STOP_COOLDOWN_SECONDS(self) -> int:
        return int(os.getenv("ESP32_STOP_COOLDOWN_SECONDS", "5"))
    
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
    # TEMPERATURE SENSORS (thermal monitoring + camera integration)
    # ===========================================================================

    @property
    def TEMP_SENSOR_ENABLED(self) -> bool:
        return os.getenv("TEMP_SENSOR_ENABLED", "false").lower() == "true"

    @property
    def TEMP_SENSOR_TYPE(self) -> str:
        return os.getenv("TEMP_SENSOR_TYPE", "mock")  # mock|sensor|webhook

    @property
    def TEMP_MAX_ALERTA(self) -> float:
        return float(os.getenv("TEMP_MAX_ALERTA", "60.0"))

    @property
    def TEMP_MAX_CRITICO(self) -> float:
        return float(os.getenv("TEMP_MAX_CRITICO", "75.0"))

    @property
    def TEMP_SOURCE(self) -> str:
        return os.getenv("TEMP_SOURCE", "mock")  # mock|file|sensor|webhook

    @property
    def TEMP_FILE_PATH(self) -> str:
        return os.getenv("TEMP_FILE_PATH", "logs/temperature.log")

    @property
    def CAPTURES_DIR(self) -> str:
        return os.getenv("CAPTURES_DIR", "captures")

    @property
    def EVIDENCE_CAPTURE_ENABLED(self) -> bool:
        """Salva frames de evidência (ALERTA/CRITICO) em CAPTURES_DIR.
        ATENÇÃO: contém a imagem real da câmera (rosto/ambiente). Padrão desligado aqui por privacidade;
        ligue explicitamente com EVIDENCE_CAPTURE_ENABLED=true se quiser esse recurso."""
        return os.getenv("EVIDENCE_CAPTURE_ENABLED", "false").lower() == "true"

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