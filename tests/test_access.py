import os
import time

os.environ["DECOPET_PRUEBAS"] = "1"


import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from plataforma import access, base_datos as BD
from plataforma.app import app

pytestmark = pytest.mark.skipif(not BD.usa_postgres(), reason="las pruebas HTTP usan PostgreSQL aislado")


@pytest.fixture
def client():
    with BD.esquema_prueba():
        with TestClient(app) as test_client:
            yield test_client


TEAM = "decopet-staging.cloudflareaccess.com"
AUD = "audiencia-staging-test"


def test_health_is_minimal_and_does_not_require_application_login(monkeypatch, client):
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("CF_ACCESS_ENFORCE", "1")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.text == "ok"
    assert "DATABASE_URL" not in response.text


def test_access_enforcement_fails_closed_without_jwt(monkeypatch, client):
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.delenv("CF_ACCESS_ENFORCE", raising=False)
    assert client.get("/health").status_code == 200
    assert client.get("/entrar").status_code == 403
    assert client.get("/static/estilo.css").status_code == 403
    assert client.get("/fotos/no-existe.jpg").status_code == 403


def test_access_enforcement_accepts_only_verified_jwt(monkeypatch, client):
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("CF_ACCESS_ENFORCE", "1")
    llave_privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    llave_publica = llave_privada.public_key()
    now = int(time.time())
    token = jwt.encode(
        {"iss": f"https://{TEAM}", "aud": [AUD], "sub": "staging-test", "iat": now, "exp": now + 60},
        llave_privada,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    monkeypatch.setattr(access, "_cargar_claves", lambda: {"test-kid": llave_publica})
    assert client.get("/entrar").status_code == 403
    response = client.get("/entrar", headers={"Cf-Access-Jwt-Assertion": token})
    assert response.status_code == 200
    assert "Ponle una clave a tu ERP" in response.text or "Correo o usuario" in response.text


@pytest.mark.parametrize("claim_overrides", [{"aud": "otra-audiencia"}, {"iss": "https://otro.cloudflareaccess.com"}])
def test_access_rejects_wrong_issuer_or_audience(monkeypatch, client, claim_overrides):
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("CF_ACCESS_ENFORCE", "1")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    claims = {"iss": f"https://{TEAM}", "aud": AUD, "sub": "staging-test", "exp": now + 60}
    claims.update(claim_overrides)
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-kid"})
    monkeypatch.setattr(access, "_cargar_claves", lambda: {"test-kid": private_key.public_key()})
    response = client.get("/entrar", headers={"Cf-Access-Jwt-Assertion": token})
    assert response.status_code == 403


def test_access_rejects_invalid_signature(monkeypatch, client):
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("CF_ACCESS_ENFORCE", "1")
    key_one = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_two = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = jwt.encode(
        {"iss": f"https://{TEAM}", "aud": AUD, "sub": "staging-test", "exp": now + 60},
        key_one,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )
    monkeypatch.setattr(access, "_cargar_claves", lambda: {"test-kid": key_two.public_key()})
    response = client.get("/entrar", headers={"Cf-Access-Jwt-Assertion": token})
    assert response.status_code == 403
