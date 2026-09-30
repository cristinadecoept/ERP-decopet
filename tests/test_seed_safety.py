import runpy
import sys
from pathlib import Path

import pytest

from plataforma import base_datos as BD


@pytest.mark.parametrize(
    "database_url,staging,confirmacion",
    [
        ("postgresql+psycopg://x:x@localhost/decopet", "1", "SEMBRAR DECOPET STAGING"),
        ("postgresql+psycopg://x:x@localhost/decopet_staging", "0", "SEMBRAR DECOPET STAGING"),
        ("postgresql+psycopg://x:x@localhost/decopet_staging", "1", ""),
    ],
)
def test_seed_refuses_non_staging_or_unconfirmed_database(monkeypatch, database_url, staging, confirmacion):
    monkeypatch.setattr(sys, "argv", ["semilla.py", "--vacia"])
    monkeypatch.setenv("DECOPET_DATABASE_URL", database_url)
    monkeypatch.setenv("DECOPET_STAGING", staging)
    monkeypatch.setenv("DECOPET_CONFIRMAR_SEMILLA", confirmacion)
    with pytest.raises(SystemExit):
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "plataforma" / "semilla.py"), run_name="__main__")
