import os
import re
from datetime import datetime

import aiohttp
import discord
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TOKEN = BOT_TOKEN  # Alias obligatorio para que run.py importe el bot correctamente
INTERNAL_TOKEN = os.getenv("BOT_INTERNAL_TOKEN")
INTERNAL_PORT = int(os.getenv("BOT_INTERNAL_PORT", "5005"))

# Dirección interna de la web (FastAPI). El bot la usa para guardar la preferencia
# cuando el usuario pulsa el botón del mensaje. En .env: WEB_INTERNAL_URL=http://127.0.0.1:8000
WEB_INTERNAL_URL = os.getenv("WEB_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
WEB_PREFS_URL = WEB_INTERNAL_URL + "/api/interno/notificaciones/preferencias"

# Dirección pública de la web (la misma PUBLIC_URL que usa main.py). De ella salen los
# enlaces del botón "Acceder al panel online" de los mensajes de las campanitas.
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

PLANTILLAS = {
    "registro": {"titulo": "¡Bienvenido a ClickSense RP!", "color": 0xA855F7, "icono": "🎉"},
    "sancion": {"titulo": "Nueva sanción registrada", "color": 0xEF4444, "icono": "⚠️"},
    "evento": {"titulo": "Nuevo evento en el servidor", "color": 0x38BDF8, "icono": "📅"},
    "ticket": {"titulo": "Ticket cerrado", "color": 0x10B981, "icono": "🎫"},
    "logout": {"titulo": "Sesión cerrada", "color": 0xF59E0B, "icono": "🔒"},
    # Campanita activada / desactivada: barra izquierda blanca
    "notificaciones": {"titulo": "SISTEMA DE NOTIFICACIONES", "color": 0xFFFFFF, "icono": "🔔"},
    # Aviso "tu evento empieza en 30 minutos" (lo envía la web a quien tiene la campanita del calendario)
    "evento_proximo": {"titulo": "SISTEMA DE NOTIFICACIONES", "color": 0xFFFFFF, "icono": "📅"},
    "default": {"titulo": "Notificación de ClickSense RP", "color": 0x71717A, "icono": "🔔"},
}

# ---------- Sistema de notificaciones (campanita) ----------
TITULO_NOTIF = "SISTEMA DE NOTIFICACIONES"
PREFIJO_NOTIF = "cs_notif"   # custom_id del botón: cs_notif:<categoria>:<on|off>:<editar|nuevo>

# Apartados con campanita. La clave es la que envía la web (y debe estar también en
# CATEGORIAS_NOTIF de main.py). Para cada uno:
#   nombre         -> texto que se ve en el embed como CATEGORÍA
#   panel          -> página a la que lleva el botón "Acceder al panel online"
#   panel_primero  -> True: los botones de enlace van a la izquierda del de activar/desactivar
#   connect        -> (opcional) comandos de conexión que se muestran en el mensaje
#   fivem_url      -> (opcional) añade el botón "Conectar a FiveM"
CATEGORIAS_NOTIF = {
    "novedades": {
        "nombre": "Novedades",
        "panel": "/home.html",
    },
    "servidor": {
        "nombre": "Servidor",
        "panel": "/servidor.html",
        "panel_primero": True,
        # Para mostrar también la IP directa, añade a la lista: "connect 91.134.62.42:30120"
        "connect": ["connect cfx.re/join/ma75bv"],
        # Discord solo admite enlaces http/https en los botones (no vale fivem://...).
        # Este enlace de cfx.re abre FiveM y conecta al servidor.
        "fivem_url": "https://cfx.re/join/ma75bv",
    },
    "calendario": {
        "nombre": "Calendario",
        "panel": "/calendario.html",
        "panel_primero": True,
    },
}

intents = discord.Intents.default()
bot = discord.Client(intents=intents)


def construir_embed(tipo: str, titulo, descripcion: str, campos: list) -> discord.Embed:
    plantilla = PLANTILLAS.get(tipo, PLANTILLAS["default"])
    embed = discord.Embed(
        title=titulo or f"{plantilla['icono']} {plantilla['titulo']}",
        description=descripcion,
        color=plantilla["color"],
        timestamp=datetime.utcnow(),
    )

    for campo in campos:
        name = campo.get("name", "\u200b")
        value = campo.get("value", "\u200b")

        embed.add_field(
            name=name,
            value=value,
            inline=campo.get("inline", False),
        )

    # URL de miniatura fija arriba a la derecha
    embed.set_thumbnail(url="https://i.imgur.com/ibt9508.png")

    # Pie de página actualizado
    embed.set_footer(
        text="ClickSense RP • Sistema de notificaciones",
        icon_url="https://i.imgur.com/ibt9508.png"
    )
    return embed


def clave_categoria(categoria) -> str:
    clave = re.sub(r"[^a-z0-9_-]", "", str(categoria or "").strip().lower())[:32]
    return clave or "novedades"


def config_categoria(categoria) -> dict:
    return CATEGORIAS_NOTIF.get(clave_categoria(categoria), {})


def nombre_categoria(categoria) -> str:
    clave = clave_categoria(categoria)
    return config_categoria(clave).get("nombre") or clave.replace("-", " ").replace("_", " ").capitalize()


def construir_embed_notificaciones(categoria, activas: bool) -> discord.Embed:
    """Embed blanco que confirma que las notificaciones se han activado o desactivado."""
    estado = "Notificaciones activadas" if activas else "Notificaciones desactivadas"
    aviso = (
        "Puedes desactivar esta notificación en cualquier momento pulsando en el botón adjunto."
        if activas
        else "Puedes volver a activar esta notificación en cualquier momento pulsando en el botón adjunto."
    )
    lineas = [
        f"- **__CATEGORÍA__**: {nombre_categoria(categoria)}",
        f"- **__ESTADO__**: {estado}",
    ]
    connects = config_categoria(categoria).get("connect") or []
    if connects:
        # Entre comillas invertidas para que se pueda copiar tal cual desde Discord
        lineas.append("- **__CONNECT__**: " + " · ".join(f"`{c}`" for c in connects))

    descripcion = (
        "¡Buenas!, se ha registrado una actualización en las notificaciones del sistema:\n\n"
        + "\n".join(lineas) + "\n\n"
        "-# ¡ATENCIÓN! Esto es un mensaje automático procedente del sistema de "
        f"notificaciones de ClickSense. {aviso}"
    )
    return construir_embed("notificaciones", TITULO_NOTIF, descripcion, [])


def boton_notificaciones(categoria, activar: bool, modo: str) -> discord.ui.Button:
    """
    Botón para activar o desactivar las notificaciones desde Discord.
      modo "editar" -> al pulsarlo se actualiza ese mismo mensaje (el de confirmación)
      modo "nuevo"  -> al pulsarlo se envía la confirmación aparte (el de una novedad)
    """
    return discord.ui.Button(
        style=discord.ButtonStyle.success if activar else discord.ButtonStyle.secondary,
        label="Activar notificaciones" if activar else "Desactivar notificaciones",
        emoji="🔔" if activar else "🔕",
        custom_id=f"{PREFIJO_NOTIF}:{clave_categoria(categoria)}:{'on' if activar else 'off'}:{modo}",
    )


def vista_confirmacion(categoria, activas: bool) -> discord.ui.View:
    cfg = config_categoria(categoria)
    cambiar = boton_notificaciones(categoria, activar=not activas, modo="editar")

    # Botones de enlace: el panel del apartado y, si lo tiene, "Conectar a FiveM"
    enlaces = [discord.ui.Button(
        style=discord.ButtonStyle.link,
        label="Acceder al panel online",
        url=PUBLIC_URL + cfg.get("panel", "/home.html")
    )]
    if cfg.get("fivem_url"):
        enlaces.append(discord.ui.Button(
            style=discord.ButtonStyle.link,
            label="Conectar a FiveM",
            url=cfg["fivem_url"]
        ))

    view = discord.ui.View()
    for boton in (enlaces + [cambiar] if cfg.get("panel_primero") else [cambiar] + enlaces):
        view.add_item(boton)
    return view


async def manejar_notificacion(request: web.Request) -> web.Response:
    if request.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
        return web.json_response({"error": "no autorizado"}, status=401)

    datos = await request.json()
    discord_id = datos.get("discord_id")
    if not discord_id:
        return web.json_response({"error": "falta discord_id"}, status=400)

    try:
        usuario = await bot.fetch_user(int(discord_id))
        tipo = datos.get("tipo", "default")

        if tipo == "notificaciones":
            # Campanita de la web: confirmación con el botón contrario al estado actual
            activas = bool(datos.get("activas"))
            categoria = datos.get("categoria")
            embed = construir_embed_notificaciones(categoria, activas)
            view = vista_confirmacion(categoria, activas)
        else:
            embed = construir_embed(
                tipo=tipo,
                titulo=datos.get("titulo"),
                descripcion=datos.get("descripcion", ""),
                campos=datos.get("campos") or [],
            )

            # Botón de enlace inferior (texto y URL configurables desde el notifier)
            boton_texto = str(datos.get("button_text") or "Acceder al panel online")[:80]
            boton_url = str(datos.get("button_url") or "http://localhost:8000/home.html")
            if not boton_url.lower().startswith(("http://", "https://")):
                boton_url = "http://localhost:8000/home.html"

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                style=discord.ButtonStyle.link,
                label=boton_texto,
                url=boton_url
            ))

            # Mensajes que se reciben por tener una campanita activada (p. ej. novedades):
            # llevan además el botón para desactivarla desde el propio mensaje.
            if datos.get("notif_categoria"):
                view.add_item(boton_notificaciones(datos.get("notif_categoria"), activar=False, modo="nuevo"))

        await usuario.send(embed=embed, view=view)
        # "campanita": True le confirma al notifier que este bot ya es la versión con el embed blanco
        return web.json_response({"success": True, "campanita": True})

    except discord.Forbidden:
        print(f"[bot] Error 403: No se puede enviar DM a {discord_id}. DMs bloqueados o sin servidor común.")
        return web.json_response({"error": "dm_bloqueado", "success": False}, status=403)
    except (discord.NotFound, ValueError):
        print(f"[bot] Error 404: Usuario de Discord no encontrado ({discord_id}).")
        return web.json_response({"error": "usuario_no_encontrado", "success": False}, status=404)
    except Exception as error:
        print(f"[bot] Error interno enviando notificación: {error}")
        return web.json_response({"error": "error_interno", "success": False}, status=500)


