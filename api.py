"""
api.py
Backend FastAPI para manutenção preditiva e segurança industrial (E-Stop).

Endpoints:
  - POST /events          : Recebe eventos de anomalia (timestamp, status, valor, frame)
  - GET  /events          : Histórico paginado/filtrado
  - GET  /status          : Estado atual consolidado da máquina
  - POST /manual-stop     : Proxy para E-Stop manual via ESP32
  - POST /reset           : Proxy para reset do sistema via ESP32
  - GET  /telemetry       : Dados de telemetria para dashboard (tempo real)
  - GET  /export/csv      : Exporta histórico filtrado em CSV
  - POST /auth/register   : Cadastro de usuário (site logável + APK)
  - POST /auth/login      : Login JWT
  - GET  /auth/me         : Dados do usuário autenticado
  - GET  /health          : Health check
"""

import os
import asyncio
import logging
import json
import bcrypt
import jwt
from datetime import datetime, timedelta
from typing import List, Optional
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, BackgroundTasks, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, Text, Index, select, func, event, text, Boolean
)
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.pool import StaticPool
import aiohttp
import pandas as pd
import io
import time

# Configuração centralizada
from config import config

# Motor de diagnóstico por IA
from diagnostic_ai import diagnostic_engine, DIAGNOSIS_SCHEMA

# ===========================================================================
# Configuração do Banco de Dados (SQLite + SQLAlchemy)
# ===========================================================================

DATABASE_URL = config.DATABASE_URL

# Motor:
# - Postgres (ex.: Neon na Vercel): SSL obrigatório, pool pequeno (serverless).
# - SQLite em arquivo (local): QueuePool com WAL + busy_timeout.
# - SQLite :memory: (testes): StaticPool obrigatório.
_is_postgres = DATABASE_URL.startswith("postgresql")
_is_memory = ":memory:" in DATABASE_URL
_engine_kwargs = dict(echo=False)

if _is_postgres:
    _engine_kwargs.update(
        connect_args={"sslmode": "require"},
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
    )
elif _is_memory:
    _engine_kwargs.update(connect_args={"check_same_thread": False, "timeout": 5}, poolclass=StaticPool)
else:
    _engine_kwargs.update(
        connect_args={"check_same_thread": False, "timeout": 5},
        pool_size=5,
        max_overflow=10,
    )

engine = create_engine(DATABASE_URL, **_engine_kwargs)

if not _is_postgres and not _is_memory:
    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

# expire_on_commit=False: objetos não expiram após commit (evita SELECT implícito
# pós-commit e falhas intermitentes em refresh sob concorrência).
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()


# ===========================================================================
# Modelos de Banco de Dados (ORM)
# ===========================================================================

class Event(Base):
    """Registro de evento de anomalia detectada."""
    __tablename__ = "events"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)  # NORMAL, ALERTA, CRITICO
    value = Column(Float, nullable=False)  # magnitude da anomalia
    temperature = Column(Float, nullable=True)  # temperatura lida no sensor (°C)
    frame_path = Column(Text, nullable=True)  # caminho do arquivo salvo
    source = Column(String(50), default="vision")  # visão, manual, api
    
    __table_args__ = (
        Index("ix_events_timestamp_status", "timestamp", "status"),
    )


class SystemLog(Base):
    """Log de ações do sistema (E-Stop manual, reset, etc.)."""
    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    action = Column(String(50), nullable=False)  # emergency_stop, reset, config_change
    details = Column(Text, nullable=True)
    user_id = Column(String(50), nullable=True)  # identificador do operador


class DeviceStatus(Base):
    """Estado do ESP32 físico que faz polling na API (relé de E-Stop na nuvem)."""
    __tablename__ = "device_status"

    device_id = Column(String(64), primary_key=True)
    last_seen = Column(DateTime, nullable=False, default=datetime.utcnow)
    temperature = Column(Float, nullable=True)
    latched = Column(Boolean, nullable=True)
    firmware = Column(String(32), nullable=True)
    detail = Column(Text, nullable=True)


class DeviceCommand(Base):
    """Comandos enfileirados para o ESP32 (ele busca na próxima consulta)."""
    __tablename__ = "device_commands"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    action = Column(String(32), nullable=False)  # emergency_stop | reset
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    delivered_at = Column(DateTime, nullable=True)
    acked_at = Column(DateTime, nullable=True)
    result = Column(Text, nullable=True)


class DeviceTemperature(Base):
    """Histórico térmico reportado pelo ESP32 (no máx. 1 ponto por minuto)."""
    __tablename__ = "device_temperature"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    temperature = Column(Float, nullable=False)


class User(Base):
    """Usuários do site logável e APK."""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="operator")  # operator, admin, viewer
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Integer, default=1)  # 1=ativo, 0=inativo


class Diagnostic(Base):
    """Laudos de diagnóstico gerados pela IA."""
    __tablename__ = "diagnostics"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    provider = Column(String(30), nullable=False)
    status_snapshot = Column(String(20), nullable=False)
    severidade = Column(String(20), nullable=False)
    diagnostico = Column(Text, nullable=False)
    causa_provavel = Column(Text, nullable=False)
    recomendacao = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    stats_json = Column(Text, nullable=True)


# Criar tabelas
if _is_postgres:
    # Serverless cold starts concorrentes podem rodar DDL ao mesmo tempo:
    # serializa com advisory lock para nao colidir no pg_type (Neon).
    with engine.begin() as _ddl_conn:
        _ddl_conn.execute(text("SELECT pg_advisory_lock(72727272)"))
        try:
            Base.metadata.create_all(bind=_ddl_conn)
        finally:
            _ddl_conn.execute(text("SELECT pg_advisory_unlock(72727272)"))
