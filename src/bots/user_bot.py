import logging
import os
from datetime import date, time, datetime
import pytz
import aiohttp
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ApplicationBuilder, Application

from db.usage import (
    register_user,
    get_or_create_user,
    get_todays_question_for_competition,
    record_answer,
    async_session,
)
from db.models import Competition, UsersRegistrations, User, Answer as AnswerModel
from sqlalchemy import select, func

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))
PARSER_BASE_URL = os.environ.get('PARSER_BASE_URL', '')
MSK = pytz.timezone('Europe/Moscow')


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a start message with guidelines."""
    guideline = """ Привет любителям пощекотать мозги!(bruh)

**Как пользоваться ботом:**

1️⃣ **Регистрация на турнир** — `/register_competition`
   Выбери турнир из предложенного списка

2️⃣ **Ответить на вопрос** — `/submit_answer`
   Команда возвращает вопрос дня. Отправьте свой ответ текстовым сообщением.
   Каждый день 1 новый вопрос. Вопросы постараюсь добавлять до 12:00 МСК.
   На вопрос можно ответить только единожды.

3️⃣ **Посмотреть рейтинг** — `/leaderboard`
   Увидите список участников с наибольшим количеством правильных ответов

**Хороших вам раскрутов!** """
    
    await update.message.reply_text(guideline, parse_mode='Markdown')


async def user_register_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 1: Show list of active competitions as buttons
    when = datetime.now(MSK).date()

    # Ensure we have a user record to check registrations
    user = await get_or_create_user(update.effective_user.id, update.effective_user.username or '')

    async with async_session() as session:
        res = await session.execute(
            select(Competition).where(Competition.start_date <= when, Competition.end_date >= when)
        )
        active_comps = res.scalars().all()

        # Fetch user's existing registrations
        regs_res = await session.execute(
            select(UsersRegistrations).where(UsersRegistrations.user_id == user.id)
        )
        regs = regs_res.scalars().all()
        registered_comp_ids = {r.competition_id for r in regs}

    if not active_comps:
        await update.message.reply_text('Нет активных турниров.')
        return

    # Filter out competitions the user already registered for
    unregistered = [c for c in active_comps if c.id not in registered_comp_ids]

    if not unregistered:
        # User is registered for all active competitions
        registered_names = ', '.join([c.name for c in active_comps if c.id in registered_comp_ids])
        if registered_names:
            await update.message.reply_text(f'Вы уже зарегистрированы на активные турниры: {registered_names}')
        else:
            await update.message.reply_text('Вы уже зарегистрированы на все активные турниры.')
        return

    keyboard = [[InlineKeyboardButton(c.name, callback_data=f'reg_comp:{c.id}')] for c in unregistered]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if registered_comp_ids:
        registered_names = ', '.join([c.name for c in active_comps if c.id in registered_comp_ids])
        await update.message.reply_text(f'Выберите турнир для регистрации:\n(Уже зарегистрированы: {registered_names})', reply_markup=reply_markup)
    else:
        await update.message.reply_text('Выберите турнир для регистрации:', reply_markup=reply_markup)


async def register_competition_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 2: User selected a competition, register them
    query = update.callback_query
    await query.answer()
    comp_id = int(query.data.split(':')[1])
    tg_id = query.from_user.id
    username = query.from_user.username or ''
    # Register for competition
    reg = await register_user(tg_id, username, comp_id)
    # Get competition name
    async with async_session() as session:
        comp = await session.get(Competition, comp_id)
        comp_name = comp.name if comp else 'неизвестный турнир'
    await query.edit_message_text(f'Вы зарегистрировались на турнир "{comp_name}"')


async def submit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update.effective_user.id, update.effective_user.username or '')

    when = datetime.now(MSK).date()
    q_found = None
    async with async_session() as session:
        res = await session.execute(select(UsersRegistrations).where(UsersRegistrations.user_id == user.id))
        regs = res.scalars().all()
        for r in regs:
            q = await get_todays_question_for_competition(r.competition_id, when)
            if q:
                q_found = q
                break

    if not q_found:
        await update.message.reply_text('Вопрос еще не задан.')
        return

    # Check if user has already answered today's question
    async with async_session() as session:
        existing_answer = await session.execute(
            select(AnswerModel).where(
                AnswerModel.user_id == user.id,
                AnswerModel.question_id == q_found.id
            )
        )
        if existing_answer.scalars().first():
            await update.message.reply_text('Вы уже ответили на этот вопрос.')
            return

    # send today's question and set state to await answer
    if q_found.image_path:
        async with aiohttp.ClientSession() as session:
            async with session.get(PARSER_BASE_URL +q_found.image_path) as resp:
                if resp.status != 200:
                    await update.message.reply_text('Ошибка при загрузке изображения вопроса.')
                    return
                await update.message.reply_photo(photo=await resp.read())
    await update.message.reply_text(f"Вопрос дня:\n{q_found.body}")
    context.user_data['submit_q_id'] = q_found.id
    context.user_data['submit_step'] = 'await_answer'


async def submit_answer_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle the user's textual answer when waiting for it
    if context.user_data.get('submit_step') != 'await_answer':
        return
    qid = context.user_data.get('submit_q_id')
    if not qid:
        return
    ans_text = update.message.text
    # find user by tg id
    user = await get_or_create_user(update.effective_user.id, update.effective_user.username or '')
    recorded = await record_answer(user.id, qid, ans_text)
    # notify admin for manual validation
    if ADMIN_CHAT_ID:
        username = update.effective_user.username or update.effective_user.id
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('✓ Правильно', callback_data=f'validate:correct:{recorded.id}'), InlineKeyboardButton('✗ Неправильно', callback_data=f'validate:wrong:{recorded.id}')]])
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f'Пользователь {username} ответил на вопрос {qid}: "{ans_text}"', reply_markup=keyboard)
    
    # Get and show the correct answer
    async with async_session() as session:
        from db.models import Question
        question = await session.get(Question, qid)
        if question:
            correct_answer_msg = f'Ваш ответ: "{ans_text}"\n\n✅ Правильный ответ: "{question.answer}"'
        else:
            correct_answer_msg = f'Ваш ответ: "{ans_text}"'
    
    await update.message.reply_text(correct_answer_msg)
    # clear state
    context.user_data['submit_step'] = None
    context.user_data['submit_q_id'] = None


async def validate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    # format: validate:correct:answer_id or validate:wrong:answer_id
    parts = data.split(':')
    if len(parts) != 3:
        return
    verdict, which, ans_id = parts[0], parts[1], int(parts[2])
    is_correct = True if which == 'correct' else False
    # update Answer.is_correct in DB
    async with async_session() as session:
        a = await session.get(AnswerModel, ans_id)
        if a:
            a.is_correct = is_correct
            session.add(a)
            await session.commit()

    status_text = 'правильно' if which == 'correct' else 'неправильно'
    await query.edit_message_text(f'Ответ {ans_id} отмечен как {status_text}')


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show leaderboard: users ranked by correct answer count
    async with async_session() as session:
        # Query: count correct answers per user, ordered by count descending
        result = await session.execute(
            select(User.username, func.count(AnswerModel.id).label('correct_count'))
            .join(AnswerModel, User.id == AnswerModel.user_id)
            .where(AnswerModel.is_correct == True)
            .group_by(User.id, User.username)
            .order_by(func.count(AnswerModel.id).desc())
        )
        leaderboard_data = result.all()

    if not leaderboard_data:
        await update.message.reply_text('Еще нет правильных ответов.')
        return

    # Build leaderboard message with tied ranks
    msg = '🏆 Рейтинг 🏆\n\n'
    
    # Group users by score
    score_groups = {}
    for username, count in leaderboard_data:
        if count not in score_groups:
            score_groups[count] = []
        score_groups[count].append(username)
    
    # Sort by score descending and assign ranks
    rank = 1
    for score in sorted(score_groups.keys(), reverse=True):
        users = score_groups[score]
        
        # Determine medal
        if rank == 1:
            medal = '🥇'
        elif rank == 2:
            medal = '🥈'
        elif rank == 3:
            medal = '🥉'
        else:
            medal = f'{rank}.'
        
        # Format users with same score
        users_str = ', '.join(users)
        msg += f'{medal} {users_str}: {score}\n'
        
        # Next rank is current rank + number of users tied at this rank
        rank += len(users)

    await update.message.reply_text(msg)


async def user_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Route message to the correct step handler based on state
    submit_step = context.user_data.get('submit_step')
    if submit_step == 'await_answer':
        await submit_answer_save(update, context)
        return

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Показать справку и инструкции."),
        BotCommand("register_competition", "Зарегистрироваться на турнир."),
        BotCommand("submit_answer", "Ответить на вопрос дня."),
        BotCommand("leaderboard", "Показать рейтинг участников по правильным ответам."),
    ])


async def handle_question_webhook(request):
    """Webhook endpoint that receives question upload notifications from admin bot."""
    try:
        data = await request.json()
        competition_id = data.get('competition_id')
        competition_name = data.get('competition_name')
        
        if not competition_id or not competition_name:
            return web.Response(text='Missing competition_id or competition_name', status=400)
        
        # Get the telegram application from the request
        app = request.app['application']
        
        # Get users registered for this competition and send notifications
        async with async_session() as session:
            regs = await session.execute(
                select(UsersRegistrations).where(UsersRegistrations.competition_id == competition_id)
            )
            registrations = regs.scalars().all()
            
            for reg in registrations:
                user = await session.get(User, reg.user_id)
                if user and user.tg_id:
                    try:
                        await app.bot.send_message(
                            chat_id=user.tg_id,
                            text=f"📢 Вопрос к турниру '{competition_name}' опубликован!\n\n"
                                 f"Используйте команду /submit_answer чтобы увидеть вопрос и ответить на него."
                        )
                    except Exception as e:
                        logging.error(f"Failed to send notification to user {user.tg_id}: {e}")
        
        return web.Response(text='OK', status=200)
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return web.Response(text=f'Error: {str(e)}', status=500)


def main():
    application = ApplicationBuilder(
    ).token(
        os.environ.get("USER_BOT_TOKEN")
    ).post_init(post_init).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('register_competition', user_register_competition))
    application.add_handler(CommandHandler('submit_answer', submit_answer))
    application.add_handler(CommandHandler('leaderboard', leaderboard))
    
    # Callback handlers for registration
    application.add_handler(CallbackQueryHandler(register_competition_callback, pattern='^reg_comp:'))
    application.add_handler(CallbackQueryHandler(validate_callback, pattern='^validate:'))
    
    # Message handlers for submit_answer multi-step
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, user_message_handler))
    
    # Setup HTTP webhook server for admin bot notifications
    webhook_port = int(os.environ.get('WEBHOOK_PORT', '8001'))
    webhook_path = '/webhook/question'
    
    # Create aiohttp app for webhook
    webhook_app = web.Application()
    webhook_app['application'] = application
    webhook_app.router.add_post(webhook_path, handle_question_webhook)
    
    # Setup webhook server as a background task
    async def start_webhook_server(context):
        runner = web.AppRunner(webhook_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', webhook_port)
        await site.start()
        logging.info(f"Webhook server started on port {webhook_port}")
        # Store runner in context so we can clean it up later
        context.application.webhook_runner = runner
    
    # Run the webhook server once when app starts
    application.job_queue.run_once(start_webhook_server, when=0)
    
    # Run telegram polling with allow_reentry to prevent event loop issues
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
