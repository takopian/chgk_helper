"""Read-only integration checks; does not start Telegram polling or send messages."""
import asyncio
import os
from unittest.mock import AsyncMock, patch

import aiohttp
from sqlalchemy import text
from telegram.ext import Application

from bots import user_bot
from db.session import async_engine
from parser import get_packs_by_complexity_sync, parse_pack_questions
from utils import fetch


async def check():
    async with async_engine.connect() as conn:
        assert await conn.scalar(text('SELECT 1')) == 1
        print('Database OK; question pool count:', await conn.scalar(text('SELECT count(*) FROM question_pool')))
    await async_engine.dispose()

    async with aiohttp.ClientSession() as session:
        base = os.environ['PARSER_BASE_URL'].rstrip('/')
        home = await fetch(session, base)
        assert home, 'Listing fetch failed'
        print('Selected packs:', get_packs_by_complexity_sync(home))
        page = await fetch(session, base + '/pack/7365')
        assert page, 'Pack fetch failed'
        questions = parse_pack_questions(page)
        assert len(questions) == 36
        print('Live GotQuestions OK: 36 questions')

    captured = []
    with patch.dict(os.environ, {'USER_BOT_TOKEN': '123456:offline-test', 'WEBHOOK_PORT': '0'}):
        with patch.object(Application, 'run_polling', lambda app, **kwargs: captured.append(app)):
            user_bot.main()
    app = captured[0]
    with patch.object(user_bot, 'post_init', new_callable=AsyncMock):
        await app.post_init(app)
    runner = app.bot_data['webhook_runner']
    port = runner.addresses[0][1]
    try:
        async with aiohttp.ClientSession() as session:
            for route in ['/webhook/question', '/webhook/competition']:
                async with session.post(f'http://127.0.0.1:{port}{route}', json={}) as response:
                    assert response.status == 400, (route, response.status)
        print('Webhook startup and both routes OK (validation only, no notifications)')
    finally:
        await app.post_shutdown(app)
    assert 'webhook_runner' not in app.bot_data
    print('Webhook cleanup OK')


if __name__ == '__main__':
    asyncio.run(check())
