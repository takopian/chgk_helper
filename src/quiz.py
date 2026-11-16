from dataclasses import dataclass
from datetime import datetime


@dataclass
class Quiz:
    poll_text: str
    url: str = None
    difficulty: str = None
    date: datetime = None
    id: str = None

    @property
    def poll_string(self):
        return f"{self.poll_text}, сложность {self.difficulty}"

    def __post_init__(self):
        self.date = datetime.fromisoformat(self.date) if isinstance(self.date, str) else self.date

