# ERP Decopet — resumen técnico

Documento para un desarrollador que vaya a colaborar. Estado a septiembre 2026.

---

## Qué es

ERP interno de Decopet Mascotas C.A. (Venezuela). Reemplaza un Airtable.
Cubre órdenes, clientes, inventario, producción (taller), despachos, cobranza,
flujo de caja, nómina y gastos recurrentes.

Es de uso interno: ~8 usuarios, ~7 órdenes/día. No es una app pública.

## Stack

| | |
|---|---|
| Lenguaje | Python 3.9 |
| Framework | FastAPI 0.128 + Uvicorn |
| Plantillas | Jinja2 (server-side rendering, sin SPA) |
| Base de datos | SQLite |
| Front | HTML + CSS propio, JS mínimo inline. Sin build step, sin npm. |

Decisión de diseño deliberada: **cero infraestructura opcional**. Sin Redis, sin
Celery, sin Docker, sin ORM, sin bundler. La dueña no es técnica y el sistema
tiene que poder arreglarse leyendo un archivo.

## Forma del código

```
plataforma/
  app.py            4.663 líneas · 142 rutas · toda la lógica
  modelo.sql        esquema base
  templates/        44 plantillas Jinja2
  static/
  data/             DB, fotos, documentos  (fuera de git)
servidor/           instalar.sh, publicar.sh, unidades systemd
scripts/respaldo.sh
pruebas.py          21 pruebas
requirements.txt
```

`app.py` es un solo archivo a propósito. Es grande pero es lineal y está
comentado en español orientado a la dueña, no al desarrollador — los comentarios
explican *por qué* una regla de negocio es así, que es la información que no
está en ningún otro lado.

SQL a mano con `sqlite3` de la stdlib. No hay ORM.

## Base de datos

41 tablas. Núcleo: `ordenes`, `orden_items`, `clientes`, `productos`,
`inventario`, `movimientos`, `cajas`, `gastos`, `usuarios`, `sesiones`,
`credito_cliente`, `despachos`, `produccion`, `recurrentes`.

**Migraciones:** no hay Alembic. `preparar_base()` corre `modelo.sql`
(idempotente, `CREATE TABLE IF NOT EXISTS`) y luego aplica una tabla de
constantes `COLUMNAS` con ~54 tuplas `(tabla, columna, tipo)` vía
`ALTER TABLE ... ADD COLUMN` si faltan. Se ejecuta en cada arranque.

Hay una prueba que **reconstruye la base desde cero y verifica que quede idéntica
a la que está en uso**. Es la que impide que el esquema se desincronice.

## Autenticación y autorización

**Claves:** `pbkdf2_hmac('sha256', ..., 200_000)` con sal de 16 bytes.
Se guarda `sal$hash`. Comparación con `secrets.compare_digest`.

**Sesiones:** tabla `sesiones`, token aleatorio, 12 h. Cookie `httponly` +
`samesite=strict`.

**Fuerza bruta:** tabla `intentos`. 8 fallos por usuario/IP en 15 min → bloqueo.

**CSRF:** validación de `Origin`/`Referer` en el middleware para POST/PUT/DELETE.

**Autorización:** un único middleware `puerta` que gatea *todo*. Dos capas:

```python
PERMISOS = {rol: {acciones permitidas}}      # qué puede hacer
PUERTAS  = {rol: (prefijos permitidos, home)} # qué puede ver
```

Roles: `admin`, `logistica`, `taller`, `despachador`, `sistema`, `invitado`.

Las restricciones de visibilidad son **requisitos de negocio reales, no cosmética**:

- `taller` (2 empleados) no puede ver dinero, ventas, ni datos de clientes.
  Solo nombre de pila + qué se llevan. Ve un único proveedor.
- `logistica` no ve dinero ni nombres de proveedores.
- `despachador` **sí ve dinero, pero solo el suyo** — lo que se le debe por sus
  entregas. Nunca el de la empresa ni el de otro despachador.

Esto está en el middleware, no en las plantillas. Si se agrega una ruta nueva
queda cubierta por defecto.

**Auditoría:** `uid_de(request)` resuelve la persona concreta, no el rol. El
historial dice "Isaías", no "taller".

## Pruebas

`pruebas.py` — 21 pruebas, **sin pytest**, sin dependencias. Cada una arma su
propia DB temporal con `preparar_base()`. Se corre con
`./.venv/bin/python pruebas.py` y da salida en español.

