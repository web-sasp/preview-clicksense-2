import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
requests_mod = __import__("requests")

load_dotenv_mod = __import__("dotenv")
load_dotenv_mod.load_dotenv()

BOT_INTERNAL_URL = os.getenv("BOT_INTERNAL_URL", "http://127.0.0.1:5005/notify")
BOT_INTERNAL_TOKEN = os.getenv("BOT_INTERNAL_TOKEN")

# Dirección pública de la web (la misma PUBLIC_URL que usan main.py y bot.py), para los enlaces de los botones
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

# Pausa entre mensajes para no saturar al bot ni el límite de Discord
PAUSA_ENTRE_DMS = 0.4


def obtener_hora_espanola() -> str:
    """Devuelve la fecha y hora actual en la España peninsular con el formato requerido."""
    try:
        now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))
        hora_str = now_madrid.strftime("%H:%M")
        return f"hoy a las {hora_str}"
    except Exception:
        return datetime.now().strftime("hoy a las %H:%M")


def _enviar_al_bot(payload: dict) -> bool:
    """Manda la petición al servidor interno del bot y valida su respuesta."""
    headers = {"X-Internal-Token": BOT_INTERNAL_TOKEN or ""}

    try:
        respuesta = requests_mod.post(BOT_INTERNAL_URL, json=payload, headers=headers, timeout=4)
        if respuesta.status_code == 200:
            try:
                data = respuesta.json()
            except ValueError:
                print("[notifier] Respuesta del bot no válida (no es JSON).")
                return False
            if payload.get("tipo") == "notificaciones" and not data.get("campanita"):
                # El mensaje se ha enviado, pero lo ha construido un bot.py antiguo (título gris por defecto y sin texto)
                print("[notifier] ⚠️ El bot que está en marcha es la versión ANTIGUA de bot.py: el mensaje de la campanita "
                      "sale sin texto. Sustituye bot.py y reinicia run.py por completo (cierra antes cualquier otro Python abierto).")
            return bool(data.get("success", False))
        else:
            print(f"[notifier] El bot rechazó la notificación (Status {respuesta.status_code}): {respuesta.text}")
            return False
    except requests_mod.RequestException as error:
        print(f"[notifier] No se pudo conectar con el servidor interno del bot en el puerto 5005: {error}")
        return False


def enviar_notificacion(discord_id: str, tipo: str, descripcion: str,
                        titulo: str = None, campos: list = None,
                        boton_texto: str = None, boton_url: str = None,
                        notif_categoria: str = None) -> bool:
    """
    Le pide al bot de Discord que mande un DM con embed a discord_id.
    Valida la respuesta del servidor interno del bot.

    notif_categoria: si el mensaje se envía porque el usuario tiene una campanita
    activada (p. ej. "novedades"), el bot añade el botón "Desactivar notificaciones".
    """
    if not discord_id:
        return False

    footer_text = f"ClickSense RP • Sistema de notificaciones • {obtener_hora_espanola()}"

    payload = {
        "discord_id": str(discord_id),
        "tipo": tipo,
        "titulo": titulo,
        "descripcion": descripcion,
        "campos": campos or [],
        "thumbnail": "https://i.imgur.com/ibt9508.png",
        "footer_text": footer_text,
        "footer_icon": "https://i.imgur.com/ibt9508.png",
        "button_text": boton_texto or "Acceder al portal online",
        "button_url": boton_url or "",
        "notif_categoria": notif_categoria or "",
    }
    return _enviar_al_bot(payload)


def enviar_cambio_notificaciones(discord_id: str, categoria: str, activas: bool) -> bool:
    """
    Confirmación por DM al pulsar la campanita: embed blanco "SISTEMA DE NOTIFICACIONES"
    con la categoría, el estado (activadas / desactivadas) y el botón para cambiarlo.
    El texto del mensaje se construye en bot.py (construir_embed_notificaciones).
    """
    if not discord_id:
        return False

    payload = {
        "discord_id": str(discord_id),
        "tipo": "notificaciones",
        "categoria": categoria or "novedades",
        "activas": bool(activas),
    }
    return _enviar_al_bot(payload)


