"""
Opiniones (feedback) de los usuarios de ClickSense RP.

Va junto a main.py. En main.py solo hay que añadir, debajo de las dos líneas de whitelist_api:

    import feedback_api
    app.include_router(feedback_api.router)

Cómo funciona:
  - feedback.html envía la opinión del usuario que tiene la sesión iniciada.
  - Se guarda en data/feedback.json con su nombre e ID de Discord (los pone el
    servidor a partir de la sesión: no se pueden falsear desde el navegador).
  - admin/feedbacks.html las lee. Solo pueden verlas los rangos de RANGOS_QUE_VEN_FEEDBACK.
"""
import os
import json
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Cookie
from sqlalchemy.orm import Session
from dotenv import load_dotenv

import models
from database import get_db

load_dotenv()

router = APIRouter()

# ==========================================
# CONFIGURACIÓN
# ==========================================
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.json")
STAFF_FILE = os.path.join(DATA_DIR, "staff.json")

# El mismo ID maestro que usa la gestión de staff
MASTER_ADMIN_ID = os.getenv("MASTER_ADMIN_ID", "732650490588037160").strip()

# Qué rangos del staff pueden leer las opiniones (en minúsculas, como salen en la
# gestión de staff). El ID maestro puede siempre.
RANGOS_QUE_VEN_FEEDBACK = {"owner", "desarrollador", "game master"}

HORAS_ENTRE_ENVIOS = 24           # cada usuario puede opinar una vez cada X horas (0 = sin límite)
MAX_GUARDADOS = 5000              # se conservan las opiniones más recientes
TEXTO_MAX = 1000                  # caracteres por cada respuesta escrita
TEXTO_MIN_TOTAL = 10              # entre todas las respuestas escritas, como mínimo
RECOMIENDA_VALIDOS = {"si", "quiza", "no"}

# ==========================================
# UTILIDADES
# ==========================================
_lock = threading.RLock()


