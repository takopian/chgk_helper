import asyncio
from .session import async_engine, async_session
from .models import User, Base

from sqlalchemy import select
from sqlalchemy import insert
from datetime import date
from .models import Competition, Question, UsersRegistrations, Answer, QuestionPool
from sqlalchemy import func


# async def init_db():
#     # create tables (useful for tests or non-migration setups)
#     async with async_engine.begin() as conn:
#         await conn.run_sync(Base.metadata.create_all)


async def create_user(username: str):
    async with async_session() as session:
        user = User(username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user




async def get_or_create_user(tg_id: int, username: str):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if user:
            return user
        user = User(tg_id=tg_id, username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user
    
async def create_competition(name: str, start_date: date, end_date: date, competition_type: int = 0):
    async with async_session() as session:
        comp = Competition(name=name, start_date=start_date, end_date=end_date, competition_type=competition_type)
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        return comp


async def add_question(competition_id: int, body: str, answer_text: str, image_path: str | None = None, q_date: date | None = None):
    # Ensure date is provided; default to today's date (server local)
    if q_date is None:
        q_date = date.today()

    async with async_session() as session:
        # Check for existing question for this competition on the same date
        existing = await session.execute(
            select(Question).where(Question.competition_id == competition_id, Question.date == q_date)
        )
        if existing.scalars().first():
            raise ValueError(f"Question for competition {competition_id} on {q_date} already exists")

        q = Question(competition_id=competition_id, body=body, answer=answer_text, image_path=image_path, date=q_date)
        session.add(q)
        await session.commit()
        await session.refresh(q)
        return q


async def register_user(tg_id: int, username: str, competition_id: int):
    # ensure user exists
    async with async_session() as session:
        user = None
        if tg_id is not None:
            result = await session.execute(select(User).where(User.tg_id == tg_id))
            user = result.scalars().first()
        if not user:
            user = User(tg_id=tg_id, username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        # create registration if not exists
        result = await session.execute(select(UsersRegistrations).where(UsersRegistrations.user_id == user.id, UsersRegistrations.competition_id == competition_id))
        reg = result.scalars().first()
        if reg:
            return reg

        reg = UsersRegistrations(user_id=user.id, competition_id=competition_id)
        session.add(reg)
        await session.commit()
        await session.refresh(reg)
        return reg


async def record_answer(user_id: int, question_id: int, answer_text: str):
    async with async_session() as session:
        ans = Answer(user_id=user_id, question_id=question_id, answer=answer_text)
        session.add(ans)
        await session.commit()
        await session.refresh(ans)
        return ans


async def get_todays_question_for_competition(competition_id: int, when: date | None = None):
    if when is None:
        when = date.today()
    async with async_session() as session:
        result = await session.execute(select(Question).where(Question.competition_id == competition_id, Question.date == when))
        return result.scalars().first()


async def get_unasked_pool_count():
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(QuestionPool).where(QuestionPool.used == False))
        return int(result.scalar() or 0)


async def get_all_present_packs() -> list[str]:
    async with async_session() as session:
        result = await session.execute(select(QuestionPool.source_pack).distinct())
        return [row[0] for row in result.fetchall() if row[0] is not None]


async def add_pool_question(body: str, answer_text: str | None = None, handout: str | None = None, comment: str | None = None, image_path: str | None = None, source_pack: str | None = None):
    async with async_session() as session:
        # check duplicate by body+source_pack
        q = await session.execute(select(QuestionPool).where(QuestionPool.body == body, QuestionPool.source_pack == source_pack))
        existing = q.scalars().first()
        if existing:
            return existing
        pool_q = QuestionPool(body=body, answer=answer_text, handout=handout, comment=comment, image_path=image_path, source_pack=source_pack)
        session.add(pool_q)
        await session.commit()
        await session.refresh(pool_q)
        return pool_q


async def pop_random_pool_question_and_mark_used():
    async with async_session() as session:
        # select random unused question
        result = await session.execute(select(QuestionPool).where(QuestionPool.used == False).order_by(func.random()).limit(1))
        pool_q = result.scalars().first()
        if not pool_q:
            return None
        pool_q.used = True
        session.add(pool_q)
        await session.commit()
        await session.refresh(pool_q)
        return pool_q
