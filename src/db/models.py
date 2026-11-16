from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    DateTime,
    Date,
    Boolean,
    Text,
    ForeignKey,
    ForeignKeyConstraint,
    func,
    UniqueConstraint,
    Table,
)

Base = declarative_base()


class TelegramPoll(Base):
    __tablename__ = 'telegram_polls'
    bot_id = Column(BigInteger, primary_key=True)
    poll_id = Column(String(128), primary_key=True)
    chat_id = Column(BigInteger, ForeignKey('poll_chats.id'), nullable=False)
    message_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class TelegramPollOption(Base):
    __tablename__ = 'telegram_poll_options'
    __table_args__ = (
        ForeignKeyConstraint(['bot_id', 'poll_id'], ['telegram_polls.bot_id', 'telegram_polls.poll_id']),
        ForeignKeyConstraint(['chat_id', 'game_id'], ['poll_games.chat_id', 'poll_games.id']),
    )
    bot_id = Column(BigInteger, primary_key=True)
    poll_id = Column(String(128), primary_key=True)
    option_id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    game_id = Column(String(32), nullable=True)


class PollVoteEvent(Base):
    __tablename__ = 'poll_vote_events'
    __table_args__ = (ForeignKeyConstraint(
        ['bot_id', 'poll_id'], ['telegram_polls.bot_id', 'telegram_polls.poll_id']),)
    bot_id = Column(BigInteger, primary_key=True)
    update_id = Column(BigInteger, primary_key=True)
    poll_id = Column(String(128), nullable=False, index=True)
    voter_kind = Column(String(8), nullable=False)
    voter_id = Column(BigInteger, nullable=False)
    display_name = Column(Text, nullable=False)
    username = Column(Text)
    option_ids = Column(ARRAY(Integer), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)


class PollVote(Base):
    __tablename__ = 'poll_votes'
    __table_args__ = (ForeignKeyConstraint(
        ['bot_id', 'poll_id'], ['telegram_polls.bot_id', 'telegram_polls.poll_id']),)
    bot_id = Column(BigInteger, primary_key=True)
    poll_id = Column(String(128), primary_key=True)
    voter_kind = Column(String(8), primary_key=True)
    voter_id = Column(BigInteger, primary_key=True)
    display_name = Column(Text, nullable=False)
    username = Column(Text)
    option_ids = Column(ARRAY(Integer), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    last_update_id = Column(BigInteger, nullable=False)


class PollChat(Base):
    __tablename__ = 'poll_chats'

    id = Column(BigInteger, primary_key=True, autoincrement=False)
    games = relationship('PollGame', back_populates='chat', cascade='all, delete-orphan')


class PollGame(Base):
    __tablename__ = 'poll_games'
    __table_args__ = (UniqueConstraint('chat_id', 'poll_text', name='uq_poll_game_chat_text'),)

    chat_id = Column(BigInteger, ForeignKey('poll_chats.id', ondelete='CASCADE'), primary_key=True)
    id = Column(String(32), primary_key=True)
    poll_text = Column(Text, nullable=False)
    url = Column(Text)
    difficulty = Column(String(128))
    date = Column(DateTime, nullable=False, index=True)
    chat = relationship('PollChat', back_populates='games')
    registration = relationship('PollRegistration', back_populates='game', uselist=False,
                                cascade='all, delete-orphan')


class PollRegistration(Base):
    __tablename__ = 'poll_registrations'
    __table_args__ = (ForeignKeyConstraint(
        ['chat_id', 'game_id'], ['poll_games.chat_id', 'poll_games.id'], ondelete='CASCADE'),)

    chat_id = Column(BigInteger, primary_key=True)
    game_id = Column(String(32), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    game = relationship('PollGame', back_populates='registration')


# Association tables for many-to-many relationships
question_authors = Table(
    'question_authors',
    Base.metadata,
    Column('question_id', Integer, ForeignKey('question_pool.id', ondelete='CASCADE'), primary_key=True),
    Column('author_id', Integer, ForeignKey('authors.id', ondelete='CASCADE'), primary_key=True),
)

question_tournaments = Table(
    'question_tournaments',
    Base.metadata,
    Column('question_id', Integer, ForeignKey('question_pool.id', ondelete='CASCADE'), primary_key=True),
    Column('tournament_id', Integer, ForeignKey('tournaments.id', ondelete='CASCADE'), primary_key=True),
)


class Author(Base):
    __tablename__ = 'authors'

    id = Column(Integer, primary_key=True)
    name = Column(String(512), nullable=False, unique=True)

    questions = relationship('QuestionPool', secondary=question_authors, back_populates='authors')

    def __repr__(self) -> str:
        return f"<Author id={self.id} name={self.name}>"


class Tournament(Base):
    __tablename__ = 'tournaments'

    id = Column(Integer, primary_key=True)
    title = Column(String(512), nullable=False, unique=True)

    questions = relationship('QuestionPool', secondary=question_tournaments, back_populates='tournaments')

    def __repr__(self) -> str:
        return f"<Tournament id={self.id} title={self.title}>"



class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, unique=True, nullable=True)
    username = Column(String(256), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # relationships
    registrations = relationship('UsersRegistrations', back_populates='user', cascade='all, delete-orphan')
    answers = relationship('Answer', back_populates='user', cascade='all, delete-orphan')
    question_feedbacks = relationship('QuestionFeedback', back_populates='user', cascade='all, delete-orphan')

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
    
    authors = relationship('Author', secondary=question_authors, back_populates='questions')
    tournaments = relationship('Tournament', secondary=question_tournaments, back_populates='questions')
    feedbacks = relationship('QuestionFeedback', back_populates='question', cascade='all, delete-orphan')


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


class QuestionFeedback(Base):
    __tablename__ = 'question_feedback'
    __table_args__ = (UniqueConstraint('user_id', 'question_id', name='uq_user_question_feedback'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    # question_id now references the pool table rather than the competitions question table
    question_id = Column(Integer, ForeignKey('question_pool.id', ondelete='CASCADE'), nullable=False)
    liked = Column(Boolean, nullable=False)  # True for liked, False for disliked
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship('User', back_populates='question_feedbacks')
    question = relationship('QuestionPool', back_populates='feedbacks')

    def __repr__(self) -> str:
        return f"<QuestionFeedback id={self.id} user_id={self.user_id} question_id={self.question_id} liked={self.liked}>"
