"""
notificaciones_dm.py  ·  ClickSense RP

Mensaje privado de confirmación cuando un usuario activa o desactiva las
notificaciones, ya sea desde la campanita de la web o desde el botón del
propio mensaje.

  - enviar_dm_notificaciones(...)        -> se llama desde main.py (FastAPI)
  - registrar_boton_notificaciones(...)  -> se llama una vez desde bot.py
"""

import inspect
import os
import re

import aiohttp
import discord

API = "https://discord.com/api/v10"
COLOR_BLANCO = 0xFFFFFF          # barra izquierda del embed
PREFIJO = "cs_notif"             # custom_id del botón: cs_notif:<categoria>:<on|off>

# Clave que envía la web  ->  texto que se ve en el embed.
# Añade aquí cada apartado nuevo que tenga campanita.
CATEGORIAS = {
    "novedades": "Novedades",
    "eventos": "Eventos",
    "sugerencias": "Sugerencias",
    "tickets": "Tickets",
}


def _token() -> str:
    # Usa aquí el mismo nombre de variable con el que bot.py lee su token.
    return (
        os.getenv("DISCORD_BOT_TOKEN")
        or os.getenv("DISCORD_TOKEN")
        or os.getenv("BOT_TOKEN")
        or ""
    )


def _clave(categoria) -> str:
    clave = re.sub(r"[^a-z0-9_-]", "", str(categoria or "").strip().lower())[:32]
    return clave or "novedades"


def _nombre(clave: str) -> str:
    return CATEGORIAS.get(clave, clave.replace("-", " ").replace("_", " ").capitalize())


def construir_embed(categoria, activas: bool) -> discord.Embed:
    clave = _clave(categoria)
    estado = "Notificaciones activadas" if activas else "Notificaciones desactivadas"
    aviso = (
        "Puedes desactivar esta notificación en cualquier momento pulsando en el botón adjunto."
        if activas
        else "Puedes volver a activar esta notificación en cualquier momento pulsando en el botón adjunto."
    )
    descripcion = (
        "¡Buenas!, se ha registrado una actualización en las notificaciones del sistema:\n\n"
        f"- **__CATEGORÍA__**: {_nombre(clave)}\n"
        f"- **__ESTADO__**: {estado}\n\n"
        "-# ¡ATENCIÓN! Esto es un mensaje automático procedente del sistema de "
        f"notificaciones de ClickSense. {aviso}"
    )
    return discord.Embed(
        title="SISTEMA DE NOTIFICACIONES",
        description=descripcion,
        color=COLOR_BLANCO,
    )


def construir_boton(categoria, activas: bool) -> discord.ui.View:
    """Botón adjunto: hace lo contrario del estado actual."""
    clave = _clave(categoria)
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Desactivar notificaciones" if activas else "Activar notificaciones",
            emoji="🔕" if activas else "🔔",
            style=discord.ButtonStyle.secondary if activas else discord.ButtonStyle.success,
            custom_id=f"{PREFIJO}:{clave}:{'off' if activas else 'on'}",
        )
    )
    return view


async def enviar_dm_notificaciones(user_id, categoria, activas: bool) -> bool:
    """
    Envía el embed por privado. Devuelve True si Discord lo ha aceptado y
    False si no se pudo (MD cerrados, token ausente, sin conexión...).
    Nunca lanza excepciones: un fallo aquí no debe romper el guardado.
    """
    token = _token()
    user_id = str(user_id or "").strip()
    if not token or not user_id.isdigit():
        print("[notificaciones] Falta el token del bot o el ID de Discord no es válido.")
        return False

    payload = {
        "embeds": [construir_embed(categoria, activas).to_dict()],
        "components": construir_boton(categoria, activas).to_components(),
    }
    try:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bot {token}"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as sesion:
            async with sesion.post(
                f"{API}/users/@me/channels", json={"recipient_id": user_id}
            ) as r:
                if r.status != 200:
                    print(f"[notificaciones] No se pudo abrir el MD con {user_id}: HTTP {r.status}")
                    return False
                canal = (await r.json())["id"]

            async with sesion.post(f"{API}/channels/{canal}/messages", json=payload) as r:
                if r.status != 200:
                    # 403 = el usuario tiene los mensajes directos cerrados
                    print(f"[notificaciones] MD rechazado para {user_id}: HTTP {r.status}")
                    return False
                return True
    except Exception as e:  # red, timeout, etc.
        print(f"[notificaciones] Error enviando el MD a {user_id}: {e}")
        return False


def registrar_boton_notificaciones(bot, guardar_preferencia) -> None:
    """
    Hace que el botón del mensaje funcione (también tras reiniciar el bot).

    guardar_preferencia(user_id: str, categoria: str, activas: bool)
        -> la MISMA función que usa el endpoint de la web para guardar la
           preferencia. Puede ser normal o async.
    """

    async def _on_interaction(interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        partes = str((interaction.data or {}).get("custom_id", "")).split(":")
        if len(partes) != 3 or partes[0] != PREFIJO or partes[2] not in ("on", "off"):
            return

        categoria, activas = partes[1], partes[2] == "on"
        try:
            resultado = guardar_preferencia(str(interaction.user.id), categoria, activas)
            if inspect.isawaitable(resultado):
                await resultado
        except Exception as e:
            print(f"[notificaciones] Error guardando la preferencia: {e}")
            await interaction.response.send_message(
                "No se ha podido guardar el cambio. Inténtalo de nuevo desde la web.",
                ephemeral=True,
            )
            return

        # Se actualiza el mismo mensaje con el nuevo estado y el botón contrario
        await interaction.response.edit_message(
            embed=construir_embed(categoria, activas),
            view=construir_boton(categoria, activas),
        )

    bot.add_listener(_on_interaction, "on_interaction")