"""Validación del JWT de Cloudflare Access en el origen de la app."""
import json
import os
import re
import threading
import time
from urllib.request import urlopen

import jwt
from jwt.algorithms import RSAAlgorithm


_candado = threading.Lock()
_claves = {}
_expiran = 0


def _team_domain():
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().rstrip("/")
    if not re.fullmatch(r"[a-z0-9-]+\.cloudflareaccess\.com", team, re.I):
        raise ValueError("CF_ACCESS_TEAM_DOMAIN debe ser <equipo>.cloudflareaccess.com")
    return f"https://{team}"


def _cargar_claves():
    global _claves, _expiran
    if time.monotonic() < _expiran and _claves:
        return _claves
    with _candado:
        if time.monotonic() < _expiran and _claves:
            return _claves
        with urlopen(f"{_team_domain()}/cdn-cgi/access/certs", timeout=5) as respuesta:
            datos = json.loads(respuesta.read())
        claves = {}
        for clave in datos.get("keys", []):
            if clave.get("kid") and clave.get("kty") == "RSA" and clave.get("use", "sig") == "sig":
                claves[clave["kid"]] = RSAAlgorithm.from_jwk(json.dumps(clave))
        if not claves:
            raise ValueError("Cloudflare Access no devolvió claves RSA de firma")
        _claves = claves
        _expiran = time.monotonic() + 3600
        return _claves


def validar(token):
    """Devuelve los claims verificados; nunca confía en payloads sin firma válida."""
    aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    if not aud:
        raise ValueError("Falta CF_ACCESS_AUD")
    cabecera = jwt.get_unverified_header(token)
    if cabecera.get("alg") != "RS256" or not cabecera.get("kid"):
        raise ValueError("Algoritmo o clave JWT inválidos")
    claves = _cargar_claves()
    clave = claves.get(cabecera["kid"])
    if clave is None:
        global _expiran
        _expiran = 0
        clave = _cargar_claves().get(cabecera["kid"])
    if clave is None:
        raise ValueError("La clave JWT no pertenece a este equipo de Cloudflare")
    return jwt.decode(
        token,
        clave,
        algorithms=["RS256"],
        audience=aud,
        issuer=_team_domain(),
        options={"require": ["iss", "aud", "exp", "sub"]},
        leeway=5,
    )