async def guardar_preferencia_web(discord_id, categoria: str, activas: bool) -> bool:
    """Guarda la preferencia en la web (la misma tabla que usa la campanita de home.html)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as sesion:
            async with sesion.post(
                WEB_PREFS_URL,
                json={"discord_id": str(discord_id), "categoria": categoria, "activas": activas},
                headers={"X-Internal-Token": INTERNAL_TOKEN or ""},
            ) as respuesta:
                if respuesta.status != 200:
                    print(f"[bot] La web rechazó el cambio de notificaciones (Status {respuesta.status}).")
                    return False
                return True
    except Exception as error:
        print(f"[bot] No se pudo conectar con la web en {WEB_INTERNAL_URL}: {error}")
        return False


@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Botón 'Activar / Desactivar notificaciones' de los mensajes privados."""
    if interaction.type != discord.InteractionType.component:
        return
    partes = str((interaction.data or {}).get("custom_id") or "").split(":")
    if len(partes) != 4 or partes[0] != PREFIJO_NOTIF or partes[2] not in ("on", "off"):
        return

    categoria, activas, modo = partes[1], partes[2] == "on", partes[3]

    # Discord da 3 segundos para responder: se confirma la pulsación y luego se hace el trabajo
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except discord.InteractionResponded:
        pass
    except Exception as error:
        print(f"[bot] No se pudo responder al botón de notificaciones: {error}")
        return

    try:
        if not await guardar_preferencia_web(interaction.user.id, categoria, activas):
            await interaction.followup.send(
                "No se ha podido guardar el cambio. Inténtalo de nuevo o cámbialo desde la web.",
                ephemeral=True,
            )
            return

        embed = construir_embed_notificaciones(categoria, activas)
        view = vista_confirmacion(categoria, activas)
        if modo == "editar":
            await interaction.edit_original_response(embed=embed, view=view)   # se actualiza el mismo mensaje
        else:
            await interaction.followup.send(embed=embed, view=view)            # la novedad se queda como está
        print(f"[bot] Notificaciones de '{categoria}' {'activadas' if activas else 'desactivadas'} por {interaction.user.id} desde Discord.")
    except Exception as error:
        print(f"[bot] Error actualizando el mensaje de notificaciones: {error}")


async def iniciar_servidor_interno():
    app_web = web.Application()
    app_web.router.add_post("/notify", manejar_notificacion)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", INTERNAL_PORT)
    await site.start()
    print(f"[bot] Servidor interno escuchando en 127.0.0.1:{INTERNAL_PORT}")
    print("[bot] ✅ Sistema de notificaciones (campanita) cargado: embed blanco con botón de activar/desactivar.")


_servidor_iniciado = False


@bot.event
async def on_ready():
    global _servidor_iniciado
    print(f"[bot] Conectado como {bot.user} (ID: {bot.user.id})")

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name="ClickSense RP")
    )

    # Solo se arranca una vez, aunque el bot se reconecte
    if not _servidor_iniciado:
        _servidor_iniciado = True
        await iniciar_servidor_interno()


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Falta DISCORD_BOT_TOKEN en el .env")
    if not INTERNAL_TOKEN:
        raise SystemExit("Falta BOT_INTERNAL_TOKEN en el .env")
    bot.run(BOT_TOKEN)