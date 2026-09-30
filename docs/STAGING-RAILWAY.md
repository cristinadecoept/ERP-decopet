# Staging Decopet — Neon + Railway + Cloudflare Access

Esta es una instancia de **prueba con datos ficticios**. No cargar la SQLite real ni crear aquí usuarios de operación. El frontend son las plantillas Jinja2 y los estáticos servidos por FastAPI en el mismo servicio; no hay despliegue frontend separado.

## 1. Neon

1. Crear un proyecto/base PostgreSQL exclusivo de staging; usar un nombre que termine en `_staging` (la semilla lo comprueba).
2. Crear un rol limitado a esa base y guardar el DSN. No escribirlo en Git ni en archivos versionados.
3. En Railway, cargar el DSN como `DECOPET_DATABASE_URL` (o `DATABASE_URL`). El container ejecuta `alembic upgrade head` antes de Uvicorn; si falla la migración, no levanta el servicio.

## 2. Railway

Crear el servicio desde el repositorio y adjuntar un volumen persistente montado en `/data`. Configurar variables en el panel privado del servicio:

| Variable | Valor |
|---|---|
| `DECOPET_DATABASE_URL` | DSN Neon de la base de staging |
| `DECOPET_DATOS` | `/data` |
| `DECOPET_STAGING` | `1` |
| `CF_ACCESS_ENFORCE` | `1` |
| `CF_ACCESS_TEAM_DOMAIN` | `<equipo>.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | Audience tag de la aplicación Access de staging |

Usar una sola réplica durante staging. Railway debe comprobar `GET /health`; responde solo `ok`, sin datos de la base ni secretos. Esa sonda es la única ruta que no requiere JWT. Todas las otras rutas —incluidos `/entrar`, `/static` y `/fotos`— requieren `Cf-Access-Jwt-Assertion` válido. La URL directa `*.up.railway.app` **no es privada ni un secreto**: la aplicación valida el JWT también en ese origen.

No se usa Cloudflare Tunnel entre Railway y Cloudflare. Cloudflare Access debe proteger el hostname público completo y Railway publica el origen web; el middleware impide que el hostname directo de Railway evite Access.

## 3. Inicializar datos ficticios (acción manual y destructiva)

La semilla de PostgreSQL borra el contenido de **todas las tablas de la base conectada** y las vuelve a llenar. Solo se permite cuando:

- `DECOPET_STAGING=1`;
- el nombre de la base termina exactamente en `_staging`;
- `DECOPET_CONFIRMAR_SEMILLA=SEMBRAR DECOPET STAGING`.

Procedimiento controlado:

1. Verificar en el panel que `DECOPET_DATABASE_URL` apunta al proyecto Neon de staging (nunca a producción).
2. Hacer una rama/copia de Neon antes de inicializar.
3. Correr como una tarea puntual de Railway: `python plataforma/semilla.py --vacia` o `python plataforma/semilla.py` y definir `DECOPET_CONFIRMAR_SEMILLA` solo para esa ejecución.
4. Retirar `DECOPET_CONFIRMAR_SEMILLA` de inmediato. No dejarla como variable permanente del servicio.
5. Revisar el resumen de filas y entrar al staging. Las credenciales sembradas son ficticias; cambiarlas antes de compartir la URL.

**Nunca ejecutar la semilla contra la base real.** La validación del nombre y la confirmación son defensas adicionales, no reemplazan comprobar la URL de destino.

## 4. Cloudflare Access

1. Crear una aplicación Self-hosted para `erp-staging.shopdecopet.com`.
2. Crear política `Allow` con solo los correos que harán las pruebas.
3. Asociar el hostname a Railway y configurar HTTPS.
4. Copiar el **Audience tag** de esa aplicación en `CF_ACCESS_AUD`; copiar el dominio del equipo en `CF_ACCESS_TEAM_DOMAIN`.
5. Confirmar que la request al origen lleva `Cf-Access-Jwt-Assertion`.

El middleware verifica firma RS256 con las claves públicas del equipo de Cloudflare y exige `iss`, `aud`, `exp` y `sub`. Si falta el token, el `kid` no existe o la firma/claims no validan, responde `403` genérico. El login del ERP sigue como segundo factor de acceso.

## 5. Comprobación y rollback de staging

- `GET /health` debe dar 200 y el cuerpo `ok`.
- Sin Access o por la URL Railway directa sin JWT: `/entrar`, `/ordenes` y cualquier ruta funcional deben dar 403.
- Con correo permitido + JWT válido: debe aparecer el login propio del ERP.
- Verificar órdenes, operaciones, finanzas, producción, reglas de rol, galería y subida de documentos.
- Subir una foto y un documento demo; hacer un redeploy y comprobar que siguen en `/data`.
- Para revertir una release, redeployar la imagen anterior **contra la misma base staging**, siempre que la versión anterior sea compatible con el esquema actual. No bajar migraciones destructivas automáticamente.

La migración de datos reales y la elección del dominio productivo son un paso posterior, con copia de seguridad SQLite, importador y conciliación de conteos/saldos ensayados.