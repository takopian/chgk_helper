import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from datetime import datetime, time, date
import pytz
from db.usage import (
    create_competition,
    add_question,
    register_user,
    get_or_create_user,
    get_todays_question_for_competition,
    record_answer,
)
from sqlalchemy import select

ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '0'))  # replace with real admin chat id
PERMITTED_ADMIN_TG_ID = int(os.environ.get('PERMITTED_ADMIN_TG_ID', '0'))
MSK = pytz.timezone('Europe/Moscow')


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
    from db.usage import async_session
    from db.models import Competition

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
        await update.message.reply_text(f'Невозможно добавить вопрос: {str(e)}')
        context.user_data['add_q_step'] = None
        return

    # Get competition name
    from db.usage import async_session
    from db.models import Competition
    async with async_session() as session:
        comp = await session.get(Competition, comp_id)
        comp_name = comp.name if comp else 'неизвестный турнир'
    await update.message.reply_text(f'Вопрос добавлен для турнира "{comp_name}" на {q.date}')
    context.user_data['add_q_step'] = None


async def user_register_competition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Step 1: Show list of active competitions as buttons
    from db.usage import async_session
    from db.models import Competition

    when = datetime.now(MSK).date()
    async with async_session() as session:
        res = await session.execute(
            select(Competition).where(Competition.start_date <= when, Competition.end_date >= when)
        )
        active_comps = res.scalars().all()

    if not active_comps:
        await update.message.reply_text('Нет активных турниров.')
        return

    keyboard = [[InlineKeyboardButton(c.name, callback_data=f'reg_comp:{c.id}')] for c in active_comps]
    reply_markup = InlineKeyboardMarkup(keyboard)
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
    from db.usage import async_session
    from db.models import Competition
    async with async_session() as session:
        comp = await session.get(Competition, comp_id)
        comp_name = comp.name if comp else 'неизвестный турнир'
    await query.edit_message_text(f'Вы зарегистрировались на турнир "{comp_name}"')


async def submit_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # New flow: user issues /submit_answer -> bot finds today's question for user's registrations,
    # sends the question and waits for the user's text reply.
    # If there is no question for today, inform the user.
    # find or create local user record
    user = await get_or_create_user(update.effective_user.id, update.effective_user.username or '')

    # find user's registered competitions and today's question (using MSK timezone)
    from db.usage import async_session
    from db.models import UsersRegistrations, Answer as AnswerModel

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
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('✓ Правильно', callback_data=f'validate:correct:{recorded.id}'), InlineKeyboardButton('✗ Неправильно', callback_data=f'validate:wrong:{recorded.id}')]])
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f'Пользователь {update.effective_user.id} ответил на вопрос {qid}: "{ans_text}"', reply_markup=keyboard)
    
    # Get and show the correct answer
    from db.usage import async_session
    from db.models import Question
    async with async_session() as session:
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
    from db.usage import async_session
    from db.models import Answer as AnswerModel

    async with async_session() as session:
        a = await session.get(AnswerModel, ans_id)
        if a:
            a.is_correct = is_correct
            session.add(a)
            await session.commit()

    status_text = 'правильно' if which == 'correct' else 'неправильно'
    await query.edit_message_text(f'Ответ отмечен как {status_text}')


async def send_daily_questions(context: ContextTypes.DEFAULT_TYPE):
    # iterate competitions active today, send today's question to their registered users
    from db.usage import async_session
    from db.models import Competition, UsersRegistrations, User

    when = datetime.utcnow().date()
    async with async_session() as session:
        # find competitions active today
        res = await session.execute(
            select(Competition).where(Competition.start_date <= when, Competition.end_date >= when)
        )
        comps = res.scalars().all()
        for comp in comps:
            q = await get_todays_question_for_competition(comp.id, when)
            if not q:
                continue
            # find registered users
            regs = await session.execute(select(UsersRegistrations).where(UsersRegistrations.competition_id == comp.id))
            regs = regs.scalars().all()
            for r in regs:
                user = await session.get(User, r.user_id)
                if user and user.tg_id:
                    await context.bot.send_message(chat_id=user.tg_id, text=f"Вопрос для {comp.name}:\n{q.body}")


async def add_question_handle_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Route message to the correct step handler based on state
    # prioritize add_question flow
    step = context.user_data.get('add_q_step')
    if step == 'body':
        await add_question_ask_answer(update, context)
        return
    if step == 'answer':
        await add_question_save(update, context)
        return

    # then check submit flow
    submit_step = context.user_data.get('submit_step')
    if submit_step == 'await_answer':
        await submit_answer_save(update, context)
        return


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show leaderboard: users ranked by correct answer count
    from db.usage import async_session
    from db.models import User, Answer as AnswerModel
    from sqlalchemy import func

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

    # Build leaderboard message
    msg = '🏆 Рейтинг 🏆\n\n'
    for idx, (username, count) in enumerate(leaderboard_data, 1):
        medal = '🥇' if idx == 1 else '🥈' if idx == 2 else '🥉' if idx == 3 else f'{idx}.'
        msg += f'{medal} {username}: {count}\n'

    await update.message.reply_text(msg)


def register_handlers(application):
    application.add_handler(CommandHandler('create_competition', admin_create_competition))
    application.add_handler(CommandHandler('add_question', admin_add_question))
    application.add_handler(CommandHandler('register_competition', user_register_competition))
    application.add_handler(CommandHandler('submit_answer', submit_answer))
    application.add_handler(CommandHandler('leaderboard', leaderboard))
    
    # Callback handlers for add_question flow
    application.add_handler(CallbackQueryHandler(add_question_select_competition, pattern='^add_q_comp:'))
    application.add_handler(CallbackQueryHandler(register_competition_callback, pattern='^reg_comp:'))
    application.add_handler(CallbackQueryHandler(validate_callback, pattern='^validate:'))
    
    # Message handlers for add_question multi-step (check state before processing)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_question_handle_step))
