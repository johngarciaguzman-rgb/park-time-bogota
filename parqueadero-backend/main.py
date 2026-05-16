import base64
import csv
import hashlib
import json
import os
import secrets
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Annotated, Generator, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
import httpx
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Bogota"))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))
SECRET_KEY = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
OIDC_ISSUER = os.getenv("OIDC_ISSUER", os.getenv("LMS_AUTH_URL", "https://auth-meli.adminml.com")).rstrip("/")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "").strip()
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "").strip()
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid profile email").strip()
OIDC_STATE_COOKIE = "parking_oidc_state"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
LOCAL_LOGIN_ENABLED = os.getenv("LOCAL_LOGIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
OIDC_ADMIN_USERS = {
    value.strip().lower()
    for value in os.getenv("OIDC_ADMIN_USERS", "").split(",")
    if value.strip()
}
OIDC_ADMIN_GROUPS = {
    value.strip().lower()
    for value in os.getenv("OIDC_ADMIN_GROUPS", "").split(",")
    if value.strip()
}
APP_ROLE_USERNAMES_DEFAULT = {
    "Conductor": "conductor@park.local",
    "Coordinador MLP": "coordinador.mlp@park.local",
    "Operación MELI": "operacion.meli@park.local",
    "Operador Estacionamiento": "operador.estacionamiento@park.local",
    "Monitor MLP": "monitor.mlp@park.local",
    "Torre de Control": "torre.control@park.local",
}
APP_ROLE_ADMIN_NAMES = {"Operación MELI", "Torre de Control"}
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1GG6twSUKAn8LK_t4Q4WK3rMfJsdxRej2FD5pDI9wReU").strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "CONTROL ARRIBOS").strip()
GOOGLE_FLASH_SHEET_NAME = os.getenv("GOOGLE_FLASH_SHEET_NAME", "Flash_Parking").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()
GOOGLE_PUBLIC_CSV_URL = os.getenv("GOOGLE_PUBLIC_CSV_URL", "").strip()
GOOGLE_FLASH_LOG_REQUIRED = os.getenv("GOOGLE_FLASH_LOG_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}
ALLOWED_GOOGLE_HOSTS = {"docs.google.com", "sheets.googleapis.com"}

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./parqueadero.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(120), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="operador")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=lambda: utc_now())


class ParkingVisit(Base):
    __tablename__ = "parking_visits"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ficha_code = Column(String(80), index=True, nullable=False)
    placa = Column(String(16), index=True, nullable=False)
    cedula = Column(String(30), index=True, nullable=True)
    ruta = Column(String(120), index=True, nullable=False)
    ubicacion = Column(String(120), index=True, nullable=False)
    dock = Column(String(80), index=True, nullable=False)
    arrival_slot_id = Column(String(36), index=True, nullable=True)
    mlp = Column(String(180), index=True, nullable=True)
    spr = Column(String(40), nullable=True)
    ola_wtd = Column(String(40), index=True, nullable=True)
    conductor = Column(String(120), nullable=True)
    observaciones = Column(Text, nullable=True)
    salida_observaciones = Column(Text, nullable=True)
    ingreso_at = Column(DateTime, index=True, nullable=False)
    salida_at = Column(DateTime, index=True, nullable=True)
    duracion_min = Column(Integer, nullable=True)
    operador_ingreso = Column(String(120), nullable=False)
    operador_salida = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: utc_now())
    updated_at = Column(DateTime, nullable=False, default=lambda: utc_now())


