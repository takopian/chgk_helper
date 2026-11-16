"""Poll metadata, immutable vote history and current selections."""
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from db.models import TelegramPoll, TelegramPollOption, PollVote, PollVoteEvent


async def game_voters(session, bot_id, chat_id, game_id):
    """Current user voters for this game, deduplicated across tracked polls."""
    rows = (await session.scalars(select(PollVote).join(TelegramPollOption,
        (TelegramPollOption.bot_id == PollVote.bot_id) &
        (TelegramPollOption.poll_id == PollVote.poll_id) &
        PollVote.option_ids.any(TelegramPollOption.option_id)
    ).where(
        PollVote.bot_id == bot_id, PollVote.voter_kind == 'user',
        TelegramPollOption.chat_id == chat_id, TelegramPollOption.game_id == game_id,
    ).order_by(PollVote.received_at.desc(), PollVote.voter_id))).all()
    unique = {}
    for row in rows:
        unique.setdefault(row.voter_id, row)
    return list(unique.values())


async def save_poll(session, bot_id, message, games, options):
    session.add(TelegramPoll(bot_id=bot_id, poll_id=message.poll.id,
                            chat_id=message.chat.id, message_id=message.message_id,
                            created_at=message.date))
    await session.flush()
    for index, label in enumerate(options):
        session.add(TelegramPollOption(
            bot_id=bot_id, poll_id=message.poll.id, option_id=index, text=label,
            chat_id=message.chat.id, game_id=games[index].id if index < len(games) else None))
    await session.flush()


async def save_vote(session, *, bot_id, update_id, poll_id, voter_kind, voter_id,
                    display_name, username, option_ids, received_at):
    # Serialize changes to each poll. Unknown (including old) polls are ignored.
    poll = await session.scalar(select(TelegramPoll).where(
        TelegramPoll.bot_id == bot_id, TelegramPoll.poll_id == poll_id).with_for_update())
    if poll is None:
        return 'unknown'
    if voter_kind not in ('user', 'chat'):
        raise ValueError('Unsupported voter kind')
    chosen = sorted(set(option_ids))
    valid = set((await session.scalars(select(TelegramPollOption.option_id).where(
        TelegramPollOption.bot_id == bot_id, TelegramPollOption.poll_id == poll_id))).all())
    if not set(chosen) <= valid:
        raise ValueError('Unknown poll option')
    values = dict(bot_id=bot_id, poll_id=poll_id, voter_kind=voter_kind, voter_id=voter_id,
                  display_name=display_name, username=username, option_ids=chosen, received_at=received_at)
    inserted = await session.scalar(insert(PollVoteEvent).values(
        **values, update_id=update_id).on_conflict_do_nothing().returning(PollVoteEvent.update_id))
    if inserted is None:
        return 'duplicate'
    current = await session.get(PollVote, (bot_id, poll_id, voter_kind, voter_id))
    if current is not None:
        # Telegram can reset its update sequence after a week without updates.
        if received_at < current.received_at or (
            update_id <= current.last_update_id and received_at - current.received_at < timedelta(days=7)
        ):
            return 'stale'
        for key, value in values.items():
            setattr(current, key, value)
        current.last_update_id = update_id
    else:
        session.add(PollVote(**values, last_update_id=update_id))
    # [] is kept as a tombstone, so delayed old votes cannot resurrect a withdrawal.
    await session.flush()
    return 'saved'
