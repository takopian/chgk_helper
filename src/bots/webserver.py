import asyncio
from aiohttp import web
from parser import parse_quizzes, get_difficulty
from utils import request_lifejournal, datetime_serializer
import os

API_KEY = os.environ.get("WEBSERVER_API_KEY")

async def auth_middleware(app, handler):
    async def middleware(request):
        if request.headers.get('X-API-Key') != API_KEY:
            return web.Response(status=401, text="Unauthorized")
        return await handler(request)
    return middleware

async def parse_quizzes_endpoint(request):
    url = "https://chgk-spb.livejournal.com/"
    html = await request_lifejournal(url)
    quizzes = parse_quizzes(html)
    
    # Fetch difficulties
    difficulty_tasks = [get_difficulty(quiz.url) for quiz in quizzes]
    difficulties = await asyncio.gather(*difficulty_tasks)
    for quiz, difficulty in zip(quizzes, difficulties):
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
    return app

if __name__ == '__main__':
    port = int(os.environ.get("WEBSERVER_PORT", 8000))
    app = init_app()
    web.run_app(app, host='0.0.0.0', port=port)