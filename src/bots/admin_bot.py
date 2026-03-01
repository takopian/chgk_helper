import logging
import os
from datetime import date, time, datetime
import pytz
import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ApplicationBuilder, Application
from telegram.request import HTTPXRequest

from db.usage import (
    create_competition,
    add_question,
    async_session,
    get_weighted_random_pool_question_and_mark_used,
    get_unasked_pool_count,
    get_all_present_packs, 
    add_pool_question, 
    add_question, 
    get_unasked_pool_count, 
    get_todays_question_for_competition,
)

from db.models import Competition, Answer as AnswerModel
from sqlalchemy import select
from parser import get_packs_by_complexity_sync, parse_pack_questions
from utils import fetch

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
PERMITTED_ADMIN_TG_ID = int(os.environ.get('PERMITTED_ADMIN_TG_ID', '0'))
PARSER_BASE_URL = os.environ.get('PARSER_BASE_URL', '')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'http://user_bot:8001/webhook/question')
WEBHOOK_COMPETITION_URL = os.environ.get('WEBHOOK_COMPETITION_URL', 'http://user_bot:8001/webhook/competition')

MSK = pytz.timezone('Europe/Moscow')


async def notify_user_bot_webhook(competition_id: int, competition_name: str):
    """Send notification to user bot via webhook when question is added."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'competition_id': competition_id,
                'competition_name': competition_name
            }
            async with session.post(WEBHOOK_URL, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logging.info(f"Successfully notified user bot about question for competition {competition_id}")
                else:
                    logging.warning(f"User bot webhook returned status {resp.status}")
    except Exception as e:
        logging.error(f"Failed to notify user bot via webhook: {e}")


async def notify_user_bot_competition_webhook(competition_id: int, competition_name: str):
    """Send notification to user bot via webhook when a competition is created."""

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                'competition_id': competition_id,
                'competition_name': competition_name
            }
            async with session.post(WEBHOOK_COMPETITION_URL, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logging.info(f"Successfully notified user bot about competition {competition_id}")
                else:
                    logging.warning(f"User bot competition webhook returned status {resp.status}")
    except Exception as e:
        logging.error(f"Failed to notify user bot via competition webhook: {e}")


async def admin_create_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # permission check
    if not update.effective_user or update.effective_user.id != PERMITTED_ADMIN_TG_ID:
        await update.message.reply_text('Вы не авторизованы для использования этой команды.')
        return
    # expected: /create_competition name|YYYY-MM-DD|YYYY-MM-DD
    if not context.args:
        await update.message.reply_text('Использование: /create_competition название|YYYY-MM-DD|YYYY-MM-DD[|type] (0 - пул, 1 - вручную)')
        return
    payload = ' '.join(context.args)
    parts = payload.split('|')
    if len(parts) not in (3, 4):
        await update.message.reply_text('Использование: /create_competition название|YYYY-MM-DD|YYYY-MM-DD[|type]')
        return
    name, start_s, end_s = parts[0], parts[1], parts[2]
    competition_type = int(parts[3].strip()) if len(parts) == 4 else '0'
    start_date = date.fromisoformat(start_s.strip())
    end_date = date.fromisoformat(end_s.strip())
    comp = await create_competition(name.strip(), start_date, end_date, competition_type=competition_type)
    await update.message.reply_text(f'Турнир "{comp.name}" создан')
    # Notify user bot about newly created competition
    try:
        await notify_user_bot_competition_webhook(comp.id, comp.name)
    except Exception as e:
        logging.error(f"Failed to notify user bot about new competition: {e}")

async def admin_send_random_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # permission check
    if not update.effective_user or update.effective_user.id != PERMITTED_ADMIN_TG_ID:
        await update.message.reply_text('Вы не авторизованы для использования этой команды.')
        return
    await distribute_random_questions_job(context)

async def admin_add_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # permission check
    if not update.effective_user or update.effective_user.id != PERMITTED_ADMIN_TG_ID:
        # If message exists reply, otherwise ignore
        if update.message:
            await update.message.reply_text('Вы не авторизованы для использования этой команды.')
        return
    # Step 1: Show list of competitions as buttons
    async with async_session() as session:
        res = await session.execute(select(Competition))
        comps = res.scalars().all()

    if not comps:
        await update.message.reply_text('Турниры не найдены.')
        return

    keyboard = [[InlineKeyboardButton(c.name, callback_data=f'add_q_comp:{c.id}')] for c in comps]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Выберите турнир:', reply_markup=reply_markup)


async def add_question_select_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 2: User selected a competition, ask for question body
    query = update.callback_query
    # permission check for callback origin
    if not query.from_user or query.from_user.id != PERMITTED_ADMIN_TG_ID:
        await query.answer('Не авторизовано', show_alert=True)
        return
    await query.answer()
    comp_id = int(query.data.split(':')[1])
    context.user_data['add_q_comp_id'] = comp_id
    await query.edit_message_text('Введите текст вопроса:')
    context.user_data['add_q_step'] = 'body'


async def add_question_ask_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 3: User entered body, ask for answer
    if context.user_data.get('add_q_step') != 'body':
        return
    body = update.message.text
    context.user_data['add_q_body'] = body
    await update.message.reply_text('Введите правильный ответ:')
    context.user_data['add_q_step'] = 'answer'


async def add_question_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 4: User entered answer, save to DB
    if context.user_data.get('add_q_step') != 'answer':
        return
    answer_text = update.message.text
    comp_id = context.user_data.get('add_q_comp_id')
    body = context.user_data.get('add_q_body')
    q_date = datetime.now(MSK).date()
    try:
        q = await add_question(comp_id, body, answer_text, None, q_date)
    except ValueError as e:
        # Duplicate question for this competition/date
        await update.message.reply_text(f'Невозможно добавить вопрос: {str(e)}')
        context.user_data['add_q_step'] = None
        return

    # Get competition name and notify user bot
    async with async_session() as session:
        comp = await session.get(Competition, comp_id)
        if comp:
            await update.message.reply_text(f'Вопрос добавлен для турнира "{comp.name}" на {q.date}')
            await notify_user_bot_webhook(comp_id, comp.name)
        else:
            await update.message.reply_text(f'Вопрос добавлен на {q.date}')
    context.user_data['add_q_step'] = None

async def add_question_handle_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Route message to the correct step handler based on state
    step = context.user_data.get('add_q_step')
    if step == 'body':
        await add_question_ask_answer(update, context)
        return
    if step == 'answer':
        await add_question_save(update, context)
        return


async def refill_pool_job(context):
    current = await get_unasked_pool_count()
    if current >= 5000:
        logging.info(f'Pool has sufficient questions: {current} >= {5000}')
        return
    
    added = 0
    present_packs = await get_all_present_packs()
    async with aiohttp.ClientSession() as session:
        main_html = await fetch(session, PARSER_BASE_URL)
        if not main_html:
            logging.error('Failed to fetch main parser page for pool refill')
            return
        packs_links = get_packs_by_complexity_sync(main_html)
        for pack_url in packs_links:
            if pack_url in present_packs:
                continue
            html = await fetch(session, PARSER_BASE_URL + pack_url)
            if not html:
                continue
            parsed = parse_pack_questions(html)
            logging.info(f'Parsed {len(parsed)} questions from {pack_url} for pool refill')
            for q in parsed:
                try:
                    await add_pool_question(
                        q['body'], q.get('answer'), q.get('handout'), q.get('comment'),
                        q.get('image_path'),  q.get('authors'),  q.get('tournaments'), pack_url
                        )
                    added += 1
                except Exception:
                    logging.exception(f'Failed to add pool question from {pack_url}')

    new_count = await get_unasked_pool_count()
    msg = f'Автозаполнение пула завершено: добавлено {added} вопросов. Текущий размер пула: {new_count}.'
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
        except Exception as e:
            logging.error(f'Failed to notify admin about pool refill: {e}')


async def distribute_random_questions_job(context: ContextTypes.DEFAULT_TYPE):
    when = datetime.now(MSK).date()
    async with async_session() as session:
        res = await session.execute(
            select(Competition).where(Competition.start_date <= when, Competition.end_date >= when, Competition.competition_type == 0)
        )
        comps = res.scalars().all()

    for comp in comps:
        q = await get_todays_question_for_competition(comp.id, when)
        if q:
            continue

        pool_q = await get_weighted_random_pool_question_and_mark_used()
        if not pool_q:
            logging.info('Pool empty, cannot assign question for %s', comp.name)
            continue
        answer = pool_q.answer + '\n\n' + 'Комментарий:\n' + pool_q.comment
        body = pool_q.body
        if pool_q.handout:
            body = "Раздаточный материал:\n" + pool_q.handout + '\n\n' + body
        try:
            await add_question(comp.id, body, answer, pool_q.image_path, when, pool_q.id)
        except Exception as e:
            logging.exception('Failed to add pooled question to competition %s: %s', comp.id, e)
            continue

        await notify_user_bot_webhook(comp.id, comp.name)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("create_competition", "Создать турнир (админ). Формат: /create_competition название|YYYY-MM-DD|YYYY-MM-DD"),
        BotCommand("send_random_question", "Отправить вопрос руками а не джобой"),
        BotCommand("add_question", "Добавить вопрос к турниру (админ)."),
    ])
    application.job_queue.run_daily(refill_pool_job, time=time(hour=9, minute=0, tzinfo=MSK))
    application.job_queue.run_daily(distribute_random_questions_job, time=time(hour=11, minute=00, tzinfo=MSK))


def main():
    request = HTTPXRequest(
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=10.0,
        pool_timeout=5.0,
        media_write_timeout=60.0,
    )

    application = ApplicationBuilder(
    ).token(
        os.environ.get("ADMIN_BOT_TOKEN")
    ).request(
        request
    ).post_init(post_init).build()
    application.add_handler(CommandHandler('create_competition', admin_create_competition))
    application.add_handler(CommandHandler('send_random_question', admin_send_random_question))
    application.add_handler(CommandHandler('add_question', admin_add_question))
    
    # Callback handlers for add_question flow
    application.add_handler(CallbackQueryHandler(add_question_select_competition, pattern='^add_q_comp:'))
    
    # Message handlers for add_question multi-step
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_handle_step))

    application.run_polling()


if __name__ == "__main__":
    main()
