"""Move poll bot chat state into PostgreSQL."""
from alembic import op
import sqlalchemy as sa

revision = 'c712e3b490ab'
down_revision = '9ee3f1f4b114'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('poll_chats', sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=False))
    op.create_table(
        'poll_games',
        sa.Column('chat_id', sa.BigInteger(), sa.ForeignKey('poll_chats.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('poll_text', sa.Text(), nullable=False),
        sa.Column('url', sa.Text()),
        sa.Column('difficulty', sa.String(128)),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('chat_id', 'poll_text', name='uq_poll_game_chat_text'),
    )
    op.create_index('ix_poll_games_date', 'poll_games', ['date'])
    op.create_table(
        'poll_registrations',
        sa.Column('chat_id', sa.BigInteger(), primary_key=True),
        sa.Column('game_id', sa.String(32), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id', 'game_id'], ['poll_games.chat_id', 'poll_games.id'], ondelete='CASCADE'),
    )


def downgrade():
    op.drop_table('poll_registrations')
    op.drop_index('ix_poll_games_date', table_name='poll_games')
    op.drop_table('poll_games')
    op.drop_table('poll_chats')