class ArrivalSlot(Base):
    __tablename__ = "arrival_slots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    sheet_id = Column(String(140), index=True, nullable=False)
    sheet_name = Column(String(140), index=True, nullable=False)
    source_row = Column(Integer, index=True, nullable=False)
    source_key = Column(String(220), index=True, nullable=False)
    est_wtd = Column(String(40), index=True, nullable=False)
    ruta_sorting = Column(String(80), index=True, nullable=False)
    mlp = Column(String(180), index=True, nullable=False)
    zona = Column(String(220), index=True, nullable=False)
    spr = Column(String(40), nullable=True)
    ola_wtd = Column(String(40), index=True, nullable=True)
    disponible = Column(String(80), nullable=True)
    placa = Column(String(16), index=True, nullable=True)
    auxiliar = Column(String(120), nullable=True)
    hora_limite = Column(String(40), nullable=True)
    tmec = Column(String(40), nullable=True)
    hora_citacion = Column(String(40), nullable=True)
    distancia_km = Column(String(40), nullable=True)
    paradas = Column(String(40), nullable=True)
    whatsapp_url = Column(String(300), nullable=True)
    arl_url = Column(String(300), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    assigned_visit_id = Column(String(36), index=True, nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    last_sync_at = Column(DateTime, nullable=False, default=lambda: utc_now())
    created_at = Column(DateTime, nullable=False, default=lambda: utc_now())
    updated_at = Column(DateTime, nullable=False, default=lambda: utc_now())


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return as_utc(dt).astimezone(APP_TZ)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=APP_TZ)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def local_range_bounds(from_day: date, to_day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(from_day, time.min, tzinfo=APP_TZ)
    end_local = datetime.combine(to_day + timedelta(days=1), time.min, tzinfo=APP_TZ)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def parse_date_str(value: Optional[str], default: Optional[date] = None) -> date:
    if not value:
        if default is None:
            raise HTTPException(status_code=400, detail="Fecha requerida")
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido") from exc


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def configured_role_usernames() -> dict[str, str]:
    mapping = dict(APP_ROLE_USERNAMES_DEFAULT)
    raw = os.getenv("APP_ROLE_USERNAMES_JSON", "").strip()
    if not raw:
        return mapping
    try:
        parsed = json.loads(raw)
    except ValueError:
        return mapping
    if not isinstance(parsed, dict):
        return mapping
    for role_name, username in parsed.items():
        if role_name in APP_ROLE_USERNAMES_DEFAULT and isinstance(username, str):
            candidate = username.strip()
            if 3 <= len(candidate) <= 120 and all(char.isalnum() or char in "@._-" for char in candidate):
                mapping[role_name] = candidate
    return mapping


def configured_role_passwords() -> dict[str, str]:
    raw = os.getenv("APP_ROLE_PASSWORDS_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    passwords: dict[str, str] = {}
    for role_name, password in parsed.items():
        if role_name in APP_ROLE_USERNAMES_DEFAULT and isinstance(password, str) and password:
            passwords[role_name] = password[:128]
    return passwords


def normalize_placa(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def oidc_is_configured() -> bool:
    return bool(OIDC_CLIENT_ID)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def oidc_cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or PUBLIC_BASE_URL.startswith("https://")


def public_url_for(request: Request, route_name: str) -> str:
    internal_url = str(request.url_for(route_name))
    if not PUBLIC_BASE_URL:
        return internal_url
    parsed = urllib.parse.urlparse(internal_url)
    return f"{PUBLIC_BASE_URL}{parsed.path}"


async def get_oidc_metadata() -> dict:
    metadata_url = f"{OIDC_ISSUER}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(metadata_url)
            response.raise_for_status()
            metadata = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="No se pudo consultar la configuración LMS/Okta") from exc

    issuer = metadata.get("issuer")
    if issuer != OIDC_ISSUER:
        raise HTTPException(status_code=503, detail="Issuer LMS/Okta inválido")
    return metadata


def state_payload_from_cookie(request: Request, state: str) -> dict:
    token = request.cookies.get(OIDC_STATE_COOKIE)
    if not token:
        raise HTTPException(status_code=400, detail="Sesión SSO expirada")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Estado SSO inválido") from exc
    if payload.get("state") != state:
        raise HTTPException(status_code=400, detail="Estado SSO no coincide")
    verifier = payload.get("code_verifier")
    if not isinstance(verifier, str) or len(verifier) < 43:
        raise HTTPException(status_code=400, detail="Verificador SSO inválido")
    return payload


def role_from_oidc_userinfo(username: str, userinfo: dict) -> str:
    username_key = username.strip().lower()
    groups_value = userinfo.get("groups")
    groups = groups_value if isinstance(groups_value, list) else []
    normalized_groups = {str(group).strip().lower() for group in groups}
    if username_key in OIDC_ADMIN_USERS or normalized_groups.intersection(OIDC_ADMIN_GROUPS):
        return "admin"
    return "operador"


def get_or_create_oidc_user(db: Session, username: str, role: str) -> User:
    username = username.strip()[:120]
    if not username:
        raise HTTPException(status_code=401, detail="El LMS no retornó usuario válido")
    user = db.scalar(select(User).where(User.username == username))
    if user:
        if not user.active:
            raise HTTPException(status_code=403, detail="Usuario inactivo")
        if user.role != role and role == "admin":
            user.role = "admin"
            db.commit()
            db.refresh(user)
        return user
    user = User(
        username=username,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=role,
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not isinstance(username, str) or not username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado") from exc

    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso denegado")
    return user


FichaCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._:/,-]+$")]
Placa = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=16, pattern=r"^[A-Za-z0-9-]+$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
OptionalText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
DeviceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$")]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120, pattern=r"^[A-Za-z0-9@._-]+$")]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
LoginPassword = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Role = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(admin|operador)$")]


class LoginRequest(BaseModel):
    username: Username
    password: LoginPassword


class RoleLoginRequest(BaseModel):
    role: ShortText
    clave: LoginPassword


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class UserCreateRequest(BaseModel):
    username: Username
    password: Password
    role: Role = "operador"


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    active: bool


class FichaRequest(BaseModel):
    ficha_code: FichaCode


class IngresoRequest(BaseModel):
    ficha_code: FichaCode = Field(..., description="Código escaneado de la ficha física")
    placa: Placa
    ruta: ShortText
    ubicacion: ShortText
    dock: ShortText
    arrival_slot_id: Optional[str] = Field(default=None, max_length=36)
    device_id: Optional[DeviceId] = None
    lector_qr: Optional[FichaCode] = None
    cedula: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=30, pattern=r"^[A-Za-z0-9._-]*$")]] = None
    conductor: Optional[OptionalText] = None
    observaciones: Optional[OptionalText] = None

    @field_validator("placa")
    @classmethod
    def placa_upper(cls, value: str) -> str:
        return normalize_placa(value)


class SalidaRequest(BaseModel):
    ficha_code: FichaCode
    device_id: Optional[DeviceId] = None
    lector_qr: Optional[FichaCode] = None
    observaciones: Optional[OptionalText] = None


class VisitResponse(BaseModel):
    id: str
    ficha_code: str
    placa: str
    ruta: str
    ubicacion: str
    dock: str
    arrival_slot_id: Optional[str]
    mlp: Optional[str]
    spr: Optional[str]
    ola_wtd: Optional[str]
    cedula: Optional[str]
    conductor: Optional[str]
    observaciones: Optional[str]
    salida_observaciones: Optional[str]
    ingreso_at: str
    ingreso_fecha: str
    ingreso_hora: str
    salida_at: Optional[str]
    salida_fecha: Optional[str]
    salida_hora: Optional[str]
    duracion_min: Optional[int]
    duracion_formato: str
    estado: str
    operador_ingreso: str
    operador_salida: Optional[str]


class ArrivalSlotResponse(BaseModel):
    id: str
    est_wtd: str
    ruta_sorting: str
    mlp: str
    zona: str
    spr: Optional[str]
    ola_wtd: Optional[str]
    disponible: Optional[str]
    placa: Optional[str]
    auxiliar: Optional[str]
    hora_limite: Optional[str]
    tmec: Optional[str]
    hora_citacion: Optional[str]
    distancia_km: Optional[str]
    paradas: Optional[str]
    whatsapp_url: Optional[str]
    arl_url: Optional[str]
    pending: bool
    assigned_visit_id: Optional[str]


class ArrivalValidateResponse(BaseModel):
    status: str
    message: str
    matches: list[ArrivalSlotResponse]


class SheetSyncResponse(BaseModel):
    status: str
    message: str
    imported: int = 0
    updated: int = 0
    deactivated: int = 0
    source: str


class FichaStatusResponse(BaseModel):
    ficha_code: str
    estado: str
    visit: Optional[VisitResponse]


class ArrivalSlotUpdateRequest(BaseModel):
    placa: Optional[Placa] = None
    auxiliar: Optional[OptionalText] = None
    hora_limite: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]] = None
    tmec: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)]] = None

    @field_validator("placa")
    @classmethod
    def placa_upper(cls, value: Optional[str]) -> Optional[str]:
        return normalize_placa(value) if value else value


class PlateAlertResponse(BaseModel):
    placa: str
    target_min: int
    late_count_15d: int
    should_agilizar: bool


def format_duration(minutes: Optional[int]) -> str:
    if minutes is None:
        return "En patio"
    hours = minutes // 60
    mins = minutes % 60
    if hours:
        return f"{hours}h {mins}min"
    return f"{mins}min"


def normalize_header(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("_", " ")
    )


def normalize_code(value: str) -> str:
    return value.strip().upper().replace(" ", "")


def scan_code_candidates(value: str) -> set[str]:
    normalized = normalize_code(value)
    candidates = {normalized} if normalized else set()
    if "." in normalized:
        candidates.add(normalized.split(".", 1)[0])
    if "," in normalized:
        candidates.add(normalized.split(",", 1)[0])
    return {candidate for candidate in candidates if candidate}


