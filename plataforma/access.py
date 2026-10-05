"""Cloudflare Access: la puerta que pide un código por correo ANTES de llegar al ERP.

Cuando alguien pasa por Cloudflare Access, Cloudflare le agrega a cada pedido una firma (un JWT en la
cabecera Cf-Access-Jwt-Assertion). Aquí se comprueba que esa firma sea de verdad de Cloudflare, de NUESTRO
equipo y de NUESTRA aplicación, y que no esté vencida. Sin esto, cualquiera podría entrar directo por la
dirección de Railway (*.up.railway.app) y saltarse la puerta.

Se activa con CF_ACCESS_ENFORCE=1 y necesita:
  CF_ACCESS_TEAM_DOMAIN   <equipo>.cloudflareaccess.com   (Zero Trust → Settings → Custom pages / Team domain)
  CF_ACCESS_AUD           el "Application Audience (AUD) Tag" de la aplicación en Access
En la Mac y en las pruebas queda apagado: no cambia nada.
"""
import os, re, json, time, threading
from urllib.request import urlopen

import jwt
from jwt.algorithms import RSAAlgorithm

_candado = threading.Lock()
_claves, _vencen = {}, 0.0


def activo():
    return os.environ.get("CF_ACCESS_ENFORCE") == "1"


def _equipo():
    t = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/").removeprefix("https://")
    if not re.fullmatch(r"[a-z0-9-]+\.cloudflareaccess\.com", t, re.I):   # solo dominios de Cloudflare: nunca se va a buscar claves a otro sitio
        raise ValueError("CF_ACCESS_TEAM_DOMAIN debe ser <equipo>.cloudflareaccess.com")
    return "https://" + t


def _cargar_claves(forzar=False):
    """Las claves públicas con que Cloudflare firma. Se piden una vez por hora (o antes, si llega una firma nueva)."""
    global _claves, _vencen
    if not forzar and _claves and time.monotonic() < _vencen: return _claves
    with _candado:
        if not forzar and _claves and time.monotonic() < _vencen: return _claves
        with urlopen(_equipo() + "/cdn-cgi/access/certs", timeout=5) as r:
            datos = json.loads(r.read())
        claves = {k["kid"]: RSAAlgorithm.from_jwk(json.dumps(k)) for k in datos.get("keys", [])
                  if k.get("kid") and k.get("kty") == "RSA" and k.get("use", "sig") == "sig"}
        if not claves: raise ValueError("Cloudflare no devolvió claves de firma")
        _claves, _vencen = claves, time.monotonic() + 3600
        return _claves


def validar(token):
    """Los datos firmados (correo de quien entró, etc.) si la firma es buena. Si no, lanza un error con el motivo."""
    if not token: raise ValueError("no trae la firma de Cloudflare (¿entró directo por la dirección de Railway?)")
    aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    if not aud: raise ValueError("falta CF_ACCESS_AUD")
    cab = jwt.get_unverified_header(token)
    if cab.get("alg") != "RS256" or not cab.get("kid"): raise ValueError("firma de un tipo que no es el de Cloudflare")
    clave = _cargar_claves().get(cab["kid"]) or _cargar_claves(forzar=True).get(cab["kid"])   # Cloudflare rota sus claves
    if clave is None: raise ValueError("la firma no es de este equipo de Cloudflare")
    return jwt.decode(token, clave, algorithms=["RS256"], audience=aud, issuer=_equipo(),
                      options={"require": ["iss", "aud", "exp"]}, leeway=5)