def _leer(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return default


def _escribir(path: str, data) -> None:
    """Escritura atómica: nunca queda un archivo a medias."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def _opiniones() -> list:
    data = _leer(FEEDBACK_FILE, [])
    return [o for o in data if isinstance(o, dict)] if isinstance(data, list) else []


def _texto(valor, maximo: int) -> str:
    """Texto limpio: conserva los saltos de línea, sin espacios repetidos ni líneas en blanco seguidas."""
    bruto = str(valor or "").replace("\r\n", "\n").replace("\r", "\n")
    lineas = []
    for linea in bruto.split("\n"):
        linea = " ".join(linea.split())
        if linea == "" and (not lineas or lineas[-1] == ""):
            continue
        lineas.append(linea)
    return "\n".join(lineas).strip()[:maximo].strip()


def _fecha(iso):
    try:
        f = datetime.fromisoformat(str(iso))
        return f if f.tzinfo else f.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _staff_activo(s: dict) -> bool:
    estado = str(s.get("status") or "Activo").strip().lower()
    return estado not in ("inactivo", "inactive", "0", "false")


def _puede_ver(discord_id) -> bool:
    """¿Puede leer las opiniones? ID maestro, o staff activo con uno de los rangos permitidos."""
    if not discord_id:
        return False
    did = str(discord_id).strip()
    if did == MASTER_ADMIN_ID:
        return True
    lista = _leer(STAFF_FILE, [])
    if not isinstance(lista, list):
        return False
    return any(
        isinstance(s, dict)
        and _staff_activo(s)
        and str(s.get("discordId") or "").strip() == did
        and str(s.get("rango") or "").strip().lower() in RANGOS_QUE_VEN_FEEDBACK
        for s in lista
    )


def _exigir_lector(discord_id) -> None:
    if not discord_id:
        raise HTTPException(status_code=401, detail="No hay sesión activa")
    if not _puede_ver(discord_id):
        raise HTTPException(status_code=403, detail="Tu rango no permite ver las opiniones de los usuarios")


def _usuario_sesion(discord_id, db: Session):
    if not discord_id:
        raise HTTPException(status_code=401, detail="No hay sesión activa")
    usuario = db.query(models.UsuarioDB).filter(models.UsuarioDB.discord_id == discord_id).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return usuario


async def _cuerpo_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON no válido")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Formato no válido")
    return body


def _nuevo_id(lista: list) -> str:
    usados = {str(o.get("id")) for o in lista}
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin letras/números que se confunden
    while True:
        nuevo = "FB-" + "".join(secrets.choice(alfabeto) for _ in range(6))
        if nuevo not in usados:
            return nuevo


def _proximo_envio(lista: list, discord_id):
    """Fecha a partir de la cual ese usuario puede volver a opinar (None = ya puede)."""
    if HORAS_ENTRE_ENVIOS <= 0:
        return None
    did = str(discord_id)
    fechas = [_fecha(o.get("fecha")) for o in lista if str(o.get("discordId")) == did]
    fechas = [f for f in fechas if f is not None]
    if not fechas:
        return None
    proximo = max(fechas) + timedelta(hours=HORAS_ENTRE_ENVIOS)
    return proximo if proximo > datetime.now(timezone.utc) else None


# ==========================================
# RUTAS PARA EL USUARIO (feedback.html)
# ==========================================

@router.get("/api/feedback/mio")
def mi_feedback(discord_id: str = Cookie(None)):
    """Si el usuario puede opinar ahora o tiene que esperar (no devuelve opiniones de nadie)."""
    if not discord_id:
        raise HTTPException(status_code=401, detail="No hay sesión activa")
    lista = _opiniones()
    proximo = _proximo_envio(lista, discord_id)
    return {
        "enviadas": sum(1 for o in lista if str(o.get("discordId")) == str(discord_id)),
        "puedeEnviar": proximo is None,
        "proximoEnvio": proximo.isoformat() if proximo else None,
        "horasEntreEnvios": HORAS_ENTRE_ENVIOS,
    }


@router.post("/api/feedback")
async def enviar_feedback(request: Request, discord_id: str = Cookie(None), db: Session = Depends(get_db)):
    """Guarda la opinión del usuario que tiene la sesión iniciada."""
    usuario = _usuario_sesion(discord_id, db)
    body = await _cuerpo_json(request)

    try:
        valoracion = int(body.get("valoracion"))
    except (TypeError, ValueError):
        valoracion = 0
    if not 1 <= valoracion <= 5:
        raise HTTPException(status_code=400, detail="Falta la valoración de 1 a 5 estrellas")

    positivo = _texto(body.get("positivo"), TEXTO_MAX)
    mejoras = _texto(body.get("mejoras"), TEXTO_MAX)
    comentario = _texto(body.get("comentario"), TEXTO_MAX)
    if len(positivo) + len(mejoras) + len(comentario) < TEXTO_MIN_TOTAL:
        raise HTTPException(status_code=400, detail="Escribe al menos un par de frases para que la opinión nos sirva")

    recomienda = str(body.get("recomienda") or "").strip().lower()
    if recomienda not in RECOMIENDA_VALIDOS:
        recomienda = ""

    with _lock:
        lista = _opiniones()
        proximo = _proximo_envio(lista, discord_id)
        if proximo is not None:
            raise HTTPException(status_code=429, detail="Ya has enviado tu opinión hace poco")

        opinion = {
            "id": _nuevo_id(lista),
            "usuario": usuario.username or "Usuario",
            "discordId": str(discord_id),
            "valoracion": valoracion,         # de 1 a 5 estrellas
            "positivo": positivo,             # lo que más le gusta
            "mejoras": mejoras,               # lo que cambiaría
            "recomienda": recomienda,         # 'si' / 'quiza' / 'no' / '' (no contestó)
            "comentario": comentario,         # algo más que quiera añadir
            "leido": False,                   # el staff lo marca al revisarla
            "fecha": datetime.now(timezone.utc).isoformat(),
        }
        lista.insert(0, opinion)
        _escribir(FEEDBACK_FILE, lista[:MAX_GUARDADOS])

    print(f"[feedback] {opinion['id']} · {opinion['usuario']} ({discord_id}): {valoracion}/5")
    return {"ok": True, "id": opinion["id"]}


# ==========================================
# RUTAS PARA EL PANEL (admin/feedbacks.html)
# ==========================================

@router.get("/api/feedback")
def listar_feedback(discord_id: str = Cookie(None)):
    """Todas las opiniones, las más recientes primero. Solo para los rangos autorizados."""
    _exigir_lector(discord_id)
    return _opiniones()


@router.post("/api/feedback/leido")
async def marcar_leido(request: Request, discord_id: str = Cookie(None)):
    """Marca una opinión como leída o no leída: { id, leido }."""
    _exigir_lector(discord_id)
    body = await _cuerpo_json(request)

    with _lock:
        lista = _opiniones()
        opinion = next((o for o in lista if str(o.get("id")) == str(body.get("id"))), None)
        if opinion is None:
            raise HTTPException(status_code=404, detail="Opinión no encontrada")
        opinion["leido"] = bool(body.get("leido"))
        _escribir(FEEDBACK_FILE, lista)
    return opinion


@router.post("/api/feedback/borrar")
async def borrar_feedback(request: Request, discord_id: str = Cookie(None)):
    """Elimina una opinión: { id }."""
    _exigir_lector(discord_id)
    body = await _cuerpo_json(request)

    with _lock:
        lista = _opiniones()
        restantes = [o for o in lista if str(o.get("id")) != str(body.get("id"))]
        if len(restantes) == len(lista):
            raise HTTPException(status_code=404, detail="Opinión no encontrada")
        _escribir(FEEDBACK_FILE, restantes)

    print(f"[feedback] {body.get('id')} eliminada por {discord_id}")
    return {"ok": True}