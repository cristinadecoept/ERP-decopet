"""Asegura que las secuencias serial existentes sigan el último id insertado.

Revisión: 0002_ids_autogenerados
Anterior: 0001_esquema_inicial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_ids_autogenerados"
down_revision = "0001_esquema_inicial"
branch_labels = None
depends_on = None

TABLAS = (
    "clientes", "cuentas", "despachadores", "faltas", "intentos", "notas_taller",
    "pagos_despachador", "productos", "proveedores", "registro_ventas", "tarifas",
    "tasas", "usuarios", "compromisos", "direcciones", "mascotas", "movimientos",
    "notas_cliente", "ordenes", "produccion", "producto_fotos", "proveedor_items",
    "seguimientos", "viajes_agencia", "abonos_produccion", "credito_cliente", "fotos",
    "gastos", "historial", "incidencias", "mov_inventario", "orden_lineas", "packs",
    "pagos", "compromisos_pagos", "entregas_repuesto", "repuestos_prepagados",
)


def upgrade():
    conexion = op.get_bind()
    for tabla in TABLAS:
        secuencia = conexion.execute(
            sa.text("SELECT pg_get_serial_sequence(:tabla, 'id')"), {"tabla": f"public.{tabla}"}
        ).scalar_one()
        if secuencia:
            conexion.execute(
                sa.text(
                    f"SELECT setval(:secuencia, GREATEST(COALESCE((SELECT MAX(id) FROM public.\"{tabla}\"), 0), 1), "
                    f"EXISTS (SELECT 1 FROM public.\"{tabla}\"))"
                ),
                {"secuencia": secuencia},
            )


def downgrade():
    pass
