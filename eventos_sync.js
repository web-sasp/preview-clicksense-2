(function () {
    'use strict';
    if (window.__eventosSync) return;
    window.__eventosSync = true;
 
    var CLAVE = 'calendar_events';
    var API = '/api/eventos';
    var script = document.currentScript;
    var recargarSiCambia = !!(script && script.hasAttribute('data-recargar'));
    var setOriginal = Storage.prototype.setItem;
 
    var temporizador = null;
    var pendiente = null;      // texto JSON que la página ha guardado y aún no se ha subido
 
    function leerLocal() {
        try { return window.localStorage.getItem(CLAVE); } catch (e) { return null; }
    }
    function aObjeto(texto) {
        try {
            var o = JSON.parse(texto);
            return (o && typeof o === 'object' && !Array.isArray(o)) ? o : null;
        } catch (e) { return null; }
    }
    function tieneEventos(o) {
        return !!o && Object.keys(o).some(function (k) { return Array.isArray(o[k]) && o[k].length > 0; });
    }
 
    // Aviso discreto abajo en el centro (solo aparece si un guardado falla)
    function aviso(mensaje) {
        var caja = document.getElementById('eventosSyncAviso');
        if (!caja) {
            caja = document.createElement('div');
            caja.id = 'eventosSyncAviso';
            caja.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483647;' +
                'max-width:90%;padding:12px 20px;border-radius:12px;background:#1a0f14;color:#fff;' +
                'border:1px solid rgba(239,68,68,.6);box-shadow:0 10px 30px rgba(0,0,0,.6);' +
                'font:500 13.5px "Plus Jakarta Sans",sans-serif;text-align:center;';
            (document.body || document.documentElement).appendChild(caja);
        }
        caja.textContent = mensaje;
        caja.style.display = 'block';
        clearTimeout(caja._t);
        caja._t = setTimeout(function () { caja.style.display = 'none'; }, 7000);
        console.warn('[eventos] ' + mensaje);
    }
 
    // Sube todos los eventos al servidor. silencioso = no avisar si falla (primera subida automática)
    function subir(texto, silencioso, alSalir) {
        var cuerpo = JSON.stringify(aObjeto(texto) || {});
        var opciones = {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: cuerpo
        };
        if (alSalir && cuerpo.length < 60000) opciones.keepalive = true;   // para que llegue aunque se cierre la página
 
        return fetch(API, opciones).then(function (r) {
            if (r.ok || silencioso) return r.ok;
            if (r.status === 401) aviso('Los eventos NO se han guardado en el servidor: tu sesión ha caducado. Inicia sesión de nuevo con Discord.');
            else if (r.status === 403) aviso('Los eventos NO se han guardado en el servidor: tu cuenta no tiene permisos de staff.');
            else aviso('Los eventos NO se han guardado en el servidor (error ' + r.status + ').');
            return false;
        }).catch(function () {
            if (!silencioso) aviso('Los eventos NO se han guardado en el servidor: no hay conexión.');
            return false;
        });
    }
 
    function programarSubida(texto) {
        pendiente = texto;
        clearTimeout(temporizador);
        temporizador = setTimeout(function () {
            var t = pendiente;
            pendiente = null;
            subir(t, false, false);
        }, 400);
    }
 
    // Cada vez que la página guarda los eventos en localStorage, se suben al servidor
    Storage.prototype.setItem = function (clave, valor) {
        setOriginal.apply(this, arguments);
        try {
            if (this === window.localStorage && clave === CLAVE) programarSubida(String(valor));
        } catch (e) {}
    };
 
    // Si se sale de la página con un guardado a medias, se envía en ese momento
    window.addEventListener('pagehide', function () {
        if (pendiente === null) return;
        clearTimeout(temporizador);
        var t = pendiente;
        pendiente = null;
        subir(t, true, true);
    });
 
    // Baja los eventos del servidor y los deja en localStorage
    function bajar() {
        return fetch(API, { cache: 'no-store', credentials: 'same-origin' }).then(function (r) {
            if (!r.ok) return null;
            var sinIniciar = r.headers.get('X-Eventos-Sin-Iniciar') === '1';
            return r.json().then(function (servidor) { return { servidor: servidor, sinIniciar: sinIniciar }; });
        }).then(function (res) {
            if (!res || !res.servidor || typeof res.servidor !== 'object' || Array.isArray(res.servidor)) return;
            if (pendiente !== null) return;      // hay un cambio local sin subir: manda lo local
 
            var localTexto = leerLocal();
            var local = localTexto ? aObjeto(localTexto) : null;
 
            if (res.sinIniciar) {
                // El servidor aún no ha guardado nunca eventos: no se toca lo que haya en este
                // navegador y, si hay eventos, se intentan subir (solo lo consigue el staff).
                if (tieneEventos(local)) subir(localTexto, true, false);
                return;
            }
 
            var nuevoTexto = JSON.stringify(res.servidor);
            if (local && JSON.stringify(local) === nuevoTexto) return;      // ya está al día
 
            setOriginal.call(window.localStorage, CLAVE, nuevoTexto);       // sin volver a subirlo
 
            if (recargarSiCambia) {
                // Como mucho una recarga cada 10 segundos, por si acaso
                var ultima = 0;
                try { ultima = Number(window.sessionStorage.getItem('eventos_sync_recarga')) || 0; } catch (e) {}
                if (Date.now() - ultima > 10000) {
                    try { window.sessionStorage.setItem('eventos_sync_recarga', String(Date.now())); } catch (e) {}
                    window.location.reload();
                }
                return;
            }
            // Las páginas ya escuchan este evento para volver a pintar (calendario.html y home.html)
            try { window.dispatchEvent(new StorageEvent('storage', { key: CLAVE, newValue: nuevoTexto })); } catch (e) {}
        }).catch(function () {});
    }
 
    bajar();
    // Al volver a la pestaña se comprueba si hay eventos nuevos (en la página de admin no,
    // para no recargarla mientras se está escribiendo un evento)
    if (!recargarSiCambia) window.addEventListener('focus', bajar);
})();

<script src="/guards.js"></script>