def validate_google_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Solo se permiten URLs HTTPS de Google Sheets")
    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_GOOGLE_HOSTS:
        raise HTTPException(status_code=400, detail="Dominio no permitido para sincronización")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="La URL no puede contener credenciales")
    return url


def public_csv_url() -> str:
    if GOOGLE_PUBLIC_CSV_URL:
        return validate_google_url(GOOGLE_PUBLIC_CSV_URL)
    encoded_sheet = urllib.parse.quote(GOOGLE_SHEET_NAME)
    return (
        f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={encoded_sheet}"
    )


def service_account_info() -> Optional[dict]:
    raw = GOOGLE_SERVICE_ACCOUNT_JSON
    if GOOGLE_SERVICE_ACCOUNT_JSON_B64:
        try:
            raw = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=500, detail="Credenciales Google inválidas") from exc
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Credenciales Google inválidas") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise HTTPException(status_code=500, detail="Credenciales Google inválidas")
    return info


async def fetch_sheet_rows() -> tuple[list[list[str]], str]:
    info = service_account_info()
    if info:
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GoogleAuthRequest
        except ImportError as exc:
            raise HTTPException(status_code=500, detail="Falta instalar google-auth") from exc

        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        credentials.refresh(GoogleAuthRequest())
        quoted_range = urllib.parse.quote(f"{GOOGLE_SHEET_NAME}!A:Z", safe="")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/{quoted_range}"
        validate_google_url(url)
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {credentials.token}"})
        if response.status_code == 403:
            raise HTTPException(status_code=403, detail="La service account no tiene acceso al Google Sheet")
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="No se pudo leer Google Sheets")
        data = response.json()
        values = data.get("values", [])
        if not isinstance(values, list):
            raise HTTPException(status_code=502, detail="Respuesta inválida de Google Sheets")
        return [[str(cell) for cell in row] for row in values], "service_account"

    url = public_csv_url()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.get(url)
    if response.status_code in {301, 302, 303, 307, 308}:
        raise HTTPException(
            status_code=403,
            detail="La hoja no está pública. Configura una service account o publica/exporta la hoja como CSV.",
        )
    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=403,
            detail="La hoja no está pública. Comparte el Google Sheet con una service account o publica la hoja como CSV.",
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="No se pudo leer CSV de Google Sheets")
    if "text/html" in (response.headers.get("content-type") or "").lower():
        raise HTTPException(
            status_code=403,
            detail="Google devolvió una página HTML, no un CSV. La hoja parece privada o requiere login.",
        )
    reader = csv.reader(StringIO(response.text))
    return [[cell for cell in row] for row in reader], "public_csv"


def movement_cycle(recorded_at: datetime) -> str:
    local_dt = to_local(recorded_at) or datetime.now(APP_TZ)
    local_time = local_dt.time()
    if local_time < time(11, 20):
        return "AM"
    if local_time < time(15, 35):
        return "AMT"
    return "PM"


def flash_assigned_dock(visit: ParkingVisit, lector_qr: Optional[str]) -> str:
    raw = (lector_qr or "").strip().upper()
    if raw:
        compact = raw.replace(" ", "")
        before_dot = compact.split(".", 1)[0]
        if "," in before_dot and len(before_dot) <= 40:
            return before_dot
    return visit.dock


def append_flash_parking_log(
    visit: ParkingVisit,
    movement_type: str,
    *,
    recorded_at: datetime,
    device_id: Optional[str],
    lector_qr: Optional[str],
) -> bool:
    """Best-effort append to the Flash_Parking Google Sheet.

    The DB remains the source of truth for the app. The Sheet append is optional
    by default so local tests and deployments without a writer service account
    continue working.
    """
    try:
        info = service_account_info()
        if not info:
            return False

        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as GoogleAuthRequest
        except ImportError as exc:
            raise HTTPException(status_code=500, detail="Falta instalar google-auth") from exc

        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        credentials.refresh(GoogleAuthRequest())

        local_dt = to_local(recorded_at) or datetime.now(APP_TZ)
        values = [
            [
                visit.id[:8],
                local_dt.strftime("%d/%m/%Y"),
                movement_cycle(recorded_at),
                (device_id or "").strip() or "web",
                (lector_qr or visit.ficha_code).strip(),
                flash_assigned_dock(visit, lector_qr),
                movement_type,
                local_dt.strftime("%H:%M:%S"),
            ]
        ]
        quoted_range = urllib.parse.quote(f"{GOOGLE_FLASH_SHEET_NAME}!A:H", safe="")
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEET_ID}/values/"
            f"{quoted_range}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        )
        validate_google_url(url)
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {credentials.token}"},
                json={"values": values},
            )
        if response.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="La service account no tiene permiso de edición sobre Flash_Parking",
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="No se pudo escribir en Flash_Parking")
        return True
    except HTTPException:
        if GOOGLE_FLASH_LOG_REQUIRED:
            raise
        return False
    except Exception as exc:
        if GOOGLE_FLASH_LOG_REQUIRED:
            raise HTTPException(status_code=502, detail="No se pudo escribir en Flash_Parking") from exc
        return False


