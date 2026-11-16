import asyncio
import logging

import re
import json
from bs4 import BeautifulSoup
from datetime import datetime

from quiz import Quiz
from utils import with_locale, FORMAT, request_lifejournal


async def get_difficulty(link) -> str | None:
    await asyncio.sleep(0.1)  # To avoid overwhelming the server with requests
    html = await request_lifejournal(link)
    soup = BeautifulSoup(html, 'html.parser')
    entry = soup.find('div', class_='aentry-post__text aentry-post__text--view')
    if not entry:
        logging.error(f"Could not find difficulty for {link}")
        logging.error(html)
        return None
    text = entry.get_text(separator=" ", strip=True)
    match = re.search(r"TrueDL:\s*([\d.]+)", text)
    truedl = match.group(1) if match else None

    if not truedl:
        match = re.search(r"сложность:\s*([\d.]+)", text)
        truedl = match.group(1) if match else None

    return truedl


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


class GotQuestionsParseError(ValueError):
    """The page does not contain the expected GotQuestions data."""


def _flight_objects(html: str):
    """Decode Next.js data without executing page JavaScript.

    Transport chunks may split records. Text records use UTF-8 byte lengths,
    while ordinary records contain JSON and may reference other records.
    """
    soup = BeautifulSoup(html, "html.parser")
    decoder = json.JSONDecoder()
    chunks = []
    for script in soup.find_all("script"):
        text = script.string or ""
        for match in re.finditer(r"self\.__next_f\.push\(\s*", text):
            try:
                payload, _ = decoder.raw_decode(text, match.end())
            except ValueError as exc:
                raise GotQuestionsParseError("Invalid Next.js transport JSON") from exc
            if isinstance(payload, list) and len(payload) > 1 and payload[0] == 1:
                if isinstance(payload[1], str):
                    chunks.append(payload[1])
    stream = "".join(chunks).encode("utf-8")
    records = {}
    pos = 0
    while pos < len(stream):
        header = re.match(rb"([0-9a-f]*):", stream[pos:])
        if not header:
            raise GotQuestionsParseError("Invalid Next.js record header")
        key = header[1].decode()
        pos += header.end()
        if stream[pos:pos + 1] == b"T":
            size = re.match(rb"T([0-9a-f]+),", stream[pos:])
            if not size:
                raise GotQuestionsParseError("Invalid Next.js text record")
            pos += size.end()
            end = pos + int(size[1], 16)
            if end > len(stream):
                raise GotQuestionsParseError("Truncated Next.js text record")
            records[key] = stream[pos:end].decode("utf-8")
            pos = end
        else:
            end = stream.find(b"\n", pos)
            if end < 0:
                end = len(stream)
            try:
                records[key] = json.loads(stream[pos:end])
            except (ValueError, UnicodeDecodeError):
                pass  # Module, hint and other non-JSON Flight records.
            pos = end + 1

    def resolve(value, seen=frozenset()):
        if isinstance(value, str):
            if value.startswith("$$"):
                return value[1:]
            if re.fullmatch(r"\$[0-9a-f]+", value):
                key = value[1:]
                if key not in records or key in seen:
                    raise GotQuestionsParseError(f"Unresolved Next.js reference {value}")
                return resolve(records[key], seen | {key})
        elif isinstance(value, list):
            return [resolve(item, seen) for item in value]
        elif isinstance(value, dict):
            return {key: resolve(item, seen) for key, item in value.items()}
        return value

    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    # Resolve only domain objects, not React's component tree.
    objects = [obj for value in records.values() for obj in walk(value)]
    return soup, objects, resolve


def get_packs_by_complexity_sync(html: str, low: float = 2.5, high: float = 5.0) -> list[str]:
    soup, objects, resolve = _flight_objects(html)
    linked_ids = {int(m[1]) for a in soup.find_all("a", href=True)
                  if (m := re.fullmatch(r"/pack/(\d+)", a["href"]))}
    packs = {}
    for obj in objects:
        if obj.get("id") in linked_ids and "truedls" in obj and "title" in obj:
            pack = resolve(obj)
            packs[pack["id"]] = pack
    if not packs:
        raise GotQuestionsParseError("No package metadata found on GotQuestions listing")
    result = []
    for pack_id, pack in packs.items():
        for value in pack.get("truedls") or []:
            if value is None:
                continue
            try:
                difficulty = float(value)
            except (TypeError, ValueError) as exc:
                raise GotQuestionsParseError("Invalid package difficulty") from exc
            if low <= difficulty <= high:
                result.append(f"/pack/{pack_id}")
                break
    return result


def parse_pack_questions(html: str):
    _, objects, resolve = _flight_objects(html)
    packs = [resolve(obj) for obj in objects if "tours" in obj and "title" in obj and "id" in obj]
    if not packs:
        raise GotQuestionsParseError("No package with tours found on GotQuestions page")
    questions = []
    seen = set()
    for pack in packs:
        for tour in pack["tours"]:
            if not isinstance(tour.get("questions"), list):
                raise GotQuestionsParseError("Missing questions in package tour")
            for q in tour["questions"]:
                if q.get("takenDown"):
                    continue
                if q.get("id") in seen:
                    continue
                if not q.get("text") or not isinstance(q.get("answer"), str):
                    raise GotQuestionsParseError("Question text or answer is missing")
                if q.get("id") is not None:
                    seen.add(q["id"])
                authors = []
                for author in q.get("authors") or []:
                    person = author.get("person", author)
                    if not person.get("name"):
                        raise GotQuestionsParseError("Question author name is missing")
                    authors.append({"id": person.get("id"), "name": person["name"]})
                tournaments = []
                for tournament in q.get("tournaments") or pack.get("legacyTournaments") or []:
                    title = tournament.get("title") or tournament.get("name")
                    if not title:
                        raise GotQuestionsParseError("Tournament title is missing")
                    tournaments.append({"id": tournament.get("id"), "title": title})
                questions.append({
                    "body": q["text"],
                    "handout": q.get("razdatkaText") or None,
                    "answer": q["answer"],
                    "image_path": q.get("razdatkaPic") or None,
                    "comment": q.get("comment") or "",
                    "authors": authors,
                    "tournaments": tournaments,
                })
    if not questions:
        raise GotQuestionsParseError("No usable questions found in package")
    return questions
