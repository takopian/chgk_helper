import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime

from utils import datetime_serializer


@dataclass
class Quiz:
    poll_text: str
    url: str = None
    difficulty: float = None
    date: datetime = None
    id: str = None

    @property
    def poll_string(self):
        return f"{self.poll_text}, сложность {self.difficulty}"

    def __post_init__(self):
        self.date = datetime.fromisoformat(self.date) if isinstance(self.date, str) else self.date


@dataclass
class ChatData:
    poll_quizzes: list[Quiz] = field(default_factory=list)
    registered_quizzes: list[Quiz] = field(default_factory=list)

    def __post_init__(self):
        self.poll_quizzes = [
            Quiz(**quiz) for quiz in self.poll_quizzes
            if isinstance(quiz, dict)
        ]
        self.registered_quizzes = [
            Quiz(**quiz) for quiz in self.registered_quizzes
            if isinstance(quiz, dict)
        ]


@dataclass
class PollsData:
    chats_data: dict[int, ChatData] = field(default_factory=dict)

    def __post_init__(self):
        self.chats_data = {
            int(key): ChatData(**val)
            for key, val in self.chats_data.items()
            if isinstance(val, dict)
        }

    def get_or_create_chat_data(self, chat_id: int) -> ChatData:
        chat_data = self.chats_data.get(chat_id)
        if chat_data is None:
            chat_data = ChatData()
            self.chats_data[chat_id] = chat_data
        return chat_data

    @classmethod
    def load(cls) -> "PollsData":
        try:
            with open('last.txt', 'r') as file:
                return cls(chats_data=json.loads(file.read()))
        except FileNotFoundError:
            logging.info("Did not find poll data in last.txt")
            return PollsData()
        except Exception:
            logging.exception("Failed to load poll data", exc_info=True)
            return PollsData()

    def dict(self) -> dict:
        return {
            str(key): asdict(val)
            for key, val in self.chats_data.items()
        }

    def save(self) -> None:
        with open('last.txt', 'w') as file:
            file.write(
                json.dumps(
                    self.dict(), default=datetime_serializer, ensure_ascii=False
                )
            )

