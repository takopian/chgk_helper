import asyncio
import logging
from aiohttp import web
from parser import parse_quizzes, get_difficulty
from utils import request_lifejournal, datetime_serializer, close_lifejournal_session
import os

API_KEY = os.environ.get("WEBSERVER_API_KEY")
logger = logging.getLogger(__name__)

@web.middleware
async def auth_middleware(request, handler):
    if request.headers.get('X-API-Key') != API_KEY:
        return web.Response(status=401, text="Unauthorized")
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception('Failed to handle %s %s', request.method, request.path)
        return web.json_response({'error': 'Failed to parse game announcements; see webserver logs'}, status=500)

async def parse_quizzes_endpoint(request):
    url = "https://chgk-spb.livejournal.com/"
    html = await request_lifejournal(url)
    quizzes = parse_quizzes(html)
    
    # Fetch difficulties
    difficulty_tasks = [get_difficulty(quiz.url) for quiz in quizzes]
    difficulties = await asyncio.gather(*difficulty_tasks, return_exceptions=True)
    for quiz, difficulty in zip(quizzes, difficulties):
        if isinstance(difficulty, Exception):
            logger.warning('Could not fetch difficulty for %s: %s: %s',
                           quiz.url, type(difficulty).__name__, difficulty)
            quiz.difficulty = None
        else:
            quiz.difficulty = difficulty
    
    # Convert quizzes to dict for JSON serialization
    quizzes_data = []
    for quiz in quizzes:
        quiz_dict = {
            'poll_text': quiz.poll_text,
            'url': quiz.url,
            'difficulty': quiz.difficulty,
            'date': datetime_serializer(quiz.date) if quiz.date else None,
            'id': quiz.id
        }
        quizzes_data.append(quiz_dict)
    return web.json_response(quizzes_data)

async def init_app():
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get('/parse_quizzes', parse_quizzes_endpoint)
    app.on_cleanup.append(cleanup)
    return app


async def cleanup(app):
    await close_lifejournal_session()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)
    port = int(os.environ.get("WEBSERVER_PORT", 8000))
    app = init_app()
    web.run_app(app, host='0.0.0.0', port=port)
