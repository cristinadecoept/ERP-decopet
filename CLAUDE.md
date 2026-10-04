# Reglas para trabajar en el ERP Decopet

Este ERP tiene la operación real adentro. **Lo que llega a `main` se publica solo en producción**
(GitHub → pruebas → Railway). No hay un paso intermedio que lo frene, salvo las pruebas.

Contexto general del sistema: `README.md`. Quien pide los cambios es la dueña, que no es técnica:
explicarle en español simple qué cambió y qué va a notar, sin jerga.

## Antes de cada push a main

1. Correr las pruebas y que pasen **todas**: `python pruebas.py` (en Windows con `PYTHONIOENCODING=utf-8`).
   Si alguna falla, no se sube. No se borra ni se debilita una prueba para que pase.
2. Si el cambio toca una pantalla, abrirla y mirarla con el ERP corriendo localmente, en computadora y en
   teléfono (360–390 px de ancho), con cada rol que la ve.
3. Contarle a la dueña qué se va a publicar y esperar su visto bueno antes del push.

## La base de datos

- **Nunca** cambiar la estructura de una base a mano (ni la local, ni la del servidor). Un cambio hecho a mano
  no llega a ninguna otra base.
- Tabla o columna nueva: en `plataforma/modelo.sql` o en `COLUMNAS` (`plataforma/app.py`). Llega sola.
- Renombrar, quitar, mover datos o cargar valores iniciales: una **migración** nueva en
  `plataforma/migraciones/NNN_que_hace.sql`. Reglas en `plataforma/migraciones/LEEME.md`. Una migración que ya
  se aplicó no se edita: se agrega otra.
- **Los datos no viajan.** Lo que se anota en la copia local (franja naranja arriba) no llega a la operación.
  Los datos reales se cargan desde la web de producción.
- Bases, respaldos (`.backup`, `.bak`), fotos y documentos nunca entran a git.

## Lo que no se toca sin preguntar

- **Los permisos por rol.** El taller no ve dinero ni clientes; logística no ve dinero ni proveedores; cada
  despachador ve solo lo suyo. `taller` y `despachador` entran solo a las rutas de su lista: una página nueva
  no la ven salvo que se agregue a su lista a propósito.
- La puerta de Cloudflare (`plataforma/access.py`) y la seguridad del inicio de sesión.
- Las variables de Railway y los dominios.

## Cómo está escrito

- Todo en español: comentarios, nombres, textos. No pasar nada a inglés.
- Un solo `plataforma/app.py`, SQL a mano, plantillas Jinja2, sin framework de front ni build.
  No proponer React, Postgres, Docker nuevo ni ORM.
- **Menos texto en la pantalla, no más.** Sin subtítulos explicativos.
- Copiar el estilo de lo que ya existe: los mismos controles (`static/controles.js`), las mismas clases de
  `static/estilo.css`, guardar sin recargar como en las demás pantallas.
