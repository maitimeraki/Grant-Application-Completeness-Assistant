"""add llm call logs

Revision ID: c3f8a2d91b47
Revises: 1bd54483c662
Create Date: 2026-10-13 14:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c3f8a2d91b47'
down_revision: str | None = '1bd54483c662'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('llm_call_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('assessment_id', sa.Integer(), nullable=True),
    sa.Column('stage', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('prompt_chars', sa.Integer(), nullable=True),
    sa.Column('completion_chars', sa.Integer(), nullable=True),
    sa.Column('tokens_prompt', sa.Integer(), nullable=True),
    sa.Column('tokens_completion', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['assessment_id'], ['assessments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llm_call_logs_assessment_id'), 'llm_call_logs', ['assessment_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_llm_call_logs_assessment_id'), table_name='llm_call_logs')
    op.drop_table('llm_call_logs')
