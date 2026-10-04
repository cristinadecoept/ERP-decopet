"""La lista de correos que Cloudflare deja pasar, manejada desde la pantalla Equipo del ERP.

En Cloudflare hay un grupo de Access ("Equipo Decopet") y la regla de la aplicación deja entrar a ese grupo.
El ERP reescribe el grupo entero con los correos de quienes tienen acceso: siempre la lista completa, así
si un cambio no llegó (Cloudflare caído), el siguiente lo arregla solo.

Necesita, en las variables del servidor:
  CF_API_TOKEN          una llave de Cloudflare con permisos "Access: Organizations, Identity Providers, and Groups"
                        Edit (cambiar el grupo) y Revoke (cerrarle la sesión a alguien)
  CF_ACCOUNT_ID         el número de cuenta de Cloudflare
  CF_ACCESS_GROUP_ID    el grupo "Equipo Decopet"
Sin ellas (la Mac, las pruebas) no hace nada.
"""
import os, json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API = "https://api.cloudflare.com/client/v4"


def configurado():
    return all(os.environ.get(k) for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_ACCESS_GROUP_ID"))


def _pedir(metodo, ruta, cuerpo=None):
    req = Request(f"{API}/accounts/{os.environ['CF_ACCOUNT_ID']}{ruta}", method=metodo,
                  data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                  headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as r: datos = json.loads(r.read())
    except HTTPError as e:   # Cloudflare explica el motivo en el cuerpo
        try: datos = json.loads(e.read())
        except ValueError: raise RuntimeError(f"Cloudflare respondió {e.code}") from None
    if not datos.get("success"):
        raise RuntimeError("; ".join(x.get("message", "") for x in datos.get("errors") or []) or "Cloudflare no aceptó el cambio")
    return datos.get("result")


def poner_correos(correos):
    """El grupo queda exactamente con estos correos. Se conserva su nombre y lo que excluya o exija."""
    if not correos: raise ValueError("el grupo no puede quedar vacío: nadie podría entrar")
    ruta = f"/access/groups/{os.environ['CF_ACCESS_GROUP_ID']}"
    g = _pedir("GET", ruta)
    _pedir("PUT", ruta, {"name": g["name"], "include": [{"email": {"email": c}} for c in sorted(correos)],
                         "exclude": g.get("exclude") or [], "require": g.get("require") or []})


def cerrar_sesion(correo):
    """Cloudflare le vuelve a pedir el código por correo la próxima vez que abra el ERP."""
    _pedir("POST", "/access/organizations/revoke_user", {"email": correo})