def row_value(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value.strip()
    return ""


def parse_arrival_rows(rows: list[list[str]]) -> list[dict[str, str | int]]:
    required = {"est wtd", "ruta sorting", "mlp", "zona"}
    header_index: Optional[int] = None
    normalized_headers: list[str] = []
    for index, raw_row in enumerate(rows[:30]):
        headers = [normalize_header(str(cell)) for cell in raw_row]
        if required.issubset(set(headers)):
            header_index = index
            normalized_headers = headers
            break
    if header_index is None:
        raise HTTPException(status_code=422, detail="No se encontraron columnas esperadas en CONTROL ARRIBOS")

    parsed: list[dict[str, str | int]] = []
    for offset, raw_row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        row_map: dict[str, str] = {}
        for idx, header in enumerate(normalized_headers):
            if header:
                row_map[header] = str(raw_row[idx]).strip() if idx < len(raw_row) else ""
        est_wtd = row_value(row_map, "est wtd")
        ruta_sorting = row_value(row_map, "ruta sorting")
        mlp = row_value(row_map, "mlp")
        zona = row_value(row_map, "zona")
        if not any([est_wtd, ruta_sorting, mlp, zona]):
            continue
        if not est_wtd or not ruta_sorting or not mlp or not zona:
            continue
        parsed.append(
            {
                "source_row": offset,
                "est_wtd": est_wtd[:40],
                "ruta_sorting": ruta_sorting[:80],
                "mlp": mlp[:180],
                "zona": zona[:220],
                "spr": row_value(row_map, "spr")[:40] or None,
                "ola_wtd": row_value(row_map, "ola wtd", "ola")[:40] or None,
                "disponible": row_value(row_map, "disponible")[:80] or None,
                "placa": row_value(row_map, "placa", "plate", "patente")[:16] or None,
                "auxiliar": row_value(row_map, "auxiliar", "driver helper")[:120] or None,
                "hora_limite": row_value(row_map, "hora limite", "hora límite", "time hs", "time")[:40] or None,
                "tmec": row_value(row_map, "tmec", "tme c", "tiempo maximo de cargue", "tiempo maximo cargue")[:40] or None,
                "hora_citacion": row_value(row_map, "hora citacion", "hora de citacion", "hora citación", "hora de citación")[:40] or None,
                "distancia_km": row_value(row_map, "distancia km", "km", "distancia")[:40] or None,
                "paradas": row_value(row_map, "paradas", "stops")[:40] or None,
                "whatsapp_url": row_value(row_map, "whatsapp", "link whatsapp", "grupo whatsapp")[:300] or None,
                "arl_url": row_value(row_map, "arl", "qr arl", "link arl")[:300] or None,
            }
        )
    return parsed


def slot_to_response(slot: ArrivalSlot) -> dict:
    return {
        "id": slot.id,
        "est_wtd": slot.est_wtd,
        "ruta_sorting": slot.ruta_sorting,
        "mlp": slot.mlp,
        "zona": slot.zona,
        "spr": slot.spr,
        "ola_wtd": slot.ola_wtd,
        "disponible": slot.disponible,
        "placa": slot.placa,
        "auxiliar": slot.auxiliar,
        "hora_limite": slot.hora_limite,
        "tmec": slot.tmec,
        "hora_citacion": slot.hora_citacion,
        "distancia_km": slot.distancia_km,
        "paradas": slot.paradas,
        "whatsapp_url": slot.whatsapp_url,
        "arl_url": slot.arl_url,
        "pending": slot.active and not slot.assigned_visit_id,
        "assigned_visit_id": slot.assigned_visit_id,
    }


def current_arrival_day() -> date:
    return datetime.now(APP_TZ).date()


def arrival_source_prefix(day: Optional[date] = None) -> str:
    sync_day = day or current_arrival_day()
    return f"{GOOGLE_SHEET_ID}|{GOOGLE_SHEET_NAME}|{sync_day.isoformat()}|"


def arrival_source_key(source_row: int, day: Optional[date] = None) -> str:
    return f"{arrival_source_prefix(day)}{source_row}"


def visit_to_response(visit: ParkingVisit) -> dict:
    ingreso_local = to_local(visit.ingreso_at)
    salida_local = to_local(visit.salida_at)
    if ingreso_local is None:
        raise HTTPException(status_code=500, detail="Error interno")
    return {
        "id": visit.id,
        "ficha_code": visit.ficha_code,
        "placa": visit.placa,
        "ruta": visit.ruta,
        "cedula": visit.cedula,
        "ubicacion": visit.ubicacion,
        "dock": visit.dock,
        "arrival_slot_id": visit.arrival_slot_id,
        "mlp": visit.mlp,
        "spr": visit.spr,
        "ola_wtd": visit.ola_wtd,
        "conductor": visit.conductor,
        "observaciones": visit.observaciones,
        "salida_observaciones": visit.salida_observaciones,
        "ingreso_at": ingreso_local.isoformat(timespec="minutes"),
        "ingreso_fecha": ingreso_local.date().isoformat(),
        "ingreso_hora": ingreso_local.strftime("%H:%M"),
        "salida_at": salida_local.isoformat(timespec="minutes") if salida_local else None,
        "salida_fecha": salida_local.date().isoformat() if salida_local else None,
        "salida_hora": salida_local.strftime("%H:%M") if salida_local else None,
        "duracion_min": visit.duracion_min,
        "duracion_formato": format_duration(visit.duracion_min),
        "estado": "Completado" if visit.salida_at else "En patio",
        "operador_ingreso": visit.operador_ingreso,
        "operador_salida": visit.operador_salida,
    }


def active_by_ficha(db: Session, ficha_code: str) -> Optional[ParkingVisit]:
    return db.scalar(
        select(ParkingVisit).where(
            ParkingVisit.ficha_code == ficha_code,
            ParkingVisit.salida_at.is_(None),
        )
    )


def pending_arrival_slots(db: Session, day: Optional[date] = None) -> list[ArrivalSlot]:
    prefix = arrival_source_prefix(day)
    return db.scalars(
        select(ArrivalSlot)
        .where(
            ArrivalSlot.active.is_(True),
            ArrivalSlot.assigned_visit_id.is_(None),
            ArrivalSlot.source_key.like(f"{prefix}%"),
        )
        .order_by(ArrivalSlot.mlp.asc(), ArrivalSlot.ruta_sorting.asc(), ArrivalSlot.est_wtd.asc())
    ).all()


def find_pending_arrival_slots(db: Session, code: str, day: Optional[date] = None) -> list[ArrivalSlot]:
    candidates = scan_code_candidates(code)
    if not candidates:
        return []
    matches = []
    for slot in pending_arrival_slots(db, day):
        slot_codes = {
            normalize_code(slot.est_wtd),
            normalize_code(slot.ruta_sorting),
        }
        if slot.ola_wtd:
            slot_codes.add(normalize_code(f"{slot.est_wtd},{slot.ola_wtd}"))
            slot_codes.add(normalize_code(f"{slot.est_wtd}.{slot.ola_wtd}"))
        if candidates.intersection(slot_codes):
            matches.append(slot)
    return matches


def arrival_slot_for_ingreso(db: Session, body: IngresoRequest) -> Optional[ArrivalSlot]:
    if body.arrival_slot_id:
        slot = db.scalar(
            select(ArrivalSlot).where(
                ArrivalSlot.id == body.arrival_slot_id,
                ArrivalSlot.active.is_(True),
            )
        )
        if not slot:
            raise HTTPException(status_code=404, detail="La ficha de CONTROL ARRIBOS no existe o no está activa")
        if slot.assigned_visit_id:
            raise HTTPException(status_code=409, detail="La ficha ya fue asignada desde CONTROL ARRIBOS")
        return slot

    matches = find_pending_arrival_slots(db, body.ficha_code)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="La ficha existe varias veces en CONTROL ARRIBOS; selecciona la ruta correcta antes de guardar",
        )
    return None


