import logging
import os
from datetime import date, time, datetime
import pytz
import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ApplicationBuilder, Application

from db.usage import (
    create_competition,
    add_question,
    async_session,
)
from db.models import Competition, Answer as AnswerModel
from sqlalchemy import select

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
PERMITTED_ADMIN_TG_ID = int(os.environ.get('PERMITTED_ADMIN_TG_ID', '0'))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'http://user_bot:8001/webhook/question')
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


async def admin_create_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # permission check
    if not update.effective_user or update.effective_user.id != PERMITTED_ADMIN_TG_ID:
        await update.message.reply_text('Вы не авторизованы для использования этой команды.')
        return
    # expected: /create_competition name|YYYY-MM-DD|YYYY-MM-DD
    if not context.args:
        await update.message.reply_text('Использование: /create_competition название|YYYY-MM-DD|YYYY-MM-DD')
        return
    payload = ' '.join(context.args)
    parts = payload.split('|')
    if len(parts) != 3:
        await update.message.reply_text('Использование: /create_competition название|YYYY-MM-DD|YYYY-MM-DD')
        return
    name, start_s, end_s = parts
    start_date = date.fromisoformat(start_s.strip())
    end_date = date.fromisoformat(end_s.strip())
    comp = await create_competition(name.strip(), start_date, end_date)
    await update.message.reply_text(f'Турнир "{comp.name}" создан')


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


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("create_competition", "Создать турнир (админ). Формат: /create_competition название|YYYY-MM-DD|YYYY-MM-DD"),
        BotCommand("add_question", "Добавить вопрос к турниру (админ)."),
    ])


def main():
    application = ApplicationBuilder(
    ).token(
        os.environ.get("ADMIN_BOT_TOKEN")
    ).post_init(post_init).build()
    application.add_handler(CommandHandler('create_competition', admin_create_competition))
    application.add_handler(CommandHandler('add_question', admin_add_question))
    
    # Callback handlers for add_question flow
    application.add_handler(CallbackQueryHandler(add_question_select_competition, pattern='^add_q_comp:'))
    
    # Message handlers for add_question multi-step
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_handle_step))

    application.run_polling()


if __name__ == "__main__":
    main()
