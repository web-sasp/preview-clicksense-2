/* =====================================================================
   comercios-sync.js · Comercios compartidos entre todos los usuarios

   Antes, admin/comercios.html guardaba los negocios solo en el navegador
   del staff (localStorage), así que nadie más los veía en comercios.html.
   Este archivo conecta las dos páginas con el servidor (/api/comercios):

     - En admin/comercios.html: cada vez que se crea, edita o borra un
       negocio, la lista se envía al servidor (las imágenes se reducen
       antes y el servidor las guarda como archivos).
     - En comercios.html: la lista se pide al servidor y se repinta sola.

   Se carga en las dos páginas con:
       <script src="/comercios-sync.js"></script>
   IMPORTANTE: tiene que ir DESPUÉS del <script> propio de la página
   (justo antes de </body>), porque usa sus funciones y variables.
   ===================================================================== */
/* global registeredBusinesses, renderActiveBusinesses, renderComerciosGrid, showNotification */
(function () {
    const CLAVE = 'registered_businesses_db';
    const LADO_MAX = 1280;          // las imágenes se reducen a este tamaño antes de subirlas
    const REFRESCO = 30000;         // comercios.html vuelve a consultar el servidor cada 30 s

    const esAdmin = typeof renderActiveBusinesses === 'function';      // admin/comercios.html
    const esPublica = typeof renderComerciosGrid === 'function';       // comercios.html
    if (!esAdmin && !esPublica) return;

    function leerLocal() {
        try {
            const d = JSON.parse(localStorage.getItem(CLAVE) || '[]');
            return Array.isArray(d) ? d : [];
        } catch (e) { return []; }
    }

    // Guarda la copia local (la que leen las páginas). Con las imágenes ya en el servidor ocupa muy poco.
    // Si no cabe, se conserva la copia que hubiera (antes se borraba primero y se podía perder).
    function escribirLocal(lista) {
        try {
            localStorage.setItem(CLAVE, JSON.stringify(lista));
            return true;
        } catch (e) {
            console.warn('[comercios] No se pudo guardar la copia local:', e);
            return false;
        }
    }

    async function pedirServidor() {
        try {
            const r = await fetch('/api/comercios', { cache: 'no-store', credentials: 'same-origin' });
            if (!r.ok) return null;
            const d = await r.json();
            return Array.isArray(d) ? d : null;
        } catch (e) {
            return null;            // null = no se ha podido consultar (no es lo mismo que lista vacía)
        }
    }

    // Reduce una imagen "data:..." y la convierte a JPG. Las que ya son enlaces se dejan igual.
    function reducirImagen(src) {
        if (typeof src !== 'string' || !src.startsWith('data:image/')) return Promise.resolve(src);
        return new Promise(resolve => {
            const img = new Image();
            img.onload = () => {
                try {
                    const escala = Math.min(1, LADO_MAX / Math.max(img.naturalWidth, img.naturalHeight));
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.max(1, Math.round(img.naturalWidth * escala));
                    canvas.height = Math.max(1, Math.round(img.naturalHeight * escala));
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = '#130d1e';
                    ctx.fillRect(0, 0, canvas.width, canvas.height);
                    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                    resolve(canvas.toDataURL('image/jpeg', 0.85));
                } catch (e) { resolve(src); }
            };
            img.onerror = () => resolve(src);
            img.src = src;
        });
    }

    async function prepararLista(lista) {
        const salida = [];
        for (const negocio of lista) {
            const imagenes = Array.isArray(negocio.imagenesData) ? negocio.imagenesData.slice(0, 3) : [];
            const reducidas = [];
            for (const img of imagenes) reducidas.push(await reducirImagen(img));
            salida.push(Object.assign({}, negocio, { imagenesData: reducidas }));
        }
        return salida;
    }

    // Envía la lista completa. Devuelve { ok, status, comercios }
    async function enviarServidor(lista) {
        try {
            const r = await fetch('/api/comercios', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(await prepararLista(lista))
            });
            let d = null;
            try { d = await r.json(); } catch (e) {}
            return { ok: r.ok, status: r.status, comercios: d && Array.isArray(d.comercios) ? d.comercios : null };
        } catch (e) {
            return { ok: false, status: 0, comercios: null };
        }
    }

    function motivo(status) {
        if (status === 401) return 'No hay sesión iniciada en el servidor. Vuelve a iniciar sesión con Discord.';
        if (status === 403) return 'Tu cuenta no tiene permisos de staff en el servidor.';
        if (status === 404 || status === 405) return 'El servidor no tiene la ruta /api/comercios (actualiza main.py y reinícialo).';
        if (status === 413) return 'Las imágenes pesan demasiado.';
        if (status === 0) return 'No se ha podido conectar con el servidor.';
        return 'El servidor ha respondido con un error (' + status + ').';
    }

    /* =================== admin/comercios.html =================== */
    if (esAdmin) {
        // Lista que tiene la página en memoria
        const listaPagina = () =>
            (typeof registeredBusinesses !== 'undefined' && Array.isArray(registeredBusinesses)) ? registeredBusinesses : [];

        // Cambia el contenido de la lista de la página SIN reasignar la variable.
        // Así funciona igual si en la página está declarada con "const" (reasignarla daba
        // "TypeError: Assignment to constant variable") que con "let".
        const ponerLista = (lista) => {
            if (typeof registeredBusinesses !== 'undefined' && Array.isArray(registeredBusinesses)) {
                if (registeredBusinesses !== lista) registeredBusinesses.splice(0, registeredBusinesses.length, ...lista);
            } else {
                registeredBusinesses = lista.slice();
            }
        };

        const guardar = async () => {
            try {
                const lista = listaPagina().slice();
                const res = await enviarServidor(lista);
                if (res.ok && res.comercios) {
                    // El servidor devuelve la lista con las imágenes ya convertidas en enlaces
                    ponerLista(res.comercios);
                    escribirLocal(res.comercios);
                    renderActiveBusinesses();
                } else {
                    escribirLocal(lista);          // al menos no se pierde en este navegador
                    console.error('[comercios] No se ha guardado en el servidor:', motivo(res.status));
                    if (typeof showNotification === 'function') {
                        setTimeout(() => showNotification('No se ha guardado en el servidor, así que los demás no lo verán. ' + motivo(res.status), false), 1500);
                    }
                }
            } catch (e) {
                console.error('[comercios] Error al guardar:', e);
            }
        };

        // La página llama a saveToLocalStorage() al crear, editar o borrar: ahora guarda en el servidor.
        // Los guardados se encadenan para que no se pisen entre sí.
        let guardando = Promise.resolve();
        window.saveToLocalStorage = function () {
            guardando = guardando.then(guardar, guardar);
        };

        const cargarAdmin = async () => {
            try {
                const servidor = await pedirServidor();
                if (servidor === null) return;             // sin conexión: se queda con la copia local
                const local = leerLocal();
                if (servidor.length === 0 && local.length > 0) {
                    // Primera vez: los negocios que ya estaban registrados en este navegador se suben al servidor
                    ponerLista(local);
                    window.saveToLocalStorage();
                    return;
                }
                ponerLista(servidor);
                escribirLocal(servidor);
                renderActiveBusinesses();
            } catch (e) {
                console.error('[comercios] Error al cargar:', e);
            }
        };

        // Se espera un momento a que la página termine su propia carga (permisos de staff incluidos)
        const arrancar = () => setTimeout(cargarAdmin, 600);
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
        else arrancar();
        return;
    }

    /* =================== comercios.html =================== */
    let ultima = null;
    let subidaIntentada = false;    // la subida de negocios antiguos se intenta una sola vez por visita
    let subidaDudosa = false;       // no se pudo saber si es staff (sin sesión o sin conexión)
    let cargando = false;           // evita dos consultas a la vez (intervalo + volver a la pestaña)

    async function cargarPublica() {
        if (cargando) return;
        cargando = true;
        try {
            let servidor = await pedirServidor();
            if (servidor === null) return;                 // sin conexión: se queda con lo que hubiera

            // Navegador del staff con negocios antiguos que aún no están en el servidor: se intentan subir.
            // Solo una vez: antes se repetía cada 30 s y llenaba la consola de errores 401 en rojo.
            const local = leerLocal();
            if (servidor.length === 0 && local.length > 0) {
                if (subidaIntentada) {
                    if (subidaDudosa) return;              // sigue sin saberse: no se toca nada
                } else {
                    subidaIntentada = true;
                    const res = await enviarServidor(local);
                    if (res.ok && res.comercios) servidor = res.comercios;
                    else if (res.status === 401 || res.status === 0) { subidaDudosa = true; return; }   // no se sabe: no se toca nada
                    // 403 = no es staff: lo que tenía guardado era una copia antigua, manda el servidor
                }
            }

            const firma = JSON.stringify(servidor);
            if (firma === ultima) return;                  // nada nuevo: no se repinta
            ultima = firma;
            escribirLocal(servidor);
            renderComerciosGrid();
        } catch (e) {
            console.error('[comercios] Error al cargar:', e);
        } finally {
            cargando = false;
        }
    }

    const arrancarPublica = () => {
        cargarPublica();
        setInterval(cargarPublica, REFRESCO);
        window.addEventListener('focus', cargarPublica);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancarPublica);
    else arrancarPublica();
})();