import asyncio
from .session import async_engine, async_session
from .models import User, Base

from sqlalchemy import select
from sqlalchemy import insert
from datetime import date
from sqlalchemy import func
from .models import Competition, Question, UsersRegistrations, Answer, QuestionPool, Author, Tournament, QuestionFeedback, question_authors, question_tournaments
from sqlalchemy import text
from pathlib import Path





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


async def add_question(competition_id: int, body: str, answer_text: str, image_path: str | None = None, q_date: date | None = None, qid: int | None = None):
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
        kwargs = {
            'competition_id': competition_id,
            'body': body,
            'answer': answer_text,
            'image_path': image_path,
            'date': q_date
        }
        if qid is not None:
            kwargs['id'] = qid
        q = Question(**kwargs)
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


async def unregister_user(tg_id: int, competition_id: int) -> bool:
    """Unregister a user from a competition by their Telegram ID."""
    async with async_session() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalars().first()
        if not user:
            return False

        reg_result = await session.execute(
            select(UsersRegistrations).where(
                UsersRegistrations.user_id == user.id,
                UsersRegistrations.competition_id == competition_id,
            )
        )
        reg = reg_result.scalars().first()
        if not reg:
            return False

        await session.delete(reg)
        await session.commit()
        return True


async def copy_registrations_from_competition(source_competition_id: int, target_competition_id: int) -> int:
    """Copy registrations from one competition to another, avoiding duplicates."""
    async with async_session() as session:
        source_regs = await session.execute(
            select(UsersRegistrations.user_id).where(UsersRegistrations.competition_id == source_competition_id)
        )
        source_user_ids = {row[0] for row in source_regs.fetchall()}

        if not source_user_ids:
            return 0

        existing_target_regs = await session.execute(
            select(UsersRegistrations.user_id).where(UsersRegistrations.competition_id == target_competition_id)
        )
        existing_target_user_ids = {row[0] for row in existing_target_regs.fetchall()}

        inserted = 0
        for user_id in source_user_ids:
            if user_id in existing_target_user_ids:
                continue
            session.add(UsersRegistrations(user_id=user_id, competition_id=target_competition_id))
            inserted += 1

        if inserted > 0:
            await session.commit()

        return inserted


async def get_previous_competition(start_date: date):
    """Return the most recently ended competition before the given start date."""
    async with async_session() as session:
        result = await session.execute(
            select(Competition)
            .where(Competition.end_date < start_date)
            .order_by(Competition.end_date.desc())
            .limit(1)
        )
        return result.scalars().first()


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


async def add_pool_question(
    body: str,
    answer_text: str | None = None,
    handout: str | None = None,
    comment: str | None = None,
    image_path: str | None = None,
    authors: list[dict] | None = None,
    tournaments: list[dict] | None = None,
    source_pack: str | None = None,
):
    async with async_session() as session:
        # check duplicate by body+source_pack
        q = await session.execute(select(QuestionPool).where(QuestionPool.body == body, QuestionPool.source_pack == source_pack))
        existing = q.scalars().first()
        if existing:
            return existing
        pool_q = QuestionPool(body=body, answer=answer_text, handout=handout, comment=comment, image_path=image_path, source_pack=source_pack)
        session.add(pool_q)

        # flush to get pool_q.id for associations
        await session.flush()

        # handle authors
        if authors:
            for a in authors:
                # expected structure: {'id': 13123, 'name': 'Павел Клепиков', 'gender': 'HE'}
                aid = a.get('id')
                name = a.get('name')
                author_obj = None
                if aid is not None:
                    author_obj = await session.get(Author, aid)
                    if author_obj and name and author_obj.name != name:
                        author_obj.name = name
                if author_obj is None and name:
                    result = await session.execute(select(Author).where(Author.name == name))
                    author_obj = result.scalars().first()
                if author_obj is None:
                    # create author (respect provided id if present)
                    if aid is not None:
                        author_obj = Author(id=aid, name=name)
                    else:
                        author_obj = Author(name=name)
                    session.add(author_obj)
                    await session.flush()

                # insert association if not exists
                if author_obj is not None:
                    res = await session.execute(
                        select(question_authors).where(question_authors.c.question_id == pool_q.id, question_authors.c.author_id == author_obj.id)
                    )
                    if not res.first():
                        await session.execute(question_authors.insert().values(question_id=pool_q.id, author_id=author_obj.id))

        # handle tournaments
        if tournaments:
            for t in tournaments:
                # expected structure: {'id': 12858, 'title': 'Чемпионат ГолКвиза. Сезон 3'}
                tid = t.get('id')
                title = t.get('title')
                tour_obj = None
                if tid is not None:
                    tour_obj = await session.get(Tournament, tid)
                    if tour_obj and title and tour_obj.title != title:
                        tour_obj.title = title
                if tour_obj is None and title:
                    result = await session.execute(select(Tournament).where(Tournament.title == title))
                    tour_obj = result.scalars().first()
                if tour_obj is None:
                    if tid is not None:
                        tour_obj = Tournament(id=tid, title=title)
                    else:
                        tour_obj = Tournament(title=title)
                    session.add(tour_obj)
                    await session.flush()

                if tour_obj is not None:
                    res = await session.execute(
                        select(question_tournaments).where(question_tournaments.c.question_id == pool_q.id, question_tournaments.c.tournament_id == tour_obj.id)
                    )
                    if not res.first():
                        await session.execute(question_tournaments.insert().values(question_id=pool_q.id, tournament_id=tour_obj.id))

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


# Feedback (evaluation) functions
async def add_question_feedback(user_id: int, question_id: int, liked: bool):
    """Add or update feedback for a question (like/dislike)"""
    async with async_session() as session:
        # Check if feedback already exists
        result = await session.execute(
            select(QuestionFeedback).where(
                QuestionFeedback.user_id == user_id,
                QuestionFeedback.question_id == question_id
            )
        )
        feedback = result.scalars().first()
        
        if feedback:
            return feedback
        
        # Create new feedback
        feedback = QuestionFeedback(user_id=user_id, question_id=question_id, liked=liked)
        session.add(feedback)
        
        await session.commit()
        await session.refresh(feedback)
        return feedback


async def get_weighted_random_pool_question_and_mark_used():
    """Execute the weighted random-selection SQL (stored in db/queries/question_query.sql),
    return the matching QuestionPool mapped object and mark it used.
    """
    sql_path = Path(__file__).parent / "queries" / "question_query.sql"
    sql = sql_path.read_text(encoding="utf-8")

    async with async_session() as session:
        result = await session.execute(text(sql))
        row = result.first()
        if not row:
            return None

        # SQL expected to return an `id` column as first column
        qid = row[0]
        pool_q = await session.get(QuestionPool, qid)
        if not pool_q:
            return None

        pool_q.used = True
        session.add(pool_q)
        await session.commit()
        await session.refresh(pool_q)
        return pool_q
