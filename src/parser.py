import logging

import aiohttp
import re
import json
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
                "http://ollama:11434/api/generate",
                json={
                    "model": "llama3.1",
                    "stream": False,
                    "prompt": prompt
                }
        ) as gpt_response:
            gpt_response_json = await gpt_response.json()

    return float(gpt_response_json['response'].replace(",", "."))


@with_locale('ru_RU.UTF-8')
def parse_date(date_str: str) -> datetime:
    logging.info(date_str)
    date_str = " ".join(date_str.split()[:-1])
    year = datetime.now().year
    return datetime.strptime(date_str + f" {str(year)}", FORMAT)


def parse_quizzes(html) -> list[Quiz]:
    soup = BeautifulSoup(html, 'html.parser')
    entry_content = soup.find('div', class_='entry-content')
    entry_body = entry_content.find('div', class_='entry-body')
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


def get_packs_by_complexity_sync(html: str, low: float = 2.5, high: float = 5.0) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    result = []

    cards = soup.find_all("div", class_="break-words")
    for card in cards:
        pack_link = card.find("a", href=re.compile(r"^/pack/\d+"))
        if not pack_link:
            continue

        complexity_span = None
        for span in card.find_all("span"):
            if "сложность" in span.get_text(strip=True).lower():
                complexity_span = span
                break

        if not complexity_span:
            continue

        complexity_text = ""
        for sibling in complexity_span.find_next_siblings():
            complexity_text += sibling.get_text(" ", strip=True) + " "

        numbers = re.findall(r"\d+(?:\.\d+)?", complexity_text)
        for num_str in numbers:
            try:
                val = float(num_str.replace(",", "."))
            except ValueError:
                continue
            if low <= val <= high:
                result.append(pack_link["href"])
                break
    return result


def parse_pack_questions(html: str):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    source_pack = h1.get_text(strip=True) if h1 else None

    questions = []
    raw_questions = []
    for script in soup.find_all("script"):
        if not script.string:
            continue
        # Try to find JSON array embedded as in test.py
        matches = re.findall(r'"questions\\"\s*:\s*(\[.*?\}\}\]\}\]\})', script.string, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                match = match.replace('\\"', '"')[:-1]
                qs = json.loads(match)
                if isinstance(qs, list):
                    raw_questions.extend(qs)
            except Exception:
                continue
    for q in raw_questions:
        questions.append({
            "body": q.get("text", ""),
            "handout": q.get("razdatkaText", None),
            "answer": q.get("answer", ""),
            "image_path": q.get("razdatkaPic", None),
            "comment": q.get("comment", None),
        })
    return questions