else:
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Migração leve: adiciona coluna "temperature" se não existir (DB legado)
# ---------------------------------------------------------------------------
def _ensure_temperature_column():
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE events ADD COLUMN temperature FLOAT"))
    except Exception as e:
        # Se a coluna já existe, ignorar; caso contrário, logar
        if "duplicate column" not in str(e).lower():
            logger = logging.getLogger(__name__)
            logger.debug(f"Skipping temperature column migration: {e}")


_ensure_temperature_column()


# ===========================================================================
# Schemas Pydantic (Validação e Serialização)
# ===========================================================================

class EventCreate(BaseModel):
    timestamp: Optional[datetime] = Field(default_factory=datetime.utcnow)
    status: str = Field(..., pattern="^(NORMAL|ALERTA|CRITICO)$")
    value: float
    temperature: Optional[float] = None
    frame_path: Optional[str] = None
    source: str = "vision"
    
    @field_validator("timestamp", mode='before')
    def default_timestamp(cls, v):
        return v or datetime.utcnow()


class EventResponse(BaseModel):
    id: int
    timestamp: datetime
    status: str
    value: float
    temperature: Optional[float] = None
    frame_path: Optional[str]
    source: str
    
    class Config:
        from_attributes = True


class EventListResponse(BaseModel):
    events: List[EventResponse]
    total: int
    page: int
    page_size: int


class StatusResponse(BaseModel):
    current_status: str  # NORMAL, ALERTA, CRITICO
    last_value: float
    last_update: datetime
    esp32_connected: bool
    events_last_hour: int


class TelemetryPoint(BaseModel):
    timestamp: datetime
    value: float
    status: str


class TelemetryResponse(BaseModel):
    points: List[TelemetryPoint]


class ManualActionRequest(BaseModel):
    user_id: Optional[str] = "operator"
    reason: Optional[str] = None


