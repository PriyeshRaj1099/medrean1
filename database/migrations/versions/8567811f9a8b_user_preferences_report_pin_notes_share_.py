"""user preferences, report pin notes, share links, guest demo

Revision ID: 8567811f9a8b
Revises: 4f87e5daae5e
Create Date: 2026-09-26 21:35:27.316446

NOTE: NOT NULL columns are added with a temporary server_default so the
migration applies to existing data; the default is then dropped so the
final schema matches the SQLAlchemy models exactly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8567811f9a8b'
down_revision: Union[str, Sequence[str], None] = '4f87e5daae5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('report_share_links',
    sa.Column('report_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_viewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('view_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['report_id'], ['reports.id'], name=op.f('fk_report_share_links_report_id_reports'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_report_share_links_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_report_share_links')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_report_share_links_token_hash'))
    )
    with op.batch_alter_table('report_share_links', schema=None) as batch_op:
        batch_op.create_index('ix_report_share_links_report_id', ['report_id'], unique=False)
        batch_op.create_index('ix_report_share_links_user_id', ['user_id'], unique=False)

    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('version', sa.String(length=40), nullable=True))

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('note_include_in_analysis', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index('ix_reports_user_pinned', ['user_id', 'is_pinned'], unique=False)
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.alter_column('is_pinned', existing_type=sa.Boolean(), existing_nullable=False, server_default=None)
        batch_op.alter_column('note_include_in_analysis', existing_type=sa.Boolean(), existing_nullable=False, server_default=None)

    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('remember', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.alter_column('remember', existing_type=sa.Boolean(), existing_nullable=False, server_default=None)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('google_id', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('explanation_level', sa.String(length=20), nullable=False, server_default='simple'))
        batch_op.add_column(sa.Column('notifications_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.create_index(batch_op.f('ix_users_phone'), ['phone'], unique=True)
        batch_op.create_unique_constraint(batch_op.f('uq_users_google_id'), ['google_id'])
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('explanation_level', existing_type=sa.String(length=20), existing_nullable=False, server_default=None)
        batch_op.alter_column('notifications_enabled', existing_type=sa.Boolean(), existing_nullable=False, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_users_google_id'), type_='unique')
        batch_op.drop_index(batch_op.f('ix_users_phone'))
        batch_op.drop_column('notifications_enabled')
        batch_op.drop_column('explanation_level')
        batch_op.drop_column('google_id')
        batch_op.drop_column('phone')

    with op.batch_alter_table('user_sessions', schema=None) as batch_op:
        batch_op.drop_column('remember')

    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_index('ix_reports_user_pinned')
        batch_op.drop_column('note_include_in_analysis')
        batch_op.drop_column('note')
        batch_op.drop_column('pinned_at')
        batch_op.drop_column('is_pinned')

    with op.batch_alter_table('knowledge_documents', schema=None) as batch_op:
        batch_op.drop_column('version')

    with op.batch_alter_table('report_share_links', schema=None) as batch_op:
        batch_op.drop_index('ix_report_share_links_user_id')
        batch_op.drop_index('ix_report_share_links_report_id')

    op.drop_table('report_share_links')
