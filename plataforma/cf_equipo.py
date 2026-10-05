"""La lista de correos que Cloudflare deja pasar, manejada desde la pantalla Equipo del ERP.

En Cloudflare, la aplicación del ERP tiene una regla (policy) que deja pasar a una lista de correos. El ERP
reescribe esa lista entera con los correos de quienes tienen acceso: siempre la lista completa, así si un cambio
no llegó (Cloudflare caído), el siguiente lo arregla solo.

Necesita, en las variables del servidor:
  CF_API_TOKEN          una llave de Cloudflare con permisos "Access: Apps and Policies" Edit (cambiar la regla)
                        y "Access: Organizations, Identity Providers, and Groups" Revoke (cerrarle la sesión a alguien)
  CF_ACCOUNT_ID         el número de cuenta de Cloudflare
  CF_ACCESS_POLICY_ID   la regla de la aplicación (Access controls → Policies → la del ERP; está en su dirección)
Sin ellas (la Mac, las pruebas) no hace nada.
"""
import os, json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API = "https://api.cloudflare.com/client/v4"
SOLO_LECTURA = ("id", "created_at", "updated_at", "app_count", "reusable", "precedence")   # Cloudflare los pone, no se mandan


def configurado():
    return all(os.environ.get(k) for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_ACCESS_POLICY_ID"))


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
    """La regla deja pasar exactamente a estos correos. Lo demás de la regla (nombre, duración de la sesión,
    lo que excluya o exija) queda como esté en Cloudflare."""
    if not correos: raise ValueError("la regla no puede quedar vacía: nadie podría entrar")
    ruta = f"/access/policies/{os.environ['CF_ACCESS_POLICY_ID']}"
    regla = {k: v for k, v in _pedir("GET", ruta).items() if k not in SOLO_LECTURA}
    regla["include"] = [{"email": {"email": c}} for c in sorted(correos)]
    _pedir("PUT", ruta, regla)


def cerrar_sesion(correo):
    """Cloudflare le vuelve a pedir el código por correo la próxima vez que abra el ERP."""
    _pedir("POST", "/access/organizations/revoke_user", {"email": correo})
