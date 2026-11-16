"""Persistence for the poll bot; callers own transaction boundaries."""
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PollChat, PollGame, PollRegistration
from db.session import async_engine


@asynccontextmanager
async def chat_session(chat_id):
    # Keep one connection so the session lock survives per-poll commits.
    # Concurrent /createpoll commands in the same chat must not send duplicates.
    async with async_engine.connect() as connection:
        await connection.execute(text('SELECT pg_advisory_lock(:id)'), {'id': chat_id})
        await connection.commit()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            await connection.rollback()
            await connection.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': chat_id})
            await connection.commit()


async def games_for_chat(session, chat_id):
    return list((await session.scalars(
        select(PollGame).where(PollGame.chat_id == chat_id).order_by(PollGame.date, PollGame.id)
    )).all())


async def save_games(session, chat_id, quizzes):
    await session.execute(insert(PollChat).values(id=chat_id).on_conflict_do_nothing())
    games = []
    for quiz in quizzes:
        if not quiz.date or quiz.date.tzinfo is not None:
            raise ValueError('Poll game must have a local, timezone-naive date')
        game_id = quiz.id or uuid4().hex
        if len(game_id) > 32:
            raise ValueError('Poll game ID exceeds Telegram callback limit')
        await session.execute(insert(PollGame).values(
            chat_id=chat_id, id=game_id, poll_text=quiz.poll_text, url=quiz.url,
            difficulty=str(quiz.difficulty) if quiz.difficulty is not None else None, date=quiz.date,
        ).on_conflict_do_nothing(index_elements=['chat_id', 'poll_text']))
        games.append(await session.scalar(select(PollGame).where(
            PollGame.chat_id == chat_id, PollGame.poll_text == quiz.poll_text)))
    return games


async def register_game(session, chat_id, game_id):
    game = await session.get(PollGame, (chat_id, game_id))
    if game is None:
        return None
    await session.execute(insert(PollRegistration).values(
        chat_id=chat_id, game_id=game_id).on_conflict_do_nothing())
    return game


async def unregister_game(session, chat_id, game_id):
    game = await session.get(PollGame, (chat_id, game_id))
    if game is None:
        return None
    removed = await session.scalar(delete(PollRegistration).where(
        PollRegistration.chat_id == chat_id,
        PollRegistration.game_id == game_id,
    ).returning(PollRegistration.game_id))
    return game if removed is not None else None


async def registered_games(session, day, chat_id=None, only_today=False):
    start = datetime.combine(day, time.min)
    query = select(PollGame).join(PollRegistration).where(PollGame.date >= start)
    if chat_id is not None:
        query = query.where(PollGame.chat_id == chat_id)
    if only_today:
        query = query.where(PollGame.date < start + timedelta(days=1))
    return list((await session.scalars(query.order_by(PollGame.date, PollGame.id))).all())