class ExportRequest(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[str] = None


# ===========================================================================
# Dependência de Banco de Dados (definida ANTES de ser usada)
# ===========================================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ===========================================================================
# Persistência de diagnósticos (callback injetado no motor de IA)
# ===========================================================================

def _persist_diagnosis(diagnosis: dict):
    """Salva laudo no banco via sessão efêmera (thread-safe)."""
    db = SessionLocal()
    try:
        d = Diagnostic(
            timestamp=datetime.utcnow(),
            provider=diagnosis.get("provider", "local"),
            status_snapshot=diagnosis.get("status_snapshot", "NORMAL"),
            severidade=diagnosis.get("severidade", "media"),
            diagnostico=diagnosis.get("diagnostico", ""),
            causa_provavel=diagnosis.get("causa_provavel", ""),
            recomendacao=diagnosis.get("recomendacao", ""),
            confidence=float(diagnosis.get("confidence", 0.0) or 0.0),
            stats_json=json.dumps(diagnosis.get("stats", {}), default=str),
        )
        db.add(d)
        db.commit()
    finally:
        db.close()


# Registra callback no motor de IA
diagnostic_engine.persist_callback = _persist_diagnosis

# Import necessário para stats_json
import json as _json_module  # já existe json abaixo; reforço para o callback

def _recent_events_dicts(db: Session, limit: int = 100) -> list:
    """Serializa eventos da janela para análise."""
    rows = db.execute(
        select(Event).order_by(Event.timestamp.desc()).limit(limit)
    ).scalars().all()
    out = []
    for e in rows:
        out.append({
            "timestamp": e.timestamp.isoformat(),
            "status": e.status,
            "value": e.value,
        })
    return out


def _run_diagnostic_now(status_snapshot: str = "CRITICO") -> dict:
    """Executa análise diagnóstica com sessão própria (thread-safe para BackgroundTasks)."""
    db = SessionLocal()
    try:
        events = _recent_events_dicts(db)
        return diagnostic_engine.analyze(events, status_snapshot or "NORMAL")
    finally:
        db.close()


# ===========================================================================
# Schemas de Autenticação (site logável + APK)
# ===========================================================================

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    email: Optional[str] = Field(None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field("operator", pattern="^(operator|admin|viewer)$")


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str]
    role: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class DiagnosticResponse(BaseModel):
    id: int
    timestamp: datetime
    provider: str
    status_snapshot: str
    severidade: str
    diagnostico: str
    causa_provavel: str
    recomendacao: str
    confidence: float
    
    class Config:
        from_attributes = True


class DiagnosticListResponse(BaseModel):
    diagnostics: List[DiagnosticResponse]
    total: int
    
    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ===========================================================================
# Serviço de Autenticação JWT
# ===========================================================================

class AuthService:
    """Hash de senhas (bcrypt) e emissão/validação de JWT."""
    
    def __init__(self):
        self.secret = os.getenv("JWT_SECRET", "change-me-in-production-use-a-long-random-string")
        self.algorithm = "HS256"
        self.expire_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8h default
    
    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    
    def verify_password(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except Exception:
            return False
    
    def create_token(self, user: User) -> str:
        expire = datetime.utcnow() + timedelta(minutes=self.expire_minutes)
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "exp": expire,
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)
    
    def decode_token(self, token: str) -> Optional[dict]:
        try:
            return jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None


auth_service = AuthService()

# HTTP Bearer scheme para FastAPI
security = HTTPBearer(auto_error=False)

def get_current_user(db: Session = Depends(get_db), creds: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> User:
    """Dependência: exige usuário autenticado."""
    if creds is None:
        raise HTTPException(status_code=401, detail="Token de autenticação ausente")
    payload = auth_service.decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")
    user = db.execute(select(User).where(User.id == int(payload["sub"]))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Usuário não encontrado ou inativo")
    return user


def require_role(*roles: str):
    """Dependência: exige uma das roles fornecidas."""
    def _checker(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"Permissão negada. Roles: {', '.join(roles)}")
        return user
    return _checker


# ===========================================================================
# Serviços de Notificação (Webhook / Telegram)
# ===========================================================================

class NotificationService:
    """Serviço assíncrono de notificações com cooldown."""
    
    def __init__(self):
        self.webhook_url = config.WEBHOOK_URL
        self.telegram_token = config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = config.TELEGRAM_CHAT_ID
        self.alert_cooldown = config.ALERT_COOLDOWN_SECONDS
        self.critical_cooldown = config.CRITICAL_COOLDOWN_SECONDS
        self._last_alert: Optional[datetime] = None
        self._last_critical: Optional[datetime] = None
    
    async def send_alert(self, event: EventCreate, frame_path: Optional[str] = None):
        """Envia alerta assíncrono se cooldown permitir."""
        now = datetime.utcnow()
        
        if event.status == "ALERTA":
            if self._last_alert and (now - self._last_alert).total_seconds() < self.alert_cooldown:
                return
            self._last_alert = now
        elif event.status == "CRITICO":
            if self._last_critical and (now - self._last_critical).total_seconds() < self.critical_cooldown:
                return
            self._last_critical = now
        else:
            return
        
        # Prepara mensagem
        msg = (
            f"🚨 **ALERTA INDUSTRIAL** 🚨\n"
            f"Status: **{event.status}**\n"
            f"Valor: {event.value:.3f}\n"
            f"Timestamp: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        # Envia via Webhook (Discord/Slack)
        if self.webhook_url:
            await self._send_webhook(msg, frame_path)
        
        # Envia via Telegram
        if self.telegram_token and self.telegram_chat_id:
            await self._send_telegram(msg, frame_path)
    
    async def _send_webhook(self, message: str, frame_path: Optional[str]):
        payload = {"content": message} if "discord" in self.webhook_url.lower() else {"text": message}
        
        async with aiohttp.ClientSession() as session:
            try:
                if frame_path and Path(frame_path).exists():
                    # Upload de arquivo para Discord
                    if "discord" in self.webhook_url.lower():
                        data = aiohttp.FormData()
                        data.add_field('content', message)
                        with open(frame_path, 'rb') as f:
                            data.add_field('file', f, filename=Path(frame_path).name)
                        await session.post(self.webhook_url, data=data)
                    else:
                        # Slack webhook simples
                        await session.post(self.webhook_url, json=payload)
                else:
                    await session.post(self.webhook_url, json=payload)
                logger.info(f"Webhook enviado: {event.status}")
            except Exception as e:
                logger.error(f"Falha ao enviar webhook: {e}")
    
    async def _send_telegram(self, message: str, frame_path: Optional[str]):
        """Envia mensagem e foto via Telegram Bot API."""
        base_url = f"https://api.telegram.org/bot{self.telegram_token}"
        
        async with aiohttp.ClientSession() as session:
            try:
                # Envia texto
                await session.post(
                    f"{base_url}/sendMessage",
                    json={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"}
                )
                
                # Envia foto se houver
                if frame_path and Path(frame_path).exists():
                    with open(frame_path, 'rb') as f:
                        data = aiohttp.FormData()
                        data.add_field('chat_id', self.telegram_chat_id)
                        data.add_field('photo', f, filename=Path(frame_path).name)
                        await session.post(f"{base_url}/sendPhoto", data=data)
                
                logger.info(f"Telegram enviado: {event.status}")
            except Exception as e:
                logger.error(f"Falha ao enviar Telegram: {e}")


notification_service = NotificationService()


# ===========================================================================
# Serviço de Comunicação com ESP32
# ===========================================================================

class ESP32Service:
    """Cliente HTTP assíncrono para o ESP32 Controller."""
    
    def __init__(self):
        self.base_url = f"http://{config.ESP32_IP}:{config.ESP32_PORT}"
        self.timeout = config.ESP32_TIMEOUT_SECONDS
    
    async def emergency_stop(self) -> dict:
        """Aciona parada de emergência no ESP32."""
        return await self._post("/emergency-stop", {})
    
    async def reset(self) -> dict:
        """Reseta o sistema no ESP32."""
        return await self._post("/reset", {})
    
    async def get_status(self) -> dict:
        """Obtém status do ESP32."""
        return await self._get("/status")
    
    async def _post(self, endpoint: str, data: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=self.timeout) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"ESP32 POST {endpoint} falhou: {e}")
            return {"success": False, "error": str(e)}
    
    async def _get(self, endpoint: str) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=self.timeout) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"ESP32 GET {endpoint} falhou: {e}")
            return {"success": False, "error": str(e)}


esp32_service = ESP32Service()


# ===========================================================================
# Relé de dispositivo (ESP32 fora da LAN) — comando enfileirado na nuvem
# O ESP32 físico faz polling em /device/heartbeat (HTTPS de saída, sem portas
# abertas na rede). Honestidade: o caminho de nuvem só existe quando
# DEVICE_TOKEN está configurado E o dispositivo deul heartbeat nos últimos
# DEVICE_RELAY_WINDOW_SECONDS. Sem isso, /manual-stop e /reset seguem 503.
# ===========================================================================

DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "").strip()
DEVICE_RELAY_WINDOW = int(os.getenv("DEVICE_RELAY_WINDOW_SECONDS", "15"))


class DeviceHeartbeatIn(BaseModel):
    device_id: str = "esp32-01"
    temperature: Optional[float] = None
    latched: Optional[bool] = None
    firmware: Optional[str] = None


class DeviceAckIn(BaseModel):
    device_id: str
    command_id: int
    result: Optional[str] = None


def _device_fresh(db: Session, device_id: Optional[str] = None):
    """Último heartbeat dentro da janela de frescor, ou None."""
    q = select(DeviceStatus).where(
        DeviceStatus.last_seen >= datetime.utcnow() - timedelta(seconds=DEVICE_RELAY_WINDOW)
    )
    if device_id:
        q = q.where(DeviceStatus.device_id == device_id)
    return db.execute(q.order_by(DeviceStatus.last_seen.desc()).limit(1)).scalar_one_or_none()


def _device_auth(x_device_token: Optional[str]) -> None:
    if not DEVICE_TOKEN:
        raise HTTPException(503, "Relé de dispositivo desativado (DEVICE_TOKEN não configurado).")
    if (x_device_token or "").strip() != DEVICE_TOKEN:
        raise HTTPException(401, "Token de dispositivo inválido.")


def _enqueue_device_command(db: Session, action: str, user_id: Optional[str], reason: Optional[str]):
    dev = _device_fresh(db)
    if dev is None:
        return None
    cmd = DeviceCommand(device_id=dev.device_id, action=action)
    db.add(cmd)
    db.add(SystemLog(
        action=f"{action}_queued",
        details=f"{action} por {user_id or 'operator'}: enfileirado na nuvem para {dev.device_id} ({reason or 'sem motivo'})",
        user_id=user_id or "operator",
    ))
    db.commit()
    db.refresh(cmd)
    return {
        "success": True,
        "queued": True,
        "command_id": cmd.id,
        "message": "Comando enfileirado na nuvem — o ESP32 executa no próximo contato (~2s) e confirma. A máquina NÃO liga sozinha.",
    }


# ===========================================================================
# Ciclo de Vida da Aplicação (Lifespan)
# ===========================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("API iniciada - tabelas criadas, serviços prontos")
    yield
    # Shutdown
    logger.info("API encerrada")


# ===========================================================================
# Instância FastAPI
# ===========================================================================

app = FastAPI(
    title="Industrial Predictive Maintenance API",
    description="Backend para monitoramento por vídeo, E-Stop e telemetria",
    version="1.0.0",
    lifespan=lifespan
)

# CORS para dashboard Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logger (definido ANTES de ser usado)
logger = logging.getLogger(__name__)


@app.post("/device/heartbeat")
async def device_heartbeat(
    body: DeviceHeartbeatIn,
    x_device_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Contato periódico do ESP32 físico (2s típico): atualiza estado, registra
    temperatura (1 ponto/min no máx.) e devolve comandos pendentes.
    """
    _device_auth(x_device_token)
    now = datetime.utcnow()
    row = db.get(DeviceStatus, body.device_id)
    if row is None:
        row = DeviceStatus(device_id=body.device_id, last_seen=now,
                           temperature=body.temperature, latched=body.latched,
                           firmware=body.firmware)
        db.add(row)
    else:
        row.last_seen = now
        if body.temperature is not None:
            row.temperature = body.temperature
        if body.latched is not None:
            row.latched = body.latched
        if body.firmware:
            row.firmware = body.firmware
    if body.temperature is not None:
        last_t = db.execute(
            select(func.max(DeviceTemperature.ts)).where(DeviceTemperature.device_id == body.device_id)
        ).scalar()
        if last_t is None or (now - last_t) >= timedelta(seconds=60):
            db.add(DeviceTemperature(ts=now, device_id=body.device_id, temperature=body.temperature))
    pending = db.execute(
        select(DeviceCommand)
        .where(DeviceCommand.device_id == body.device_id, DeviceCommand.delivered_at.is_(None))
        .order_by(DeviceCommand.id)
    ).scalars().all()
    commands = [{"id": c.id, "action": c.action} for c in pending]
    for c in pending:
        c.delivered_at = now
    db.commit()
    return {"ok": True, "server_time": now.isoformat(), "commands": commands}


@app.post("/device/ack")
async def device_ack(
    body: DeviceAckIn,
    x_device_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """ESP32 confirma execução de um comando enfileirado (fechando o ciclo honesto)."""
    _device_auth(x_device_token)
    cmd = db.get(DeviceCommand, body.command_id)
    if cmd is None or cmd.device_id != body.device_id:
        raise HTTPException(404, "Comando não encontrado para este dispositivo.")
    cmd.acked_at = datetime.utcnow()
    cmd.result = (body.result or "")[:500]
    db.add(SystemLog(
        action=f"{cmd.action}_ack",
        details=f"ACK do ESP32 (cmd {cmd.id}): {cmd.result or 'ok'}",
        user_id="esp32",
    ))
    db.commit()
    return {"ok": True}


# ===========================================================================
# Endpoints da API
# ===========================================================================

@app.post("/events", response_model=EventResponse, status_code=201)
async def create_event(
    event: EventCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Registra um novo evento de anomalia.
    Dispara notificação assíncrona via background task.
    """
    db_event = Event(**event.model_dump())
    db.add(db_event)
    db.commit()
    try:
        db.refresh(db_event)
    except Exception as e:
        # Não-crítico: o objeto já tem os dados (expire_on_commit=False)
        logger.warning(f"refresh do evento falhou (ignorado): {e}")
    
    # Notificação assíncrona
    background_tasks.add_task(notification_service.send_alert, event, event.frame_path)
    
    # Rastreamento de erros (janela deslizante) -> aviso de burst
    burst = diagnostic_engine.track_event(event.status)
    if burst:
        logger.warning(f"BURST de erros detectado! status={event.status}")
        background_tasks.add_task(_run_diagnostic_now, event.status)
    elif event.status == "CRITICO":
        # Diagnóstico automático imediato em CRÍTICO
        background_tasks.add_task(_run_diagnostic_now, event.status)
    
    return db_event


@app.post("/events/upload", response_model=EventResponse, status_code=201)
async def create_event_with_upload(
    background_tasks: BackgroundTasks,
    status: str = Form(...),
    value: float = Form(...),
    timestamp: Optional[str] = Form(None),
    source: str = Form("vision"),
    frame: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Registra evento com upload de frame (multipart/form-data).
    Salva o frame em disco e registra o caminho.
    """
    # Valida status
    if status not in ("NORMAL", "ALERTA", "CRITICO"):
        raise HTTPException(400, "Status inválido")
    
    # Salva frame
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    ext = Path(frame.filename).suffix or ".jpg"
    filename = f"{ts}{ext}"
    filepath = Path(config.CAPTURES_DIR) / filename
    
    Path(config.CAPTURES_DIR).mkdir(parents=True, exist_ok=True)
    content = await frame.read()
    with open(filepath, "wb") as f:
        f.write(content)
    
    # Cria evento
    event_data = EventCreate(
        timestamp=datetime.fromisoformat(timestamp) if timestamp else datetime.utcnow(),
        status=status,
        value=value,
        frame_path=str(filepath),
        source=source
    )
    
    db_event = Event(**event_data.model_dump())
    db.add(db_event)
    db.commit()
    try:
        db.refresh(db_event)
    except Exception as e:
        logger.warning(f"refresh do evento falhou (ignorado): {e}")
    
    # Notificação assíncrona
    background_tasks.add_task(notification_service.send_alert, event_data, str(filepath))
    
    return db_event


@app.get("/events", response_model=EventListResponse)
async def list_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, pattern="^(NORMAL|ALERTA|CRITICO)$"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db)
):
    """
    Lista eventos com paginação e filtros.
    """
    query = select(Event)
    
    if status:
        query = query.where(Event.status == status)
    if start_date:
        query = query.where(Event.timestamp >= start_date)
    if end_date:
        query = query.where(Event.timestamp <= end_date)
    
    query = query.order_by(Event.timestamp.desc())
    
    # Total
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    
    # Paginação
    offset = (page - 1) * page_size
    events = db.execute(query.offset(offset).limit(page_size)).scalars().all()
    
    return EventListResponse(
        events=[EventResponse.model_validate(e) for e in events],
        total=total,
        page=page,
        page_size=page_size
    )


@app.get("/status", response_model=StatusResponse)
async def get_status(db: Session = Depends(get_db)):
    """
    Retorna estado atual consolidado da máquina.
    """
    # Último evento
    last_event = db.execute(
        select(Event).order_by(Event.timestamp.desc()).limit(1)
    ).scalar_one_or_none()
    
    # Contagem últimas 24h
    since = datetime.utcnow() - timedelta(hours=24)
    events_24h = db.scalar(
        select(func.count(Event.id)).where(Event.timestamp >= since)
    )
    
    # Status ESP32 (conectado quando responde sem erro)
    esp32_status = await esp32_service.get_status()
    esp32_connected = isinstance(esp32_status, dict) and "error" not in esp32_status
    
    # Erros na janela (para /status)
    errors_window = diagnostic_engine.error_tracker.count()
    
    return StatusResponse(
        current_status=last_event.status if last_event else "NORMAL",
        last_value=last_event.value if last_event else 0.0,
        last_update=last_event.timestamp if last_event else datetime.utcnow(),
        esp32_connected=esp32_connected,
        events_last_hour=events_24h or 0
    )


@app.get("/telemetry", response_model=TelemetryResponse)
async def get_telemetry(
    limit: int = Query(config.MAX_TELEMETRY_POINTS, ge=1, le=1000),
    db: Session = Depends(get_db)
):
    """
    Retorna pontos de telemetria para gráfico em tempo real.
    """
    events = db.execute(
        select(Event)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    ).scalars().all()
    
    # Inverte para ordem cronológica
    events = list(reversed(events))
    
    points = [
        TelemetryPoint(
            timestamp=e.timestamp,
            value=e.value,
            status=e.status
        )
        for e in events
    ]
    
    return TelemetryResponse(points=points)


@app.get("/temperature", response_model=dict)
async def get_temperature(db: Session = Depends(get_db)):
    """Retorna a temperatura atual do sistema (último evento com leitura térmica)."""
    row = db.execute(
        select(Event)
        .where(Event.temperature.isnot(None))
        .order_by(Event.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    dev = db.execute(
        select(DeviceStatus)
        .where(DeviceStatus.temperature.isnot(None))
        .order_by(DeviceStatus.last_seen.desc())
        .limit(1)
    ).scalar_one_or_none()
    # Fonte mais recente vence (evento com leitura térmica vs. ESP32 pela nuvem)
    if dev is not None and (row is None or dev.last_seen >= row.timestamp):
        temperature, source, ts = dev.temperature, "device", dev.last_seen
    elif row is not None:
        temperature, source, ts = row.temperature, config.TEMP_SOURCE, row.timestamp
    else:
        temperature, source, ts = 0.0, config.TEMP_SOURCE, datetime.utcnow()
    return {
        "temperature": temperature,
        "source": source,
        "thresholds": {
            "alerta": config.TEMP_MAX_ALERTA,
            "critico": config.TEMP_MAX_CRITICO,
        },
        "timestamp": ts.isoformat(),
    }


@app.get("/temperature/history", response_model=dict)
async def get_temperature_history(
    limit: int = Query(300, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Histórico de leituras térmicas (para gráfico no dashboard)."""
    rows = db.execute(
        select(Event)
        .where(Event.temperature.isnot(None))
        .order_by(Event.timestamp.desc())
        .limit(limit)
    ).scalars().all()
    dev_rows = db.execute(
        select(DeviceTemperature)
        .order_by(DeviceTemperature.ts.desc())
        .limit(limit)
    ).scalars().all()
    # Mescla eventos térmicos + leituras do ESP32 pela nuvem, ordenado por tempo
    merged = [{"timestamp": r.timestamp.isoformat(), "temperature": r.temperature, "status": r.status} for r in rows]
    merged += [{"timestamp": r.ts.isoformat(), "temperature": r.temperature, "status": "NORMAL"} for r in dev_rows]
    merged.sort(key=lambda p: p["timestamp"])
    points = merged[-limit:]
    return {
        "points": points
    }


@app.post("/manual-stop", response_model=dict)
async def manual_emergency_stop(
    request: ManualActionRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Aciona parada de emergência manual via ESP32.
    Registra ação no log do sistema.
    """
    # Tenta acionar o ESP32 de verdade; em falha, responde 503 honesto
    # (nunca finge sucesso nem grava evento CRITICO de mentira).
    result = await esp32_service.emergency_stop()
    esp_ok = bool(result.get("success") is True)

    # Log da tentativa (sem fingir que a máquina parou)
    log = SystemLog(
        action="emergency_stop",
        details=f"Manual stop by {request.user_id}: {request.reason or 'Sem motivo'} | esp32_ok={esp_ok}",
        user_id=request.user_id
    )
    db.add(log)

    if not esp_ok:
        # Relé pela nuvem: ESP32 físico que faz polling no /device/heartbeat?
        queued = _enqueue_device_command(db, "emergency_stop", request.user_id, request.reason)
        if queued is not None:
            return queued
        db.commit()
        raise HTTPException(
            503,
            f"ESP32 inalcançável em {esp32_service.base_url} — a parada física NÃO foi executada. Verifique a rede do controlador."
        )

    # ESP32 confirmou: registra evento CRITICO correspondente
    event = Event(
        timestamp=datetime.utcnow(),
        status="CRITICO",
        value=999.0,
        frame_path=None,
        source="manual"
    )
    db.add(event)
    db.commit()

    return {"success": True, "esp32_response": result}


@app.post("/reset", response_model=dict)
async def reset_system(
    request: ManualActionRequest,
    db: Session = Depends(get_db)
):
    """
    Reseta o sistema via ESP32 (requer botão físico ou comando explícito).
    Registra ação no log do sistema.
    """
    # Tenta o reset no ESP32 de verdade; em falha, 503 honesto.
    result = await esp32_service.reset()
    esp_ok = bool(result.get("success") is True)

    log = SystemLog(
        action="reset",
        details=f"Reset by {request.user_id}: {request.reason or 'Sem motivo'} | esp32_ok={esp_ok}",
        user_id=request.user_id
    )
    db.add(log)

    if not esp_ok:
        queued = _enqueue_device_command(db, "reset", request.user_id, request.reason)
        if queued is not None:
            return queued
        db.commit()
        raise HTTPException(
            503,
            f"ESP32 inalcançável em {esp32_service.base_url} — o latch NÃO foi liberado. Verifique a rede do controlador."
        )

    # ESP32 confirmou: registra evento NORMAL correspondente
    event = Event(
        timestamp=datetime.utcnow(),
        status="NORMAL",
        value=0.0,
        frame_path=None,
        source="manual"
    )
    db.add(event)
    db.commit()

    return {"success": True, "esp32_response": result}


@app.get("/export/csv")
async def export_csv(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Exporta histórico filtrado em CSV.
    """
    query = select(Event)
    
    if status:
        query = query.where(Event.status == status)
    if start_date:
        query = query.where(Event.timestamp >= start_date)
    if end_date:
        query = query.where(Event.timestamp <= end_date)
    
    query = query.order_by(Event.timestamp.desc())
    events = db.execute(query).scalars().all()
    
    # Converte para DataFrame
    data = [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "status": e.status,
            "value": e.value,
            "frame_path": e.frame_path,
            "source": e.source
        }
        for e in events
    ]
    
    df = pd.DataFrame(data)
    
    # Stream CSV
    stream = io.StringIO()
    df.to_csv(stream, index=False)
    stream.seek(0)
    
    filename = f"events_export_{datetime.utcnow():%Y%m%d_%H%M%S}.csv"
    
    return StreamingResponse(
        io.BytesIO(stream.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ===========================================================================
# Endpoints de Vídeo ao Vivo (snapshot + stream MJPEG)
# ===========================================================================

SNAPSHOT_PATH = Path(config.CAPTURES_DIR) / "latest.jpg"
SNAPSHOT_MAX_AGE_SECONDS = 2.0  # considera "sem sinal" se o último frame for mais velho


async def _snapshot_from_bucket():
    """
    Fallback de nuvem: quando não há latest.jpg local (deploy serverless, sem
    Vision Monitor neste processo), devolve um redirect 302 para uma URL
    pré-assinada do objeto snapshots/latest.jpg no Neon Object Storage.
    Retorna None quando o bucket não está configurado ou o objeto não existe.
    """
    endpoint = os.getenv("AWS_ENDPOINT_URL_S3")
    ak = os.getenv("AWS_ACCESS_KEY_ID")
    sk = os.getenv("AWS_SECRET_ACCESS_KEY")
    bucket = os.getenv("NEON_STORAGE_BUCKET", "assets")
    key = "snapshots/latest.jpg"
    if not (endpoint and ak and sk):
        return None
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3", endpoint_url=endpoint, region_name=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key_id=ak, aws_secret_access_key=sk,
            config=Config(signature_version="s3v4"),
        )
        head = client.head_object(Bucket=bucket, Key=key)
        age = time.time() - head["LastModified"].timestamp()
        if age > 30:
            raise HTTPException(503, f"Frame da nuvem desatualizado ({age:.0f}s). O edge que publica snapshots está offline?")
        url = client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=30
        )
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store, max-age=0"})
    except HTTPException:
        raise
    except Exception as e:
        logger.debug(f"snapshot via bucket indisponível: {e}")
        return None


@app.get("/snapshot")
async def get_snapshot():
    """
    Último frame capturado pelo Vision Monitor (JPEG).
    Local: arquivo captures/latest.jpg. Nuvem (serverless): redirect 302 para
    URL pré-assinada do bucket Neon (edge publica a cada ~2s via storage.py).
    Público por design (câmeras IP / <img> do site logável não enviam headers).
    """
    if SNAPSHOT_PATH.exists():
        age = time.time() - SNAPSHOT_PATH.stat().st_mtime
        if age <= SNAPSHOT_MAX_AGE_SECONDS:
            return FileResponse(
                SNAPSHOT_PATH,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store, max-age=0"},
            )
        cloud = await _snapshot_from_bucket()
        if cloud is not None:
            return cloud
        raise HTTPException(503, f"Frame desatualizado ({age:.1f}s). Verifique o Vision Monitor.")
    cloud = await _snapshot_from_bucket()
    if cloud is not None:
        return cloud
    raise HTTPException(404, "Nenhum frame recente. O Vision Monitor (edge) está publicando no bucket?")


@app.get("/stream")
async def stream_mjpeg():
    """
    Stream MJPEG (multipart/x-mixed-replace) para embutir ao vivo:
        <img src="http://<host>:8000/stream">
    Funciona em browsers, VLC, img/APKs com suporte a multipart MJPEG.
    """
    BOUNDARY = b"frame"

    def generate():
        last_mtime = 0.0
        while True:
            try:
                if SNAPSHOT_PATH.exists():
                    mtime = SNAPSHOT_PATH.stat().st_mtime
                    if mtime != last_mtime:
                        data = SNAPSHOT_PATH.read_bytes()
                        last_mtime = mtime
                        yield (
                            b"--" + BOUNDARY + b"\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                            + data
                            + b"\r\n"
                        )
            except Exception:
                pass
            time.sleep(0.03)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/motion")
async def motion_state():
    """
    Estado de movimento do Vision Monitor (localização): célula 3x3 mais agitada,
    direção dominante, magnitudes por célula. Atualizado ~4 Hz pelo monitor.
    """
    path = Path(config.LOGS_DIR) / "motion_state.json"
    if not path.exists():
        raise HTTPException(404, "Sem estado de movimento. O Vision Monitor está rodando?")
    age = time.time() - path.stat().st_mtime
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(503, "Estado de movimento ilegível — tente novamente.")
    data["age_seconds"] = round(age, 2)
    data["fresh"] = age < 3.0
    if age >= 3.0:
        data["state"] = "SEM SINAL"
    return data


@app.get("/health")
async def health_check():
    """Health check para monitoramento."""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


# ===========================================================================
# Endpoints de Diagnóstico por IA
# ===========================================================================

@app.get("/diagnostics", response_model=DiagnosticListResponse)
async def list_diagnostics(
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """Histórico de laudos da IA."""
    rows = db.execute(
        select(Diagnostic).order_by(Diagnostic.timestamp.desc()).limit(limit)
    ).scalars().all()
    total = db.scalar(select(func.count()).select_from(Diagnostic))
    return DiagnosticListResponse(
        diagnostics=[DiagnosticResponse.model_validate(d) for d in rows],
        total=total or 0,
    )


@app.get("/diagnostics/latest", response_model=DiagnosticResponse)
async def latest_diagnosis(
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """Último laudo da IA."""
    row = db.execute(
        select(Diagnostic).order_by(Diagnostic.timestamp.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Nenhum diagnóstico registrado ainda")
    return DiagnosticResponse.model_validate(row)


@app.get("/diagnostics/stats")
async def diagnostic_stats(
    user: "User" = Depends(get_current_user),
):
    """Contador de erros na janela deslizante + status do motor de IA."""
    return {
        "errors_in_window": diagnostic_engine.error_tracker.count(),
        "window_seconds": config.ERROR_WINDOW_SECONDS,
        "burst_limit": config.ERROR_BURST_LIMIT,
        "ai_provider": (config.AI_PROVIDER or "local"),
        "ai_enabled": config.AI_ENABLED,
        "cooldown_seconds": config.AI_COOLDOWN_SECONDS,
    }


@app.post("/diagnostics/analyze")
async def force_analysis(
    status_snapshot: str = "CRITICO",
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """Força análise diagnóstica imediata."""
    result = _run_diagnostic_now(status_snapshot)
    return result


# ===========================================================================
# Endpoints de IA treinada (scikit-learn — open source)
# ===========================================================================

@app.get("/ml/status")
async def ml_status(
    user: "User" = Depends(get_current_user),
):
    """Metadados do modelo treinado (treinado? quando? acurácia? janelas?)."""
    from ml_model import ml_engine
    return ml_engine.status()


@app.post("/ml/train")
async def ml_train(
    synthetic_bootstrap: bool = Query(True, description="Completa dados insuficientes com dados sintéticos calibrados"),
    limit: int = Query(20000, ge=100, le=1000000),
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """
    Treina a IA open-source com o histórico do SQLite.
    IsolationForest (não-supervisionado) + RandomForest (rótulos NORMAL/ALERTA/CRITICO).
    Pode levar alguns segundos.
    """
    from ml_model import ml_engine
    from sqlalchemy import select as _select
    import asyncio as _asyncio

    rows = db.execute(
        _select(Event).order_by(Event.timestamp.asc()).limit(limit)
    ).scalars().all()
    events = [
        {"timestamp": e.timestamp.isoformat(), "status": e.status, "value": e.value}
        for e in rows
    ]
    logger.info(f"Treino ML solicitado por {user.username}: {len(events)} eventos reais")
    # Roda treino síncrono fora do event loop (pode levar alguns segundos)
    metrics = await _asyncio.to_thread(ml_engine.train, events, synthetic_bootstrap)
    if not metrics.get("ok"):
        raise HTTPException(400, f"Falha no treino: {metrics.get('reason')}")
    return metrics


# ===========================================================================
# Aprendizado do PADRÃO da máquina (não supervisionado — sem rótulos)
# ===========================================================================

@app.get("/ml/pattern/status")
async def ml_pattern_status(
    user: "User" = Depends(get_current_user),
):
    """
    Metadados do padrão de movimento aprendido (regimes, limites, janelas).
    Nenhum rótulo/status é usado no aprendizado.
    """
    from pattern_learner import pattern_learner
    return pattern_learner.status()


@app.post("/ml/pattern/train")
async def ml_pattern_train(
    limit: int = Query(50000, ge=100, le=1000000),
    db: Session = Depends(get_db),
    user: "User" = Depends(get_current_user),
):
    """
    Aprende o padrão da máquina apenas observando o histórico de movimento.

    Importante: a coluna `status` (NORMAL/ALERTA/CRITICO) é IGNORADA.
    O algoritmo descobre sozinho o comportamento padrão (k-NN + recorte
    robusto), os regimes normais (KMeans) e os limites de anomalia
    (percentis do próprio normal).
    """
    from pattern_learner import pattern_learner
    from sqlalchemy import select as _select
    import asyncio as _asyncio

    rows = db.execute(
        _select(Event).order_by(Event.timestamp.asc()).limit(limit)
    ).scalars().all()

    values, timestamps = [], []
    for e in rows:
        try:
            value = float(e.value)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= value < 100.0):  # ignora eventos manuais (999) e inválidos
            continue
        if (e.source or "").lower() == "manual":
            continue
        values.append(value)
        timestamps.append(e.timestamp)

    if len(values) < 100:
        raise HTTPException(400, f"Observações insuficientes: {len(values)}")

    logger.info(
        "Aprendizado do padrão solicitado por %s: %d observações (sem rótulos)",
        user.username, len(values),
    )
    report = await _asyncio.to_thread(pattern_learner.learn, values, timestamps)
    if not report.get("ok"):
        raise HTTPException(400, f"Falha no aprendizado: {report.get('reason')}")
    return report


@app.post("/ml/pattern/reset")
async def ml_pattern_reset(
    user: "User" = Depends(get_current_user),
):
    """Apaga o padrão aprendido (use ao trocar de máquina/peça)."""
    from pattern_learner import pattern_learner
    logger.info("Reset do padrão solicitado por %s", user.username)
    return pattern_learner.reset()


# ===========================================================================
# Endpoints de Autenticação (site logável + APK)
# ===========================================================================

@app.post("/auth/register", response_model=UserResponse, status_code=201)
async def register_user(payload: UserRegister, db: Session = Depends(get_db)):
    """Cadastro de novo usuário."""
    # Verifica se já existe
    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Username já existe")
    
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=auth_service.hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    try:
        db.refresh(user)
    except Exception as e:
        logger.warning(f"refresh do usuário falhou (ignorado): {e}")
    return user


@app.post("/auth/login", response_model=TokenResponse)
async def login_user(payload: UserLogin, db: Session = Depends(get_db)):
    """Login JWT."""
    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if user is None or not auth_service.verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Username ou senha incorretos")
    if not user.is_active:
        raise HTTPException(403, "Usuário inativo")
    
    token = auth_service.create_token(user)
    return TokenResponse(access_token=token, user=UserResponse.model_validate(user))


@app.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    """Retorna dados do usuário autenticado."""
    return user


# Exemplo de rota protegida (pronta para uso no site/APK)
@app.get("/api/machines")
async def list_machines(user: User = Depends(get_current_user)):
    """Lista máquinas monitoradas (requer autenticação)."""
    return {
        "machines": [
            {"id": "maquina-01", "name": "Torno CNC", "status": "NORMAL"},
            {"id": "maquina-02", "name": "Fresa Industrial", "status": "ALERTA"},
        ],
        "user": user.username,
    }


# ===========================================================================
# Frontend estático (HMI dark) — servido pela própria API
# Rotas da API têm precedência; o frontend fica na raiz "/".
# ===========================================================================
from fastapi.staticfiles import StaticFiles

_FRONTEND_DIR = Path(__file__).parent / "frontend"
_CAPTURES_DIR = Path(__file__).parent / "captures"

if _CAPTURES_DIR.is_dir():
    app.mount("/captures", StaticFiles(directory=str(_CAPTURES_DIR)), name="captures")
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


# ===========================================================================
# Rodar com: uvicorn api:app --host 0.0.0.0 --port 8000 --reload
# ===========================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT)