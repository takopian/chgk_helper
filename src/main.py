import logging
import os
import uuid
from datetime import datetime, time, timedelta
import pytz

import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, CallbackQueryHandler, Application

from parser import parse_quizzes
from quiz import PollsData
from utils import read_yaml, write_yaml

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

QUESTION_HOUR = 8
QUESTION_MINUTE = 45


async def register(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    polls_data = PollsData.load()
    chat_data = polls_data.get_or_create_chat_data(chat_id)
    if not chat_data.poll_quizzes:
        await update.message.reply_text(
            "Прежде чем отмечать зарегистрированные турниры,"
            " нужно хоть раз выполнить команду /createpoll."
        )
        return
    registered = [
        quiz.poll_text for quiz in chat_data.registered_quizzes
    ]
    today = datetime.utcnow().date()
    options = [
        quiz for quiz in chat_data.poll_quizzes
        if quiz.poll_text not in registered and quiz.date.date() >= today
    ]
    keyboard = [
        [
            InlineKeyboardButton(quiz.poll_text, callback_data=f'register:{quiz.id}')
        ]
        for quiz in options
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        'Выбери игру, на которую ты зарегал команду:',
        reply_markup=reply_markup
    )


async def handle_register(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    selected_option = query.data
    quiz_id = selected_option[9:]
    logging.info(f"User selected option {selected_option}")

    chat_id = update.effective_chat.id
    polls_data = PollsData.load()
    chat_data = polls_data.get_or_create_chat_data(chat_id)
    quiz = next((quiz for quiz in chat_data.poll_quizzes if quiz.id == quiz_id))
    chat_data.registered_quizzes.append(quiz)
    polls_data.save()
    await query.edit_message_text(
        text=f"Зарегистрировались на игру: {quiz.poll_text}"
    )


async def get_registered(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    polls_data = PollsData.load()
    chat_data = polls_data.get_or_create_chat_data(chat_id)
    upcoming_games = [
        quiz.poll_text for quiz in sorted(chat_data.registered_quizzes, key=lambda x: x.date)
        if quiz.date.date() >= datetime.utcnow().date()
    ]
    if not upcoming_games:
        await update.message.reply_text("Нет зарегистрированных игр.")
        return
    formatted_list = "\n".join([f"- {quiz}" for quiz in upcoming_games])
    await update.message.reply_text(f"Зарегистрированные игры:\n{formatted_list}.")


async def notify_registered(context):
    today = datetime.utcnow().date()
    polls_data = PollsData.load()
    for chat_id, chat_data in polls_data.chats_data.items():
        for quiz in chat_data.registered_quizzes:
            if quiz.date.date() == today:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Напоминаю: что сегодня состоится игра:\n {quiz.poll_text}",
                    )
                except Exception as e:
                    print(f"Failed to send message to chat {chat_id}: {e}")


async def create_poll(update: Update, context) -> None:
    url = "https://chgk-spb.livejournal.com/"
    response = requests.get(url)
    quizzes = parse_quizzes(response.text)
    chat_id = update.effective_chat.id

    polls_data = PollsData.load()
    chat_data = polls_data.get_or_create_chat_data(chat_id)

    new_quizzes = []
    old_quizzes = set(chat.poll_text for chat in chat_data.poll_quizzes)
    # difficulty_tasks = []
    for quiz in quizzes:
        if quiz.poll_text in old_quizzes:
            logging.info(f"Already voted on {quiz.poll_text}")
            continue
        new_quizzes.append(quiz)
        quiz.id = uuid.uuid4().hex
        # difficulty_tasks.append(get_difficulty(quiz.url))

    if not new_quizzes:
        await update.message.reply_text("Нет новых игр.")
        return

    logging.info(f"Got {len(new_quizzes)} new quizzes.")

    # difficulties = await asyncio.gather(*difficulty_tasks)
    # for quiz, difficulty in zip(new_quizzes, difficulties):
    #     quiz.difficulty = difficulty

    poll_size = 10 if len(new_quizzes) % 10 != 1 else 9
    polls = [
        [f"{quiz.poll_text[:90]}, с-ь ?" for quiz in new_quizzes[i: i + poll_size]]
        for i in range(0, len(new_quizzes), poll_size)
    ]
    for options in polls:
        await update.message.reply_poll(
            question="Господа и дамы.",
            options=options,
            is_anonymous=False,
            allows_multiple_answers=True
        )

    chat_data.poll_quizzes.extend(new_quizzes)
    polls_data.save()


async def start_advent(update: Update, context):
    chat_id = str(update.effective_chat.id)
    active_chats = read_yaml("advent_chats.yml") or {}
    if chat_id not in active_chats:
        active_chats[chat_id] = {"user_name": update.effective_user.name}
        write_yaml("advent_chats.yml", active_chats)
        await update.message.reply_text("ЧГК адвент активирован! Вопросы будут присылаться каждый день в 20:00 на протяжении 25 дней. Чтобы ответить на сегодняшний вопрос воспользуйтесь командой /advent_answer.")
        await send_to_one(context, chat_id)
    else:
        await update.message.reply_text("ЧГК адвент уже активирован дубина ты стоеросовая!")


async def send_to_one(context, chat_id):
    questions = read_yaml("advent_questions.yml")
    now = datetime.utcnow()
    if now.hour < QUESTION_HOUR:
        today = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        today = now.strftime('%Y-%m-%d')

    question = questions.get(today, {})
    text = question.get("question", "Кто-то забыл задать сегодняшний вопрос(((")
    try:
        await context.bot.send_message(chat_id=int(chat_id), text=f"ВОПРОС ДНЯ!!!\n{text}")
    except Exception as e:
        print(f"Failed to send message to chat {chat_id}: {e}")


async def send_quiz_question(context):
    active_chats = read_yaml("advent_chats.yml")
    questions = read_yaml("advent_questions.yml")
    now = datetime.utcnow()
    today = now.strftime('%Y-%m-%d')
    question = questions.get(today, {})
    text = question.get("question", "Кто-то забыл задать сегодняшний вопрос(((")
    image_path = question.get("image")
    for chat_id in active_chats:
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=f"Рубрика вопрос от подписчика:\n{text}")
            if image_path:
                await context.bot.send_photo(chat_id=int(chat_id), photo=open(image_path, 'rb'))
        except Exception as e:
            print(f"Failed to send message to chat {chat_id}: {e}")


async def send_quiz_answers(context):
    active_chats = read_yaml("advent_chats.yml")
    questions = read_yaml("advent_questions.yml")
    now = datetime.utcnow()
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    question = questions.get(yesterday, {})
    for chat_id in active_chats:
        if question and not active_chats[chat_id].get("answers", {}).get(yesterday):
            try:
                await context.bot.send_message(chat_id=int(chat_id), text=f"Правильный ответ на вчерашний вопрос:\n {question['answer']}")
            except Exception as e:
                print(f"Failed to send message to chat {chat_id}: {e}")


async def answer(update: Update, context):
    chat_id = str(update.effective_chat.id)
    now = datetime.utcnow()
    if now.hour < QUESTION_HOUR:
        today = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        today = now.strftime('%Y-%m-%d')

    if len(context.args) == 0:
        await update.message.reply_text("Не хватает ответа. Использование: /advent_answer ваш ответ")
        return

    chat_data = read_yaml("advent_chats.yml") or {}
    questions = read_yaml("advent_questions.yml") or {}
    if not questions.get(today):
        await update.message.reply_text("Сегодняшний вопрос еще не был задан.")
        return
    if chat_id not in chat_data:
        await update.message.reply_text("Сначала используй /advent_start.")
        return

    ans = chat_data[chat_id].get("answers", {})
    if today in ans:
        await update.message.reply_text("Сегодняшний вопрос уже был отвечен!")
        return

    answer = " ".join(context.args)
    ans[today] = {"answer": answer, "is_correct": None}
    chat_data[chat_id]["answers"] = ans
    write_yaml("advent_chats.yml", chat_data)

    await update.message.reply_text("Ответ записан! ✅", reply_to_message_id=update.message.message_id)
    await update.message.reply_text(f"Правильный ответ:\n {questions[today]['answer']}", reply_to_message_id=update.message.message_id)


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("createpoll", "Создать опрос на базе последнего анонса игр."),
        BotCommand("register", "Отметить игру, на которую была произведена регистрация."),
        BotCommand("upcoming", "Получить список игр, на которые была произведена регистрация."),
        BotCommand("advent_start", "Жми!!!"),
        BotCommand("advent_answer", "Ответить на вопрос. Зажмите кнопку, чтобы кайфануть"),
    ])


def main():
    application = ApplicationBuilder(
    ).token(
        os.environ.get("BOT_TOKEN"),
    ).post_init(post_init).build()
    application.add_handler(CommandHandler("createpoll", create_poll))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("upcoming", get_registered))
    application.add_handler(CallbackQueryHandler(handle_register, pattern='^register:'))

    application.add_handler(CommandHandler("advent_start", start_advent))
    application.add_handler(CommandHandler("advent_answer", answer))
    application.job_queue.run_daily(notify_registered, time=time(hour=7, minute=0, tzinfo=pytz.utc))
    # application.job_queue.run_daily(send_quiz_answers, time=time(hour=QUESTION_HOUR, minute=QUESTION_MINUTE, tzinfo=pytz.utc))
    # application.job_queue.run_daily(send_quiz_question, time=time(hour=QUESTION_HOUR, minute=QUESTION_MINUTE + 1, tzinfo=pytz.utc))

    application.run_polling()


if __name__ == "__main__":
    main()
