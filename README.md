# ERP Decopet

ERP interno de **Decopet Mascotas C.A.** Reemplaza el Airtable con el que se
llevaba el negocio.

Cubre órdenes, clientes, inventario, producción del taller, despachos, cobranza,
flujo de caja, nómina y gastos recurrentes. Lo usan 8 personas con permisos
distintos.

No es un prototipo: tiene la operación real adentro.

---

## Arrancarlo

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn plataforma.app:app --host 127.0.0.1 --port 8765
```

Y en el navegador: `http://127.0.0.1:8765`

En la Mac de Cristina hay un `iniciar.command` que hace lo mismo con doble clic.

La base se crea sola en `plataforma/data/plataforma.db` si no existe. Para
probar con datos ficticios: `./.venv/bin/python plataforma/semilla.py`

## Las pruebas

```bash
./.venv/bin/python pruebas.py
```

21 pruebas, sin pytest y sin dependencias. Cada una arma su propia base
temporal. Cubren sobre todo **reglas de plata**: fechas de quincena, saldo a
favor de clientes, deuda a despachadores, cuadre de cajas, parseo de números
con coma y punto, y que editar un registro parcial no borre campos ausentes.

Hay una que reconstruye la base desde cero y verifica que quede idéntica a la
que está en uso. Es la que impide que el esquema se desincronice.

`servidor/publicar.sh` las corre antes de desplegar y aborta si alguna falla.

## Cómo está hecho

| | |
|---|---|
| Python 3.9 + FastAPI, servido con Uvicorn | |
| SQLite, SQL a mano, sin ORM | |
| Jinja2, renderizado en el servidor | |
| HTML y CSS propio, JS mínimo inline | |
| Sin framework de front, sin build step, sin npm, sin Docker | |

**Es deliberado.** Cero infraestructura opcional: la dueña no es técnica y el
sistema tiene que poder arreglarse leyendo un archivo. Con ~2.600 órdenes al
año, SQLite sobra y va a seguir sobrando.

```
plataforma/app.py        toda la lógica · 4.663 líneas · 142 rutas
plataforma/modelo.sql    esquema
plataforma/templates/    44 plantillas
plataforma/data/         base, fotos, documentos  (fuera de git)
servidor/                instalar.sh, publicar.sh, unidades systemd
scripts/                 respaldo, importación del histórico, gancho de git
pruebas.py
```

`app.py` es un solo archivo a propósito. Es grande pero lineal, y los
comentarios están en español explicando **por qué** una regla de negocio es
así — esa información no está en ningún otro lado.

## Migraciones

No hay Alembic. En cada arranque, `preparar_base()` hace tres cosas, en orden:

1. Corre `modelo.sql` (idempotente): tablas nuevas.
2. Aplica `COLUMNAS`, tuplas `(tabla, columna, tipo)` vía `ALTER TABLE ADD COLUMN` si faltan: columnas nuevas.
3. Aplica las **migraciones** de `plataforma/migraciones/NNN_que_hace.sql` que falten, una sola vez cada una
   y en orden (la tabla `migraciones` anota cuáles ya se hicieron). Son para lo que 1 y 2 no hacen: renombrar,
   mover datos o cargar valores iniciales. Si una falla, no queda nada a medias y el ERP no arranca: en el
   servidor sigue la versión anterior. Reglas en `plataforma/migraciones/LEEME.md`.

Así, un cambio de estructura hecho en el código llega solo a producción al desplegar. **Un cambio hecho a mano
en una base no llega a ninguna otra**: la prueba "Una base nueva queda igual que la que está en uso" lo detecta.

**Los datos no viajan entre bases.** Lo que se anota en la copia local (la Mac, la PC) no llega a la operación.
Por eso la copia local y la versión de prueba muestran una franja arriba; en producción no sale nada.

## Permisos

Dos capas independientes. **`PERMISOS`** dice qué puedes hacer; **`PUERTAS`**
dice dónde puedes entrar.

| Rol | Quién | Puede hacer |
|---|---|---|
| `admin` | Cristina | todo, incluido `ver_dinero` |
| `logistica` | Operaciones | crear · coordinar · entregar · editar entrega · incidencia · reprogramar |
| `taller` | Isaías, Manawa | solo `taller` |
| `despachador` | Ingrid, Fernando, Juan | entregar · mis entregas · incidencia |
| `sistema` | Tina (bot) | nada todavía |

`taller` y `despachador` están cerrados por lista blanca de rutas: una página
nueva **no la ven por defecto**. `admin`, `logistica` y `sistema` no tienen esa
lista — los frenan los `solo_admin` en las rutas de dinero y los
`'ver_dinero' in puede` en las plantillas.

Las restricciones son **requisitos de negocio reales, no cosmética**: el taller
no ve dinero ni ventas ni datos de clientes; logística no ve dinero ni
proveedores; el despachador sí ve dinero, pero solo el suyo.

## Seguridad

- Claves con `pbkdf2_hmac` sha256, 200.000 iteraciones, sal de 16 bytes
- Sesiones en base, 12 h, cookie `httponly` + `samesite=strict`
- 8 intentos fallidos en 15 min y se bloquea
- CSRF por validación de `Origin`/`Referer`
- Cabeceras de seguridad y límite de subida en el middleware

El gancho `scripts/proteger-datos.sh` impide que bases de datos, fotos,
documentos o llaves entren al historial. Instalarlo tras clonar:

```bash
ln -sf ../../scripts/proteger-datos.sh .git/hooks/pre-commit
```

## Dónde vive

**Hoy:** en la Mac de Cristina, en `127.0.0.1:8765`, arrancado a mano. Nada
publicado, sin dominio, sin arranque automático.

**A dónde va** (programado y probado, sin desplegar): Hetzner + Ubuntu, sin
puertos web expuestos — Cloudflare Tunnel hacia afuera, `ufw` denegando todo el
entrante salvo SSH por llave, y Cloudflare Access por delante. systemd para el
proceso y para el respaldo diario. Despliegue por `git push` a un repo bare +
`systemctl restart`.

Detalle completo en [`servidor/LEEME.md`](servidor/LEEME.md).

## Antes de tocar

- **El español de los comentarios y los nombres es intencional.** La dueña lee
  el código. No renombrar a inglés.
- **Menos texto en la UI, no más.** Hay una preferencia fuerte y repetida por
  quitar subtítulos explicativos.
- **Las reglas de visibilidad por rol no son negociables** sin preguntar.
- No proponer Docker / Postgres / React sin una razón fuerte. La restricción de
  que lo pueda operar una persona no técnica es dura.

Más contexto: [`RESUMEN-TECNICO.md`](RESUMEN-TECNICO.md) y
[`contexto-para-el-programador.txt`](contexto-para-el-programador.txt).

---

Repositorio privado. El código es de Decopet Mascotas C.A.