def enviar_novedad(novedad: dict, discord_ids: list) -> dict:
    """
    Envía una novedad por DM (vía el bot) a cada discord_id de la lista.
    Pensada para ejecutarse en segundo plano (BackgroundTasks).
    Devuelve un resumen {"enviados": n, "fallidos": n}.
    """
    categoria = novedad.get("categoria") or "Comunicados"
    autor = novedad.get("autor") or "Staff"
    destacada = bool(novedad.get("destacada"))
    titulo_novedad = novedad.get("titulo", "")

    titulo = f"NUEVA NOVEDAD REGISTRADA - {titulo_novedad}"[:256]
    descripcion = (novedad.get("descripcion") or "")[:4000]

    campos = [
        {"name": "Categoría", "value": categoria, "inline": True},
        {"name": "Publicado por", "value": autor, "inline": True},
    ]
    if destacada:
        campos.append({"name": "Destacada", "value": "Sí", "inline": True})

    # Añadimos el bloque de texto de atención justo debajo de los campos de metadatos
    campos.append({
        "name": "\u200b",
        "value": "-# ¡ATENCIÓN! Esto es un mensaje automático procedente del sistema de notificaciones de ClickSense. Puedes desactivar esta notificación en cualquier momento pulsando en el botón adjunto.",
        "inline": False
    })

    enviados = 0
    fallidos = 0
    for discord_id in discord_ids:
        ok = enviar_notificacion(
            discord_id=discord_id,
            tipo="novedad",
            titulo=titulo,
            descripcion=descripcion,
            campos=campos,
            notif_categoria="novedades",   # añade el botón "Desactivar notificaciones" del que habla el texto
        )
        if ok:
            enviados += 1
        else:
            fallidos += 1
        time.sleep(PAUSA_ENTRE_DMS)

    print(f"[notifier] Novedad '{titulo_novedad}': {enviados} enviados, {fallidos} fallidos.")
    return {"enviados": enviados, "fallidos": fallidos}


def enviar_aviso_evento(titulo_evento: str, discord_ids: list, minutos: int = 30,
                        inicio_ts: int = None, ubicacion: str = "") -> dict:
    """
    Aviso por DM de que un evento del calendario está a punto de empezar.
    Lo lanza main.py unos 30 minutos antes, a quienes tienen activada la campanita
    de calendario.html. Lleva el botón del calendario y el de "Desactivar notificaciones".
    """
    titulo_evento = str(titulo_evento or "Evento").strip()[:200]
    minutos = max(1, int(minutos))
    cuanto = "1 minuto" if minutos == 1 else f"{minutos} minutos"

    lineas = [f"- **__TÍTULO DEL EVENTO__**: {titulo_evento}"]
    if inicio_ts:
        # <t:...:t> lo muestra Discord en la hora local de cada usuario
        lineas.append(f"- **__HORA DE INICIO__**: <t:{int(inicio_ts)}:t>")
    if ubicacion:
        lineas.append(f"- **__UBICACIÓN__**: {str(ubicacion).strip()[:200]}")

    descripcion = (
        f"¡Buenas!, el siguiente evento va a comenzar en **{cuanto}**:\n\n"
        + "\n".join(lineas) + "\n\n"
        "-# ¡ATENCIÓN! Esto es un mensaje automático procedente del sistema de notificaciones de ClickSense. "
        "Puedes desactivar esta notificación en cualquier momento pulsando en el botón adjunto."
    )

    enviados = 0
    fallidos = 0
    for discord_id in discord_ids:
        ok = enviar_notificacion(
            discord_id=discord_id,
            tipo="evento_proximo",
            titulo="SISTEMA DE NOTIFICACIONES",
            descripcion=descripcion,
            boton_texto="Acceder al panel online",
            boton_url=f"{PUBLIC_URL}/calendario.html",
            notif_categoria="calendario",   # añade el botón "Desactivar notificaciones"
        )
        if ok:
            enviados += 1
        else:
            fallidos += 1
        time.sleep(PAUSA_ENTRE_DMS)

    print(f"[notifier] Aviso del evento '{titulo_evento}': {enviados} enviados, {fallidos} fallidos.")
    return {"enviados": enviados, "fallidos": fallidos}