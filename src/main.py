import asyncio
import logging
import os
import uuid
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, CallbackQueryHandler, \
    Application

from parser import parse_quizzes, get_difficulty
from quiz import PollsData

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


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
    options = [
        quiz for quiz in chat_data.poll_quizzes
        if quiz.poll_text not in registered and quiz.date > datetime.utcnow()
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
        quiz.poll_text for quiz in chat_data.registered_quizzes
        if quiz.date > datetime.utcnow()
    ]
    if not upcoming_games:
        await update.message.reply_text("Нет зарегистрированных игр.")
        return
    formatted_list = "\n".join([f"- {quiz}" for quiz in upcoming_games])
    await update.message.reply_text(f"Зарегистрированные игры:\n{formatted_list}.")


async def create_poll(update: Update, context) -> None:
    url = "https://chgk-spb.livejournal.com/"
    response = requests.get(url)
    quizzes = parse_quizzes(response.text)
    chat_id = update.effective_chat.id

    polls_data = PollsData.load()
    chat_data = polls_data.get_or_create_chat_data(chat_id)

    new_quizzes = []
    old_quizzes = set(chat.poll_text for chat in chat_data.poll_quizzes)
    difficulty_tasks = []
    for quiz in quizzes:
        if quiz.poll_text in old_quizzes:
            logging.info(f"Already voted on {quiz.poll_text}")
            continue
        new_quizzes.append(quiz)
        quiz.id = uuid.uuid4().hex
        difficulty_tasks.append(get_difficulty(quiz.url))

    if not new_quizzes:
        await update.message.reply_text("Нет новых игр.")
        return

    logging.info(f"Got {len(new_quizzes)} new quizzes.")

    difficulties = await asyncio.gather(*difficulty_tasks)
    for quiz, difficulty in zip(new_quizzes, difficulties):
        quiz.difficulty = difficulty

    poll_size = 10 if len(new_quizzes) % 10 != 1 else 9
    polls = [
        [f"{quiz.poll_text}, сложность {quiz.difficulty}" for quiz in new_quizzes[i: i + poll_size]]
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


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("createpoll", "Создать опрос на базе последнего анонса игр."),
        BotCommand("register", "Отметить игру, на которую была произведена регистрация."),
        BotCommand("upcoming", "Получить список игр, на которые была произведена регистрация."),
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

    application.run_polling()


if __name__ == "__main__":
    main()
