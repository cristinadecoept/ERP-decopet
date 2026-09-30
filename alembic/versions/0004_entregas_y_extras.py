"""Añade fecha de cobros extra y viajes de despachador.

Revisión: 0004_entregas_y_extras
Anterior: 0003_operaciones_recientes
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_entregas_y_extras"
down_revision = "0003_operaciones_recientes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orden_lineas", sa.Column("extra_en", sa.Text(), nullable=True))
    op.add_column("packs", sa.Column("en_ruta", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.create_table(
        "viajes_despachador",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tipo", sa.Text(), nullable=False, server_default=sa.text("'fallido'")),
        sa.Column("orden_id", sa.Integer(), sa.ForeignKey("ordenes.id"), nullable=True),
        sa.Column("pack_id", sa.Integer(), nullable=True),
        sa.Column("prepagado_id", sa.Integer(), nullable=True),
        sa.Column("fecha", sa.Text(), nullable=False),
        sa.Column("despachador", sa.Text(), nullable=False),
        sa.Column("monto", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("pagado", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("pago_id", sa.Integer(), sa.ForeignKey("pagos_despachador.id"), nullable=True),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("creado_en", sa.Text(), nullable=True, server_default=sa.text("to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')")),
    )


def downgrade():
    op.drop_table("viajes_despachador")
    op.drop_column("packs", "en_ruta")
    op.drop_column("orden_lineas", "extra_en")