def ensure_schema_columns() -> None:
    """Pequeña migración segura para desarrollo local cuando agregamos campos nuevos."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    with engine.begin() as connection:
        if "parking_visits" in existing_tables:
            columns = {column["name"] for column in inspector.get_columns("parking_visits")}
            visit_migrations = {
                "cedula": "ALTER TABLE parking_visits ADD COLUMN cedula VARCHAR(30)",
                "arrival_slot_id": "ALTER TABLE parking_visits ADD COLUMN arrival_slot_id VARCHAR(36)",
                "mlp": "ALTER TABLE parking_visits ADD COLUMN mlp VARCHAR(180)",
                "spr": "ALTER TABLE parking_visits ADD COLUMN spr VARCHAR(40)",
                "ola_wtd": "ALTER TABLE parking_visits ADD COLUMN ola_wtd VARCHAR(40)",
            }
            for column_name, ddl in visit_migrations.items():
                if column_name not in columns:
                    connection.execute(text(ddl))
        if "arrival_slots" in existing_tables:
            columns = {column["name"] for column in inspector.get_columns("arrival_slots")}
            slot_migrations = {
                "disponible": "ALTER TABLE arrival_slots ADD COLUMN disponible VARCHAR(80)",
                "placa": "ALTER TABLE arrival_slots ADD COLUMN placa VARCHAR(16)",
                "auxiliar": "ALTER TABLE arrival_slots ADD COLUMN auxiliar VARCHAR(120)",
                "hora_limite": "ALTER TABLE arrival_slots ADD COLUMN hora_limite VARCHAR(40)",
                "tmec": "ALTER TABLE arrival_slots ADD COLUMN tmec VARCHAR(40)",
                "hora_citacion": "ALTER TABLE arrival_slots ADD COLUMN hora_citacion VARCHAR(40)",
                "distancia_km": "ALTER TABLE arrival_slots ADD COLUMN distancia_km VARCHAR(40)",
                "paradas": "ALTER TABLE arrival_slots ADD COLUMN paradas VARCHAR(40)",
                "whatsapp_url": "ALTER TABLE arrival_slots ADD COLUMN whatsapp_url VARCHAR(300)",
                "arl_url": "ALTER TABLE arrival_slots ADD COLUMN arl_url VARCHAR(300)",
            }
            for column_name, ddl in slot_migrations.items():
                if column_name not in columns:
                    connection.execute(text(ddl))


def seed_admin_user() -> None:
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        return

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing:
            return
        db.add(User(username=username, password_hash=hash_password(password), role="admin", active=True))
        db.commit()


def seed_role_users() -> None:
    """Create role users only when passwords are provided through environment variables."""
    default_password = os.getenv("APP_ROLE_DEFAULT_PASSWORD", "")
    per_role_passwords = configured_role_passwords()
    if not default_password and not per_role_passwords:
        return

    with SessionLocal() as db:
        for app_role, username in configured_role_usernames().items():
            password = per_role_passwords.get(app_role) or default_password
            if not password:
                continue
            existing = db.scalar(select(User).where(User.username == username))
            if existing:
                continue
            system_role = "admin" if app_role in APP_ROLE_ADMIN_NAMES else "operador"
            db.add(User(username=username, password_hash=hash_password(password), role=system_role, active=True))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns()
    seed_admin_user()
    seed_role_users()
    yield


app = FastAPI(title="Sistema de Parqueadero por Fichas", version="2.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})


@app.post("/api/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    if not LOCAL_LOGIN_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Login local deshabilitado")
    user = db.scalar(select(User).where(User.username == body.username, User.active.is_(True)))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario o contraseña inválidos")
    token = create_access_token(user.username, user.role)
    return TokenResponse(access_token=token, username=user.username, role=user.role)


@app.post("/api/auth/role-login", response_model=TokenResponse)
def role_login(body: RoleLoginRequest, db: Session = Depends(get_db)):
    role_name = body.role.strip()
    username = configured_role_usernames().get(role_name)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Rol o clave inválidos")
    user = db.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if not user or not verify_password(body.clave, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Rol o clave inválidos")
    token = create_access_token(user.username, user.role)
    return TokenResponse(access_token=token, username=user.username, role=user.role)


@app.get("/api/auth/settings")
def auth_settings(request: Request):
    callback_url = public_url_for(request, "oidc_callback")
    return {
        "provider": "MELI/Okta",
        "local_login_enabled": LOCAL_LOGIN_ENABLED,
        "oidc_enabled": oidc_is_configured(),
        "issuer": OIDC_ISSUER,
        "authorization_url": f"{OIDC_ISSUER}/oauth2/v1/authorize",
        "callback_url": callback_url,
        "missing": [] if oidc_is_configured() else ["OIDC_CLIENT_ID"],
    }


@app.get("/api/auth/oidc/login")
async def oidc_login(request: Request):
    if not oidc_is_configured():
        raise HTTPException(status_code=503, detail="Falta configurar OIDC_CLIENT_ID para login LMS/MELI")

    metadata = await get_oidc_metadata()
    authorization_endpoint = metadata.get("authorization_endpoint")
    if not isinstance(authorization_endpoint, str):
        raise HTTPException(status_code=503, detail="Endpoint de autorización LMS inválido")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    cookie_token = jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    redirect_uri = public_url_for(request, "oidc_callback")
    params = {
        "client_id": OIDC_CLIENT_ID,
        "response_type": "code",
        "scope": OIDC_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    query = urllib.parse.urlencode(params)
    response = RedirectResponse(f"{authorization_endpoint}?{query}")
    response.set_cookie(
        OIDC_STATE_COOKIE,
        cookie_token,
        max_age=600,
        httponly=True,
        secure=oidc_cookie_secure(request),
        samesite="lax",
    )
    return response


@app.get("/api/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    code: Optional[str] = Query(default=None, max_length=2048),
    state: Optional[str] = Query(default=None, max_length=256),
    error: Optional[str] = Query(default=None, max_length=256),
    db: Session = Depends(get_db),
):
    if error:
        return HTMLResponse("""<!doctype html><html lang='es'><head><meta charset='utf-8'><meta http-equiv='refresh' content='4;url=/'><title>Login LMS</title></head><body style='font-family:Arial;padding:32px'><h2>No se completó el login LMS</h2><p>Okta/MELI canceló o rechazó la autenticación.</p><p><a href='/'>Volver al inicio</a></p></body></html>""", status_code=401)
    if not code or not state:
        return HTMLResponse("""<!doctype html><html lang='es'><head><meta charset='utf-8'><meta http-equiv='refresh' content='4;url=/'><title>Login LMS</title></head><body style='font-family:Arial;padding:32px'><h2>Esta URL no se abre directamente</h2><p>Primero debes iniciar el flujo desde el botón <b>Iniciar con LMS/MELI</b>. Si estás en desarrollo, usa el login local.</p><p><a href='/'>Volver al inicio</a></p></body></html>""", status_code=400)
    payload = state_payload_from_cookie(request, state)
    metadata = await get_oidc_metadata()
    token_endpoint = metadata.get("token_endpoint")
    userinfo_endpoint = metadata.get("userinfo_endpoint")
    if not isinstance(token_endpoint, str) or not isinstance(userinfo_endpoint, str):
        raise HTTPException(status_code=503, detail="Endpoints LMS inválidos")

    token_body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": public_url_for(request, "oidc_callback"),
        "client_id": OIDC_CLIENT_ID,
        "code_verifier": payload["code_verifier"],
    }
    if OIDC_CLIENT_SECRET:
        token_body["client_secret"] = OIDC_CLIENT_SECRET

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            token_response = await client.post(token_endpoint, data=token_body)
            token_response.raise_for_status()
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if not isinstance(access_token, str):
                raise HTTPException(status_code=401, detail="El LMS no entregó access token")
            userinfo_response = await client.get(userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"})
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=401, detail="No se pudo completar el login LMS") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Error consultando LMS") from exc

    username = (
        userinfo.get("preferred_username")
        or userinfo.get("email")
        or userinfo.get("nickname")
        or userinfo.get("sub")
    )
    if not isinstance(username, str):
        raise HTTPException(status_code=401, detail="El LMS no retornó identificador de usuario")

    role = role_from_oidc_userinfo(username, userinfo)
    user = get_or_create_oidc_user(db, username, role)
    app_token = create_access_token(user.username, user.role)
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>Login LMS</title></head>
<body>
<script>
localStorage.setItem('parking_token', {json.dumps(app_token)});
localStorage.setItem('parking_user', JSON.stringify({json.dumps({'username': user.username, 'role': user.role})}));
window.location.replace('/');
</script>
<p>Login LMS correcto. Redirigiendo...</p>
</body></html>"""
    response = HTMLResponse(html)
    response.delete_cookie(OIDC_STATE_COOKIE)
    return response


