"""make document identity metadata optional

Revision ID: dacca29182ec
Revises: ef22d12e25af
Create Date: 2026-07-21 13:24:13.033818

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dacca29182ec'
down_revision: Union[str, Sequence[str], None] = 'ef22d12e25af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make aktenzeichen and klassifizierung nullable.

    s3_object_key stays NOT NULL — a document must have a storage location.
    """
    op.alter_column('documents', 'aktenzeichen',
               existing_type=sa.VARCHAR(length=64),
               nullable=True)
    op.alter_column('documents', 'klassifizierung',
               existing_type=sa.VARCHAR(length=32),
               nullable=True)


def downgrade() -> None:
    """Restore NOT NULL. Fails if any row has NULL in these columns — clean them
    (set a value or delete the rows) before downgrading."""
    op.alter_column('documents', 'klassifizierung',
               existing_type=sa.VARCHAR(length=32),
               nullable=False)
    op.alter_column('documents', 'aktenzeichen',
               existing_type=sa.VARCHAR(length=64),
               nullable=False)
