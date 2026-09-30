"""Añade seguimiento de personalizaciones y adelantos a despachadores.

Revisión: 0003_operaciones_recientes
Anterior: 0002_ids_autogenerados
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_operaciones_recientes"
down_revision = "0002_ids_autogenerados"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orden_lineas", sa.Column("perso_lista", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("orden_lineas", sa.Column("perso_lista_en", sa.Text(), nullable=True))
    op.add_column("pagos_despachador", sa.Column("adelanto_usado", sa.Float(), nullable=False, server_default=sa.text("0")))


def downgrade():
    op.drop_column("pagos_despachador", "adelanto_usado")
    op.drop_column("orden_lineas", "perso_lista_en")
    op.drop_column("orden_lineas", "perso_lista")