Cubren sobre todo reglas de plata: fechas de quincena, saldo a favor de clientes,
deuda a despachadores, cuadre de cajas, parseo de números con coma y punto, y
que editar un registro parcial no borre los campos ausentes.

`publicar.sh` las corre antes de desplegar y aborta si alguna falla.

## Despliegue

**Servidor:** Hetzner CPX21 (3 vCPU / 4 GB / 80 GB), Ubuntu 24.04, Ashburn.

**Red — esto es lo más importante del diseño:**

```
internet → Cloudflare Access → Cloudflare Tunnel → 127.0.0.1:8765
```

El servidor **no expone ningún puerto web**. `cloudflared` establece una conexión
saliente; no hay nada escuchando en 80/443. `ufw` deniega todo el tráfico entrante
excepto SSH. SSH es solo por llave (`PasswordAuthentication no`, `PermitRootLogin no`),
con fail2ban.

Delante va **Cloudflare Access** con política por correo. Un visitante no
autenticado nunca llega a ver la pantalla de login de la app.

No hay Caddy ni nginx. Las cabeceras de seguridad (`X-Frame-Options`, `nosniff`,
`X-Robots-Tag`, `Referrer-Policy`, `Cache-Control: no-store`) y el límite de
subida (25 MB, por `Content-Length`) los pone el propio middleware.

**Proceso:** systemd (`decopet.service`), uvicorn en 127.0.0.1:8765 con
`--proxy-headers --forwarded-allow-ips=127.0.0.1`, `Restart=always`.

**Datos fuera del código:** `DECOPET_DATOS` y `DECOPET_RESPALDOS`. El programa es
desechable; los datos no.

**Despliegue:** `git push` a un repo bare en el servidor + `systemctl restart`.
Lo hace `servidor/publicar.sh` desde la Mac. Exige árbol limpio y pruebas en verde.

**Respaldos:** timer de systemd diario. Respalda 4 cosas: DB en uso, DB histórica
de Airtable, programa + git, y fotos/documentos. Más los snapshots de Hetzner.

## Datos

| | |
|---|---|
| DB en uso | ~250 KB (recién vaciada para arrancar limpio) |
| Histórico Airtable | 2,8 MB · 5.285 clientes · 7.794 pedidos |
| Fotos de producto | 83 MB · 118 archivos |
| Proyección a 3 años | ~4 GB todo incluido |

Volumen real: unas 2.600 órdenes/año. **SQLite sobra** y va a seguir sobrando.
No hace falta Postgres; sugerirlo sería complejidad sin beneficio.

## Lo que está pendiente

1. **Shopify.** Decidir entre *polling* cada 2 min (sin exponer nada) o webhook
   con excepción de ruta en Access + verificación HMAC. Inclinación: polling, por
   volumen y por simplicidad.
2. **Bot Tina (proveedor KAI).** Entra hacia el ERP, así que necesita
   autenticación máquina-a-máquina. Plan: *service token* de Cloudflare Access
   (`CF-Access-Client-Id` / `CF-Access-Client-Secret`) + su propio usuario con
   permisos acotados. **Bloqueado hasta confirmar con KAI si su bot puede enviar
   cabeceras HTTP personalizadas.** Si no puede, hay que caer a bypass de ruta +
   firma.
3. **Migración de NS** de `shopdecopet.com` de Google a Cloudflare. Requisito
   para el túnel. Los registros actuales están inventariados; hay que verificar la
   copia antes de cambiar. Riesgo real: MX (Google Workspace) y SPF.
4. **DMARC** no existe hoy. Agregar al migrar.
5. Faltan claves para 9 usuarios. Migración del histórico de ventas desde Airtable.

## Cosas que conviene saber antes de tocar

- **El español de los comentarios y los nombres es intencional.** La dueña lee el
  código. No renombrar a inglés.
- **Menos texto en la UI, no más.** Hay una preferencia fuerte y repetida por
  quitar subtítulos explicativos. No agregar ayuda contextual sin pedirla.
- **Las reglas de visibilidad por rol no son negociables** y vienen de decisiones
  de negocio concretas. Antes de "simplificar" esa parte, preguntar.
- **El repositorio es local, sin remoto**, por decisión de privacidad.
- No proponer Docker/Postgres/React sin una razón fuerte. La restricción de
  operabilidad por una persona no técnica es dura.
