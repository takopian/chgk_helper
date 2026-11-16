"""Store new Telegram polls, vote history and current choices."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = 'e483a10f2c61'
down_revision = 'c712e3b490ab'
branch_labels = None
depends_on = None


def voter_columns(primary=False):
    return [sa.Column('voter_kind', sa.String(8), nullable=False, primary_key=primary),
            sa.Column('voter_id', sa.BigInteger(), nullable=False, primary_key=primary),
            sa.Column('display_name', sa.Text(), nullable=False),
            sa.Column('username', sa.Text()),
            sa.Column('option_ids', ARRAY(sa.Integer()), nullable=False),
            sa.Column('received_at', sa.DateTime(timezone=True), nullable=False)]


def poll_fk():
    return sa.ForeignKeyConstraint(['bot_id', 'poll_id'], ['telegram_polls.bot_id', 'telegram_polls.poll_id'])


def upgrade():
    op.create_table('telegram_polls',
        sa.Column('bot_id', sa.BigInteger(), primary_key=True),
        sa.Column('poll_id', sa.String(128), primary_key=True),
        sa.Column('chat_id', sa.BigInteger(), sa.ForeignKey('poll_chats.id'), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('telegram_poll_options',
        sa.Column('bot_id', sa.BigInteger(), primary_key=True),
        sa.Column('poll_id', sa.String(128), primary_key=True),
        sa.Column('option_id', sa.Integer(), primary_key=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('game_id', sa.String(32)), poll_fk(),
        sa.ForeignKeyConstraint(['chat_id', 'game_id'], ['poll_games.chat_id', 'poll_games.id']))
    op.create_table('poll_vote_events',
        sa.Column('bot_id', sa.BigInteger(), primary_key=True),
        sa.Column('update_id', sa.BigInteger(), primary_key=True),
        sa.Column('poll_id', sa.String(128), nullable=False),
        *voter_columns(), poll_fk())
    op.create_index('ix_poll_vote_events_poll_id', 'poll_vote_events', ['poll_id'])
    op.create_table('poll_votes',
        sa.Column('bot_id', sa.BigInteger(), primary_key=True),
        sa.Column('poll_id', sa.String(128), primary_key=True),
        *voter_columns(primary=True),
        sa.Column('last_update_id', sa.BigInteger(), nullable=False), poll_fk())


def downgrade():
    op.drop_table('poll_votes')
    op.drop_index('ix_poll_vote_events_poll_id', table_name='poll_vote_events')
    op.drop_table('poll_vote_events')
    op.drop_table('telegram_poll_options')
    op.drop_table('telegram_polls')
