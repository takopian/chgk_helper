import logging
import asyncio
import os
from html import escape
from datetime import datetime, time, timezone

import aiohttp
import pytz
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, CallbackQueryHandler, Application, PollAnswerHandler
from sqlalchemy.exc import SQLAlchemyError

from quiz import Quiz
from db.session import async_session
from db.poll_votes import save_poll, save_vote, game_voters
from db.polls import chat_session, games_for_chat, save_games, register_game, registered_games, unregister_game

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)


async def register(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    today = datetime.utcnow().date()
    async with async_session() as session:
        games = await games_for_chat(session, chat_id)
        registered = {game.id for game in await registered_games(session, today, chat_id)}
    if not games:
        await update.message.reply_text(
            'Прежде чем отмечать зарегистрированные турниры, нужно хоть раз выполнить команду /createpoll.')
        return
    options = [game for game in games if game.id not in registered and game.date.date() >= today]
    if not options:
        await update.message.reply_text('Нет новых игр для регистрации.')
        return
    keyboard = [[InlineKeyboardButton(game.poll_text, callback_data=f'register:{game.id}')] for game in options]
    await update.message.reply_text('Выбери игру, на которую ты зарегал команду:',
                                    reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_register(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    async with async_session() as session:
        async with session.begin():
            game = await register_game(session, update.effective_chat.id, query.data[9:])
    if game is None:
        await query.edit_message_text('Игра не найдена. Обновите список командой /register.')
        return
    await query.edit_message_text(f'Зарегистрировались на игру: {game.poll_text}')


async def unregister(update: Update, context: CallbackContext) -> None:
    async with async_session() as session:
        games = await registered_games(session, datetime.utcnow().date(), update.effective_chat.id)
    if not games:
        await update.message.reply_text('Нет зарегистрированных игр для отмены.')
        return
    keyboard = [[InlineKeyboardButton(game.poll_text, callback_data=f'unregister:{game.id}')]
                for game in games]
    await update.message.reply_text('Выбери игру, регистрацию на которую нужно отменить:',
                                    reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_unregister(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    async with async_session() as session:
        async with session.begin():
            game = await unregister_game(session, update.effective_chat.id, query.data[len('unregister:'):])
    if game is None:
        await query.edit_message_text('Регистрация уже отменена или игра не найдена. Обновите список командой /unregister.')
        return
    await query.edit_message_text(f'Регистрация отменена: {game.poll_text}')


async def get_registered(update: Update, context: CallbackContext) -> None:
    async with async_session() as session:
        games = await registered_games(session, datetime.utcnow().date(), update.effective_chat.id)
    if not games:
        await update.message.reply_text('Нет зарегистрированных игр.')
        return
    formatted = '\n'.join(f'- {game.poll_text}' for game in games)
    await update.message.reply_text(f'Зарегистрированные игры:\n{formatted}.')


async def notify_registered(context, chat_id=None):
    async with async_session() as session:
        games = await registered_games(session, datetime.utcnow().date(), chat_id=chat_id, only_today=True)
    sent = 0
    for game in games:
        try:
            # Fetch current selections immediately before this game's reminder.
            async with async_session() as session:
                voters = await game_voters(session, context.bot.id, game.chat_id, game.id)
            for message in reminder_messages(game.poll_text, voters):
                await context.bot.send_message(chat_id=game.chat_id, text=message, parse_mode='HTML')
            sent += 1
        except Exception:
            logging.exception('Failed to send game reminder to chat %s', game.chat_id)
    return sent


def reminder_messages(game_title, voters):
    """Keep complete HTML entities and mentions within Telegram message limits."""
    heading = f'Напоминаю, что сегодня состоится игра:\n {game_title}'
    # 1500 Unicode characters use at most 3000 UTF-16 code units.
    parts = [escape(heading[i:i + 1500]) for i in range(0, len(heading), 1500)]
    size = len(heading[-(len(heading) % 1500 or 1500):].encode('utf-16-le')) // 2
    count = 0
    for voter in voters:
        name = voter.display_name or str(voter.voter_id)
        name_size = len(name.encode('utf-16-le')) // 2
        prefix = '\n\nУчастники: ' if count == 0 else ', '
        if count >= 50 or size + len(prefix) + name_size > 3800:
            parts.append('')
            size, count = 0, 0
            prefix = 'Участники: '
        parts[-1] += prefix + f'<a href="tg://user?id={voter.voter_id}">{escape(name)}</a>'
        size += len(prefix) + name_size
        count += 1
    return parts


def poll_batches(quizzes):
    # Telegram requires at least two options, including for a single new game.
    remaining = list(quizzes)
    while remaining:
        size = 9 if len(remaining) == 11 else 10
        yield remaining[:size]
        remaining = remaining[size:]


async def create_poll(update: Update, context) -> None:
    webserver_url = os.environ.get('WEBSERVER_URL', 'http://webserver:8000')
    api_key = os.environ.get('WEBSERVER_API_KEY')
    headers = {'X-API-Key': api_key} if api_key else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as client:
        async with client.get(f'{webserver_url.rstrip("/")}/parse_quizzes', headers=headers) as resp:
            if resp.status != 200:
                await update.message.reply_text('Failed to fetch quizzes from webserver.')
                return
            quizzes = [Quiz(**data) for data in await resp.json()]
    chat_id = update.effective_chat.id
    async with chat_session(chat_id) as session:
        known = {game.poll_text for game in await games_for_chat(session, chat_id)}
        new_quizzes = []
        for quiz in quizzes:
            if quiz.poll_text not in known:
                known.add(quiz.poll_text)
                new_quizzes.append(quiz)
        if not new_quizzes:
            await update.message.reply_text('Нет новых игр.')
            return
        for batch in poll_batches(new_quizzes):
            # Validate and stage before sending; a failed Telegram send rolls back.
            games = await save_games(session, chat_id, batch)
            options = [f'{quiz.poll_text[:90]}, с-ь {quiz.difficulty}'[:100] for quiz in batch]
            if len(options) == 1:
                options.append('Не участвую')
            message = await update.message.reply_poll(question='Господа и дамы.', options=options,
                                            is_anonymous=False, allows_multiple_answers=True)
            await save_poll(session, context.bot.id, message, games, options)
            # Each successful poll survives failure of a later batch.
            await session.commit()


async def handle_poll_answer(update: Update, context: CallbackContext) -> None:
    answer = update.poll_answer
    voter = answer.user or answer.voter_chat
    if voter is None:
        logging.warning('Poll answer %s has no voter', update.update_id)
        return
    received_at = datetime.now(timezone.utc)
    for attempt in range(3):
        try:
            async with async_session() as session:
                async with session.begin():
                    result = await save_vote(
                        session, bot_id=context.bot.id, update_id=update.update_id,
                        poll_id=answer.poll_id, voter_kind='user' if answer.user else 'chat',
                        voter_id=voter.id, display_name=voter.full_name if answer.user else voter.title or '',
                        username=voter.username, option_ids=answer.option_ids, received_at=received_at)
            if result == 'unknown':
                logging.info('Ignoring answer for untracked poll %s', answer.poll_id)
            return
        except SQLAlchemyError:
            if attempt == 2:
                logging.exception('Could not persist poll answer update %s after retries', update.update_id)
                raise
            await asyncio.sleep(2 ** attempt)


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("createpoll", "Создать опрос на базе последнего анонса игр."),
        BotCommand("register", "Отметить игру, на которую была произведена регистрация."),
        BotCommand("unregister", "Отменить регистрацию на игру."),
        BotCommand("upcoming", "Получить список игр, на которые была произведена регистрация."),
    ]
    await application.bot.set_my_commands(commands)


def main():
    application = ApplicationBuilder(
    ).token(
        os.environ.get("POLLS_BOT_TOKEN")
    ).post_init(post_init).concurrent_updates(False).build()
    application.add_handler(CommandHandler("createpoll", create_poll))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("unregister", unregister))
    application.add_handler(CommandHandler("upcoming", get_registered))
    application.add_handler(CallbackQueryHandler(handle_register, pattern='^register:'))
    application.add_handler(CallbackQueryHandler(handle_unregister, pattern='^unregister:'))
    application.add_handler(PollAnswerHandler(handle_poll_answer))
    application.job_queue.run_daily(notify_registered, time=time(hour=7, minute=0, tzinfo=pytz.utc))

    application.run_polling(allowed_updates=['message', 'callback_query', 'poll_answer'],
                            drop_pending_updates=False)


if __name__ == "__main__":
    main()
