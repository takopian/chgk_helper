import logging

import aiohttp
from bs4 import BeautifulSoup
from datetime import datetime

from quiz import Quiz
from utils import with_locale, FORMAT


async def get_difficulty(link):
    async with aiohttp.ClientSession() as session:
        async with session.get(link) as response:
            html = await response.text()

        soup = BeautifulSoup(html, 'html.parser')
        article = soup.find('article').find('article')
        announce = article.get_text('\n')

        prompt = f"""
            Я покажу тебе анонс турнира, тебе нужно в качестве ответа выдать 
            мне единственное число, являющееся сложностью данного турнира.
            Ответ должен содержать только число, в нем не должно быть никаких дополнительных символов.
            Это число должно равняться заявленной сложности турнира.
            Вот искомый анонс:
            {announce}
        """

        async with session.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "llama3.1",
                    "stream": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                }
        ) as gpt_response:
            gpt_response_json = await gpt_response.json()

    return float(gpt_response_json['message']['content'].replace(",", "."))


@with_locale('ru_RU.UTF-8')
def parse_date(date_str: str) -> datetime:
    logging.info(date_str)
    date_str = " ".join(date_str.split()[:-1])
    return datetime.strptime(date_str + " 2024", FORMAT)


def parse_quizzes(html) -> list[Quiz]:
    soup = BeautifulSoup(html, 'html.parser')
    entry_content = soup.find('div', class_='entry-content')
    entry_body = entry_content.find('div', class_='entry-body').find('p')
    quizzes = []

    current_date = None
    prev_is_br = False
    for element in entry_body.children:
        if element.name == 'b':
            current_date = element.get_text(strip=True)
        elif element.name == 'a' and current_date:
            url = element.attrs['href']
            quiz_text = element.get_text(strip=True)
            quizzes.append(
                Quiz(
                    f"{current_date} {quiz_text}",
                    url=url,
                    date=parse_date(current_date),
                )
            )
        elif element.name == 'br':
            if prev_is_br:
                current_date = None
            else:
                prev_is_br = True
            continue
        prev_is_br = False

    return quizzes