@app.get("/api/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, username=user.username, role=user.role, active=user.active)


@app.post("/api/users", response_model=UserResponse, status_code=201)
def create_user(body: UserCreateRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    existing = db.scalar(select(User).where(User.username == body.username))
    if existing:
        raise HTTPException(status_code=409, detail="El usuario ya existe")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role, active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse(id=user.id, username=user.username, role=user.role, active=user.active)


@app.post("/api/fichas/status", response_model=FichaStatusResponse)
def ficha_status(body: FichaRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visit = active_by_ficha(db, body.ficha_code)
    return FichaStatusResponse(
        ficha_code=body.ficha_code,
        estado="ocupada" if visit else "libre",
        visit=visit_to_response(visit) if visit else None,
    )


@app.post("/api/sheets/sync", response_model=SheetSyncResponse)
async def sync_arrivals(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows, source = await fetch_sheet_rows()
    parsed_rows = parse_arrival_rows(rows)
    now = utc_now()
    sync_day = current_arrival_day()
    seen_keys: set[str] = set()
    imported = 0
    updated = 0

    for row in parsed_rows:
        source_key = arrival_source_key(int(row["source_row"]), sync_day)
        seen_keys.add(source_key)
        slot = db.scalar(select(ArrivalSlot).where(ArrivalSlot.source_key == source_key))
        if slot:
            updated += 1
        else:
            imported += 1
            slot = ArrivalSlot(
                id=str(uuid.uuid4()),
                sheet_id=GOOGLE_SHEET_ID,
                sheet_name=GOOGLE_SHEET_NAME,
                source_row=int(row["source_row"]),
                source_key=source_key,
                created_at=now,
            )
            db.add(slot)

        slot.est_wtd = str(row["est_wtd"])
        slot.ruta_sorting = str(row["ruta_sorting"])
        slot.mlp = str(row["mlp"])
        slot.zona = str(row["zona"])
        slot.spr = row["spr"] if isinstance(row["spr"], str) else None
        slot.ola_wtd = row["ola_wtd"] if isinstance(row["ola_wtd"], str) else None
        slot.disponible = row["disponible"] if isinstance(row["disponible"], str) else None
        slot.placa = normalize_placa(str(row["placa"])) if isinstance(row["placa"], str) and row["placa"] else None
        slot.auxiliar = row["auxiliar"] if isinstance(row["auxiliar"], str) else None
        slot.hora_limite = row["hora_limite"] if isinstance(row["hora_limite"], str) else None
        slot.tmec = row["tmec"] if isinstance(row["tmec"], str) else None
        slot.hora_citacion = row["hora_citacion"] if isinstance(row["hora_citacion"], str) else None
        slot.distancia_km = row["distancia_km"] if isinstance(row["distancia_km"], str) else None
        slot.paradas = row["paradas"] if isinstance(row["paradas"], str) else None
        slot.whatsapp_url = row["whatsapp_url"] if isinstance(row["whatsapp_url"], str) else None
        slot.arl_url = row["arl_url"] if isinstance(row["arl_url"], str) else None
        slot.sheet_id = GOOGLE_SHEET_ID
        slot.sheet_name = GOOGLE_SHEET_NAME
        slot.source_row = int(row["source_row"])
        slot.active = True
        slot.last_sync_at = now
        slot.updated_at = now

    deactivated = 0
    prefix = arrival_source_prefix(sync_day)
    active_slots = db.scalars(
        select(ArrivalSlot).where(
            ArrivalSlot.sheet_id == GOOGLE_SHEET_ID,
            ArrivalSlot.sheet_name == GOOGLE_SHEET_NAME,
            ArrivalSlot.active.is_(True),
            ArrivalSlot.source_key.like(f"{prefix}%"),
        )
    ).all()
    for slot in active_slots:
        if slot.source_key not in seen_keys and not slot.assigned_visit_id:
            slot.active = False
            slot.updated_at = now
            deactivated += 1

    db.commit()
    return SheetSyncResponse(
        status="ok",
        message=f"Sincronización completa: {len(parsed_rows)} filas leídas de {GOOGLE_SHEET_NAME}.",
        imported=imported,
        updated=updated,
        deactivated=deactivated,
        source=source,
    )


@app.get("/api/arrivals/pending", response_model=list[ArrivalSlotResponse])
def arrivals_pending(
    ola: Optional[str] = Query(default="1", max_length=40),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = pending_arrival_slots(db)
    if ola and ola.strip().lower() not in {"all", "todas", "todos"}:
        normalized_ola = normalize_code(ola)
        rows = [row for row in rows if normalize_code(row.ola_wtd or "") == normalized_ola]
    return [slot_to_response(row) for row in rows]


@app.get("/api/arrivals/validate", response_model=ArrivalValidateResponse)
def validate_arrival(
    code: str = Query(..., min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._:/, -]+$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    matches = find_pending_arrival_slots(db, code)
    if not matches:
        return ArrivalValidateResponse(
            status="not_found",
            message="No encontré esa ficha/ruta en los pendientes sincronizados de CONTROL ARRIBOS.",
            matches=[],
        )
    if len(matches) == 1:
        return ArrivalValidateResponse(
            status="unique",
            message="Ficha validada. Se asignará esta ruta y saldrá de pendientes al guardar.",
            matches=[slot_to_response(matches[0])],
        )
    return ArrivalValidateResponse(
        status="multiple",
        message="La ficha coincide con varias rutas; selecciona una para asignarla.",
        matches=[slot_to_response(row) for row in matches],
    )


@app.patch("/api/arrivals/{slot_id}", response_model=ArrivalSlotResponse)
def update_arrival_slot(
    slot_id: str,
    body: ArrivalSlotUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    slot = db.scalar(select(ArrivalSlot).where(ArrivalSlot.id == slot_id, ArrivalSlot.active.is_(True)))
    if not slot:
        raise HTTPException(status_code=404, detail="No encontré esa ruta pendiente")
    if slot.assigned_visit_id:
        raise HTTPException(status_code=409, detail="La ruta ya fue asignada a un vehículo")
    if body.placa is not None:
        slot.placa = body.placa
    if body.auxiliar is not None:
        slot.auxiliar = body.auxiliar
    if body.hora_limite is not None:
        slot.hora_limite = body.hora_limite
    if body.tmec is not None:
        slot.tmec = body.tmec
    slot.updated_at = utc_now()
    db.commit()
    db.refresh(slot)
    return slot_to_response(slot)


@app.get("/api/plates/{placa}/alerts", response_model=PlateAlertResponse)
def plate_alerts(
    placa: str,
    target_min: int = Query(default=18, ge=1, le=24 * 60),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    normalized = normalize_placa(placa)
    if not normalized or len(normalized) < 4:
        raise HTTPException(status_code=422, detail="Placa inválida")
    start = utc_now() - timedelta(days=15)
    rows = db.scalars(
        select(ParkingVisit).where(
            ParkingVisit.placa == normalized,
            ParkingVisit.salida_at.is_not(None),
            ParkingVisit.salida_at >= start,
        )
    ).all()
    late_count = sum(1 for row in rows if (row.duracion_min or 0) > target_min)
    return PlateAlertResponse(
        placa=normalized,
        target_min=target_min,
        late_count_15d=late_count,
        should_agilizar=late_count >= 2,
    )


@app.post("/api/visits/ingreso", response_model=VisitResponse, status_code=201)
def registrar_ingreso(body: IngresoRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if active_by_ficha(db, body.ficha_code):
        raise HTTPException(status_code=409, detail="La ficha ya está asociada a un vehículo activo")

    arrival_slot = arrival_slot_for_ingreso(db, body)
    ruta = arrival_slot.ruta_sorting if arrival_slot else body.ruta
    ubicacion = arrival_slot.zona if arrival_slot else body.ubicacion
    dock = arrival_slot.est_wtd if arrival_slot else body.dock

    placa_activa = db.scalar(
        select(ParkingVisit).where(ParkingVisit.placa == body.placa, ParkingVisit.salida_at.is_(None))
    )
    if placa_activa:
        raise HTTPException(status_code=409, detail="La placa ya se encuentra activa en patio")

    ubicacion_activa = db.scalar(
        select(ParkingVisit).where(ParkingVisit.ubicacion == ubicacion, ParkingVisit.salida_at.is_(None))
    )
    if ubicacion_activa:
        raise HTTPException(status_code=409, detail="La ubicación ya está ocupada")

    dock_activo = None
    if not arrival_slot:
        dock_activo = db.scalar(select(ParkingVisit).where(ParkingVisit.dock == dock, ParkingVisit.salida_at.is_(None)))
    if dock_activo:
        raise HTTPException(status_code=409, detail="La doka/dock ya está ocupada")

    now = utc_now()
    visit = ParkingVisit(
        id=str(uuid.uuid4()),
        ficha_code=body.ficha_code,
        placa=body.placa,
        ruta=ruta,
        cedula=body.cedula,
        ubicacion=ubicacion,
        dock=dock,
        arrival_slot_id=arrival_slot.id if arrival_slot else None,
        mlp=arrival_slot.mlp if arrival_slot else None,
        spr=arrival_slot.spr if arrival_slot else None,
        ola_wtd=arrival_slot.ola_wtd if arrival_slot else None,
        conductor=body.conductor,
        observaciones=body.observaciones,
        ingreso_at=now,
        operador_ingreso=user.username,
        created_at=now,
        updated_at=now,
    )
    db.add(visit)
    if arrival_slot:
        arrival_slot.assigned_visit_id = visit.id
        arrival_slot.assigned_at = now
        arrival_slot.updated_at = now
    db.commit()
    db.refresh(visit)
    append_flash_parking_log(
        visit,
        "Ingreso",
        recorded_at=now,
        device_id=body.device_id or user.username,
        lector_qr=body.lector_qr or body.ficha_code,
    )
    return visit_to_response(visit)


@app.post("/api/visits/salida", response_model=VisitResponse)
def registrar_salida(body: SalidaRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visit = active_by_ficha(db, body.ficha_code)
    if not visit:
        raise HTTPException(status_code=404, detail="No hay vehículo activo asociado a esta ficha")

    now = utc_now()
    duration = max(0, int(round((now - visit.ingreso_at).total_seconds() / 60)))
    visit.salida_at = now
    visit.duracion_min = duration
    visit.salida_observaciones = body.observaciones
    visit.operador_salida = user.username
    visit.updated_at = now
    db.commit()
    db.refresh(visit)
    append_flash_parking_log(
        visit,
        "Salida",
        recorded_at=now,
        device_id=body.device_id or user.username,
        lector_qr=body.lector_qr or body.ficha_code,
    )
    return visit_to_response(visit)


@app.get("/api/visits/active", response_model=list[VisitResponse])
def active_visits(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.scalars(
        select(ParkingVisit).where(ParkingVisit.salida_at.is_(None)).order_by(ParkingVisit.ingreso_at.desc())
    ).all()
    return [visit_to_response(row) for row in rows]


@app.get("/api/visits/history", response_model=list[VisitResponse])
def history(
    date_filter: Optional[str] = Query(default=None, alias="date", max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    from_date: Optional[str] = Query(default=None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: Optional[str] = Query(default=None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    q: Optional[str] = Query(default=None, max_length=40),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if date_filter:
        start, end = local_day_bounds(parse_date_str(date_filter))
    else:
        today = datetime.now(APP_TZ).date()
        start_day = parse_date_str(from_date, today)
        end_day = parse_date_str(to_date, start_day)
        if end_day < start_day:
            raise HTTPException(status_code=400, detail="Rango de fechas inválido")
        start, end = local_range_bounds(start_day, end_day)

    stmt = select(ParkingVisit).where(ParkingVisit.ingreso_at >= start, ParkingVisit.ingreso_at < end)
    if q:
        term = f"%{q.strip().upper()}%"
        stmt = stmt.where(ParkingVisit.placa.like(term))
    rows = db.scalars(stmt.order_by(ParkingVisit.ingreso_at.desc())).all()
    return [visit_to_response(row) for row in rows]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    today = datetime.now(APP_TZ).date()
    start, end = local_day_bounds(today)

    ingresos_hoy = db.scalars(
        select(ParkingVisit).where(ParkingVisit.ingreso_at >= start, ParkingVisit.ingreso_at < end)
    ).all()
    salidas_hoy = db.scalars(
        select(ParkingVisit).where(ParkingVisit.salida_at >= start, ParkingVisit.salida_at < end)
    ).all()
    activos = db.scalars(select(ParkingVisit).where(ParkingVisit.salida_at.is_(None))).all()
    completados = [row for row in salidas_hoy if row.duracion_min is not None]
    promedio = round(sum(row.duracion_min for row in completados) / len(completados)) if completados else None

    ultimos_ingresos = db.scalars(select(ParkingVisit).order_by(ParkingVisit.ingreso_at.desc()).limit(8)).all()
    ultimas_salidas = db.scalars(
        select(ParkingVisit).where(ParkingVisit.salida_at.is_not(None)).order_by(ParkingVisit.salida_at.desc()).limit(8)
    ).all()

    return {
        "fecha": today.isoformat(),
        "ingresos_hoy": len(ingresos_hoy),
        "activos": len(activos),
        "salidas_hoy": len(salidas_hoy),
        "tiempo_promedio_min": promedio,
        "tiempo_promedio_formato": format_duration(promedio) if promedio is not None else "–",
        "ultimos_ingresos": [visit_to_response(row) for row in ultimos_ingresos],
        "ultimas_salidas": [visit_to_response(row) for row in ultimas_salidas],
    }


@app.get("/api/reports")
def reports(
    from_date: Optional[str] = Query(default=None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    to_date: Optional[str] = Query(default=None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = datetime.now(APP_TZ).date()
    start_day = parse_date_str(from_date, today - timedelta(days=30))
    end_day = parse_date_str(to_date, today)
    if end_day < start_day:
        raise HTTPException(status_code=400, detail="Rango de fechas inválido")
    start, end = local_range_bounds(start_day, end_day)
    completed = db.scalars(
        select(ParkingVisit).where(
            ParkingVisit.ingreso_at >= start,
            ParkingVisit.ingreso_at < end,
            ParkingVisit.salida_at.is_not(None),
        )
    ).all()

    by_plate: dict[str, dict] = {}
    by_route: dict[str, dict] = {}
    for row in completed:
        dur = row.duracion_min or 0
        plate = by_plate.setdefault(row.placa, {"placa": row.placa, "visitas": 0, "total_min": 0, "max_min": 0})
        plate["visitas"] += 1
        plate["total_min"] += dur
        plate["max_min"] = max(plate["max_min"], dur)

        route = by_route.setdefault(row.ruta, {"ruta": row.ruta, "visitas": 0, "vehiculos": set(), "total_min": 0, "max_min": 0})
        route["visitas"] += 1
        route["vehiculos"].add(row.placa)
        route["total_min"] += dur
        route["max_min"] = max(route["max_min"], dur)

    plate_rows = []
    for item in by_plate.values():
        avg = round(item["total_min"] / item["visitas"]) if item["visitas"] else 0
        plate_rows.append({**item, "promedio_min": avg, "total_formato": format_duration(item["total_min"]), "promedio_formato": format_duration(avg)})
    plate_rows.sort(key=lambda x: x["total_min"], reverse=True)

    route_rows = []
    for item in by_route.values():
        avg = round(item["total_min"] / item["visitas"]) if item["visitas"] else 0
        route_rows.append({
            "ruta": item["ruta"],
            "visitas": item["visitas"],
            "vehiculos": len(item["vehiculos"]),
            "total_min": item["total_min"],
            "promedio_min": avg,
            "max_min": item["max_min"],
            "total_formato": format_duration(item["total_min"]),
            "promedio_formato": format_duration(avg),
            "max_formato": format_duration(item["max_min"]),
        })
    route_rows.sort(key=lambda x: x["total_min"], reverse=True)

    ranking = sorted(completed, key=lambda row: row.duracion_min or 0, reverse=True)[:50]
    return {
        "from_date": start_day.isoformat(),
        "to_date": end_day.isoformat(),
        "total_completados": len(completed),
        "por_vehiculo": plate_rows,
        "por_ruta": route_rows,
        "ranking": [visit_to_response(row) for row in ranking],
    }


@app.get("/api/export.csv")
def export_csv(
    date_filter: Optional[str] = Query(default=None, alias="date", max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    day = parse_date_str(date_filter, datetime.now(APP_TZ).date())
    start, end = local_day_bounds(day)
    rows = db.scalars(
        select(ParkingVisit).where(ParkingVisit.ingreso_at >= start, ParkingVisit.ingreso_at < end).order_by(ParkingVisit.ingreso_at.desc())
    ).all()

    headers = [
        "ficha",
        "placa",
        "cedula",
        "ruta",
        "ubicacion",
        "doka_dock",
        "mlp",
        "spr",
        "ola_wtd",
        "conductor",
        "fecha_ingreso",
        "hora_ingreso",
        "fecha_salida",
        "hora_salida",
        "duracion_min",
        "duracion",
        "estado",
        "operador_ingreso",
        "operador_salida",
    ]
    lines = [",".join(headers)]
    for row in rows:
        r = visit_to_response(row)
        values = [
            r["ficha_code"],
            r["placa"],
            r.get("cedula") or "",
            r["ruta"],
            r["ubicacion"],
            r["dock"],
            r.get("mlp") or "",
            r.get("spr") or "",
            r.get("ola_wtd") or "",
            r.get("conductor") or "",
            r["ingreso_fecha"],
            r["ingreso_hora"],
            r.get("salida_fecha") or "",
            r.get("salida_hora") or "",
            str(r.get("duracion_min") or ""),
            r["duracion_formato"],
            r["estado"],
            r["operador_ingreso"],
            r.get("operador_salida") or "",
        ]
        escaped = [f'"{value.replace(chr(34), chr(34) + chr(34))}"' for value in values]
        lines.append(",".join(escaped))

    content = "\ufeff" + "\n".join(lines)
    filename = f"parqueadero_{day.isoformat()}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return Response("Sistema de Parqueadero API", media_type="text/plain")
