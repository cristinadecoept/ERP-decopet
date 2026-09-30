from unittest.mock import patch

import pytest

from plataforma import base_datos as BD
from plataforma.app import demo_publica_activa, rol_de, uid_de, ventas_por_dia


def test_public_demo_requires_staging_postgres_and_explicit_flag(monkeypatch):
    monkeypatch.setenv("DECOPET_PUBLIC_DEMO", "1")
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("DECOPET_DATABASE_URL", "postgresql+psycopg://x:x@localhost/db_staging")
    assert demo_publica_activa()

    monkeypatch.setenv("DECOPET_DATABASE_URL", "postgresql+psycopg://x:x@localhost/production")
    assert not demo_publica_activa()

    monkeypatch.setenv("DECOPET_DATABASE_URL", "sqlite:///db_staging")
    assert not demo_publica_activa()

    monkeypatch.delenv("DECOPET_PUBLIC_DEMO")
    monkeypatch.setenv("DECOPET_DATABASE_URL", "postgresql+psycopg://x:x@localhost/db_staging")
    assert not demo_publica_activa()


def test_public_demo_uses_admin_demo_identity_only_in_staging(monkeypatch):
    monkeypatch.setenv("DECOPET_PUBLIC_DEMO", "1")
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("DECOPET_DATABASE_URL", "postgresql+psycopg://x:x@localhost/db_staging")
    request = type("Request", (), {"cookies": {}})()
    assert rol_de(request) == "admin"
    assert uid_de(request) == 1


@pytest.mark.skipif(not BD.usa_postgres(), reason="regresión de agregación necesita PostgreSQL")
def test_ventas_por_dia_reads_postgresql_rows_and_extras():
    with BD.esquema_prueba() as esquema:
        con = BD.conectar()
        con.execute("INSERT INTO clientes (id,nombre_pila,nombre) VALUES (1,'Demo','Demo')")
        con.execute("INSERT INTO ordenes (id,numero,cliente_id,estado,total,creado_en) VALUES (1,'#1',1,'pendiente',30,'2026-09-30 10:00:00')")
        con.execute("INSERT INTO orden_lineas (orden_id,nombre,cantidad,precio,total,extra_en) VALUES (1,'Delivery',1,5,5,'2026-09-30')")
        con.commit()
        assert ventas_por_dia(con, "2026-09-30", "2026-09-30")["2026-09-30"] == (30, 1)
        con.close()


def test_demo_flag_does_not_bypass_access_on_production_database(monkeypatch):
    monkeypatch.setenv("DECOPET_PUBLIC_DEMO", "1")
    monkeypatch.setenv("DECOPET_STAGING", "1")
    monkeypatch.setenv("DECOPET_DATABASE_URL", "postgresql+psycopg://x:x@localhost/production")
    request = type("Request", (), {"cookies": {}})()
    assert not demo_publica_activa()
    with patch("plataforma.app.quien_es", return_value=None), patch("plataforma.app.hay_claves", return_value=True):
        assert rol_de(request) == "invitado"
        assert uid_de(request) == 1
