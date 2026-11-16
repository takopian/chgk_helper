"""Import legacy last.txt atomically, without starting Telegram bots."""
import argparse
import asyncio
import json
from pathlib import Path
from sqlalchemy import func, select

from db.polls import save_games, register_game
from db.models import PollChat, PollGame, PollRegistration
from db.session import async_session, async_engine
from quiz import Quiz


def read_legacy(path):
    # Read directly: unlike the old loader, corruption must abort the import.
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object keyed by chat ID')
    result = []
    for chat_id, state in data.items():
        games = [Quiz(**item) for item in state.get('poll_quizzes', [])]
        registered = [Quiz(**item) for item in state.get('registered_quizzes', [])]
        result.append((int(chat_id), games, registered))
    return result


async def import_legacy(session, data):
    for chat_id, games, registered in data:
        await save_games(session, chat_id, games)
        # Preserve registrations even when their game is missing from poll_quizzes.
        for game in await save_games(session, chat_id, registered):
            await register_game(session, chat_id, game.id)


async def run(path):
    data = read_legacy(path)
    try:
        async with async_session() as session:
            async with session.begin():
                await import_legacy(session, data)
            counts = [await session.scalar(select(func.count()).select_from(model))
                      for model in (PollChat, PollGame, PollRegistration)]
        print(f'Imported {len(data)} chats; source file unchanged. Re-running is safe.')
        print(f'Database totals: {counts[0]} chats, {counts[1]} games, {counts[2]} registrations.')
    finally:
        await async_engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', default='last.txt')
    asyncio.run(run(parser.parse_args().path))
