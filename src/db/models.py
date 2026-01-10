from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Date,
    Boolean,
    Text,
    ForeignKey,
    func,
    UniqueConstraint,
)

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, unique=True, nullable=True)
    username = Column(String(256), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # relationships
    registrations = relationship('UsersRegistrations', back_populates='user', cascade='all, delete-orphan')
    answers = relationship('Answer', back_populates='user', cascade='all, delete-orphan')

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"


class Competition(Base):
    __tablename__ = 'competitions'

    id = Column(Integer, primary_key=True)
    name = Column(String(512), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    # type: 0 uses question pool, 1 uses scheduled questions
    competition_type = Column(Integer, nullable=False)

    questions = relationship('Question', back_populates='competition', cascade='all, delete-orphan')
    registrations = relationship('UsersRegistrations', back_populates='competition', cascade='all, delete-orphan')

    def __repr__(self) -> str:
        return f"<Competition id={self.id} name={self.name}>"


class Question(Base):
    __tablename__ = 'questions'

    id = Column(Integer, primary_key=True)
    competition_id = Column(Integer, ForeignKey('competitions.id', ondelete='CASCADE'), nullable=False)
    body = Column(Text, nullable=False)
    image_path = Column(String(1024), nullable=True)
    answer = Column(Text, nullable=False)
    date = Column(Date, nullable=True)

    competition = relationship('Competition', back_populates='questions')
    answers = relationship('Answer', back_populates='question', cascade='all, delete-orphan')

    def __repr__(self) -> str:
        return f"<Question id={self.id} competition_id={self.competition_id}>"


class QuestionPool(Base):
    __tablename__ = 'question_pool'

    id = Column(Integer, primary_key=True)
    body = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    handout = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)
    image_path = Column(String(1024), nullable=True)
    source_pack = Column(String(512), nullable=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    used = Column(Boolean, nullable=False, server_default='false')

    def __repr__(self) -> str:
        return f"<QuestionPool id={self.id} used={self.used} source={self.source_pack}>"


class UsersRegistrations(Base):
    __tablename__ = 'users_registrations'
    __table_args__ = (UniqueConstraint('user_id', 'competition_id', name='uq_user_competition'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    competition_id = Column(Integer, ForeignKey('competitions.id', ondelete='CASCADE'), nullable=False)

    user = relationship('User', back_populates='registrations')
    competition = relationship('Competition', back_populates='registrations')

    def __repr__(self) -> str:
        return f"<UsersRegistrations user_id={self.user_id} competition_id={self.competition_id}>"


class Answer(Base):
    __tablename__ = 'answers'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    question_id = Column(Integer, ForeignKey('questions.id', ondelete='CASCADE'), nullable=False)
    answer = Column(Text, nullable=False)
    is_correct = Column(Boolean, nullable=True)

    user = relationship('User', back_populates='answers')
    question = relationship('Question', back_populates='answers')

    def __repr__(self) -> str:
        return f"<Answer id={self.id} user_id={self.user_id} question_id={self.question_id} is_correct={self.is_correct}>"
