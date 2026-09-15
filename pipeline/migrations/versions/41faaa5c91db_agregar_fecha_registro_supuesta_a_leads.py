"""agregar fecha_registro_supuesta a leads

Revision ID: 41faaa5c91db
Revises: b0d7bbdb6257
Create Date: 2026-09-15 20:23:28.111501

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41faaa5c91db'
down_revision: Union[str, Sequence[str], None] = 'b0d7bbdb6257'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('leads', sa.Column('fecha_registro_supuesta', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('leads', 'fecha_registro_supuesta')