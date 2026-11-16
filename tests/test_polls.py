import importlib.util
import os
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from db.models import PollChat, PollGame, PollRegistration, TelegramPoll, TelegramPollOption, PollVote, PollVoteEvent
from db.poll_votes import save_poll, save_vote, game_voters
from db.polls import games_for_chat, save_games, register_game, registered_games, unregister_game
from db import polls as poll_storage
from db.session import DATABASE_URL
from quiz import Quiz
from scripts.import_polls import import_legacy
from bots import polls_bot


class BatchTests(unittest.TestCase):
    def test_reminder_escapes_and_splits_mentions(self):
        voters = [SimpleNamespace(voter_id=i + 1, display_name='<Имя & фамилия> ' + '😀' * 60)
                  for i in range(80)]
        messages = polls_bot.reminder_messages('Игра <А> & Б', voters)
        self.assertGreater(len(messages), 1)
        self.assertIn('Игра &lt;А&gt; &amp; Б', messages[0])
        from html import unescape
        import re
        for message in messages:
            visible = unescape(re.sub('<[^>]+>', '', message))
            self.assertLessEqual(len(visible.encode('utf-16-le')) // 2, 4096)
            self.assertEqual(message.count('<a '), message.count('</a>'))
        self.assertEqual(sum(message.count('<a ') for message in messages), 80)
        self.assertNotIn('<a ', polls_bot.reminder_messages('Без голосов', [])[0])

    def test_batches(self):
        for count in range(1, 45):
            batches = list(polls_bot.poll_batches(list(range(count))))
            self.assertEqual([item for batch in batches for item in batch], list(range(count)))
            self.assertTrue(all(2 <= len(batch) <= 10 for batch in batches) or count == 1)


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL')
class PollStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DATABASE_URL)
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        schema = 'polls_test_' + uuid4().hex
        await self.connection.execute(text(f'CREATE SCHEMA {schema}'))
        await self.connection.execute(text(f'SET LOCAL search_path TO {schema}'))
        path = Path(polls_bot.__file__).resolve().parents[1] / 'alembic/versions/c712e3b490ab_poll_storage.py'
        spec = importlib.util.spec_from_file_location('poll_migration', path)
        self.migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.migration)
        vote_spec = importlib.util.spec_from_file_location('vote_migration', path.with_name('e483a10f2c61_poll_votes.py'))
        self.vote_migration = importlib.util.module_from_spec(vote_spec)
        vote_spec.loader.exec_module(self.vote_migration)
        await self.connection.run_sync(self.upgrade)
        self.session = AsyncSession(bind=self.connection, expire_on_commit=False,
                                    join_transaction_mode='create_savepoint')
        self.today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        self.chat = -1001234567890

    def upgrade(self, conn):
        with Operations.context(MigrationContext.configure(conn)):
            self.migration.upgrade()
            self.vote_migration.upgrade()

    async def asyncTearDown(self):
        await self.session.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    def quiz(self, title='Game', id='a' * 32, day=None):
        return Quiz(title, date=day or self.today, id=id)

    async def test_import_idempotence_and_old_callbacks(self):
        game = self.quiz()
        orphan = self.quiz('Registered only', 'b' * 32)
        data = [(self.chat, [game, game], [game, game, orphan])]
        await import_legacy(self.session, data)
        await self.session.commit()
        await import_legacy(self.session, data)
        await self.session.commit()
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollGame)), 2)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollRegistration)), 2)
        self.assertEqual((await register_game(self.session, self.chat, game.id)).id, game.id)
        self.assertEqual(len(await registered_games(self.session, self.today.date(), self.chat)), 2)

    async def test_chat_isolation_and_duplicate_registration(self):
        await save_games(self.session, self.chat, [self.quiz()])
        await save_games(self.session, self.chat + 1, [self.quiz()])
        for _ in range(2):
            await register_game(self.session, self.chat, 'a' * 32)
        self.assertIsNone(await register_game(self.session, self.chat + 2, 'a' * 32))
        self.assertEqual(await registered_games(self.session, self.today.date(), self.chat + 1), [])
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollRegistration)), 1)

    async def test_upcoming_and_today(self):
        for offset in [-1, 0, 1]:
            game = self.quiz(str(offset), str(offset), self.today + timedelta(days=offset))
            await save_games(self.session, self.chat, [game])
            await register_game(self.session, self.chat, game.id)
        self.assertEqual(len(await registered_games(self.session, self.today.date(), self.chat)), 2)
        today = await registered_games(self.session, self.today.date(), only_today=True)
        self.assertEqual([game.poll_text for game in today], ['0'])

    async def test_unregister_is_scoped_repeatable_and_preserves_game(self):
        for chat in [self.chat, self.chat + 1]:
            await save_games(self.session, chat, [self.quiz()])
            await register_game(self.session, chat, 'a' * 32)
        self.assertIsNone(await unregister_game(self.session, self.chat + 2, 'a' * 32))
        removed = await unregister_game(self.session, self.chat, 'a' * 32)
        self.assertEqual(removed.poll_text, 'Game')
        await self.session.commit()
        self.assertIsNone(await unregister_game(self.session, self.chat, 'a' * 32))
        self.assertEqual(await registered_games(self.session, self.today.date(), self.chat), [])
        reminders = await registered_games(self.session, self.today.date(), only_today=True)
        self.assertEqual([game.chat_id for game in reminders], [self.chat + 1])
        self.assertEqual(len(await games_for_chat(self.session, self.chat)), 1)
        await register_game(self.session, self.chat, 'a' * 32)
        self.assertEqual(len(await registered_games(self.session, self.today.date(), self.chat)), 1)

    async def test_import_failure_rolls_back(self):
        with self.assertRaises(ValueError):
            async with self.session.begin():
                await import_legacy(self.session, [(self.chat, [self.quiz(), Quiz('Invalid')], [])])
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollChat)), 0)

    async def test_successful_batch_survives_later_send_failure(self):
        quizzes = [dict(poll_text=f'Game {i}', date=self.today.isoformat()) for i in range(12)]
        response = AsyncMock()
        response.status = 200
        response.json.return_value = quizzes
        client = AsyncMock()
        from unittest.mock import MagicMock
        client.get = MagicMock()
        client.get.return_value.__aenter__ = AsyncMock(return_value=response)
        client.get.return_value.__aexit__ = AsyncMock(return_value=False)
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=self.chat), message=AsyncMock())
        def sent(poll_id):
            return SimpleNamespace(poll=SimpleNamespace(id=poll_id), chat=SimpleNamespace(id=self.chat),
                                   message_id=12, date=datetime.now(timezone.utc))
        update.message.reply_poll.side_effect = [sent('first'), RuntimeError('Telegram unavailable')]
        context = SimpleNamespace(bot=SimpleNamespace(id=123))

        @asynccontextmanager
        async def storage(chat_id):
            try:
                yield self.session
            except Exception:
                await self.session.rollback()
                raise

        with patch.object(polls_bot.aiohttp, 'ClientSession', return_value=client_context), \
             patch.object(polls_bot, 'chat_session', storage):
            with self.assertRaises(RuntimeError):
                await polls_bot.create_poll(update, context)
            self.assertEqual(len(await games_for_chat(self.session, self.chat)), 10)
            update.message.reply_poll.side_effect = None
            update.message.reply_poll.return_value = sent('second')
            await polls_bot.create_poll(update, context)
            self.assertEqual(len(await games_for_chat(self.session, self.chat)), 12)
            await polls_bot.create_poll(update, context)
            self.assertEqual(update.message.reply_poll.await_count, 3)
            self.assertEqual(await self.session.scalar(select(func.count()).select_from(TelegramPoll)), 2)
            self.assertEqual(await self.session.scalar(select(func.count()).select_from(TelegramPollOption)), 12)

    async def test_migration_downgrade(self):
        def downgrade(conn):
            with Operations.context(MigrationContext.configure(conn)):
                self.vote_migration.downgrade()
                self.migration.downgrade()
        await self.connection.run_sync(downgrade)
        self.assertIsNone(await self.connection.scalar(text("SELECT to_regclass('poll_games')")))

    async def test_chat_lock_survives_commit_and_releases_on_error(self):
        key = -int(uuid4().hex[:15], 16)
        async with self.engine.connect() as contender:
            with patch.object(poll_storage, 'async_engine', self.engine):
                with self.assertRaises(RuntimeError):
                    async with poll_storage.chat_session(key) as session:
                        await session.execute(text('SELECT 1'))
                        await session.commit()
                        self.assertFalse(await contender.scalar(
                            text('SELECT pg_try_advisory_lock(:id)'), {'id': key}))
                        raise RuntimeError('send failed')
                self.assertTrue(await contender.scalar(
                    text('SELECT pg_try_advisory_lock(:id)'), {'id': key}))
                await contender.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': key})


    async def prepare_vote_poll(self, bot_id=123):
        games = await save_games(self.session, self.chat, [self.quiz()])
        message = SimpleNamespace(poll=SimpleNamespace(id='new-poll'), chat=SimpleNamespace(id=self.chat),
                                  message_id=99, date=datetime.now(timezone.utc))
        await save_poll(self.session, bot_id, message, games, ['Game', 'Не участвую'])
        await self.session.commit()

    async def vote(self, update_id=1, choices=(0,), **overrides):
        args = dict(bot_id=123, update_id=update_id, poll_id='new-poll', voter_kind='user',
                    voter_id=9876543210, display_name='Test User', username=None,
                    option_ids=choices, received_at=datetime.now(timezone.utc))
        args.update(overrides)
        result = await save_vote(self.session, **args)
        await self.session.commit()
        return result

    async def test_revoting_withdrawal_history_and_late_duplicate(self):
        await self.prepare_vote_poll()
        for update_id, choices in [(1, [0]), (2, [0, 1]), (3, [1]), (4, [])]:
            self.assertEqual(await self.vote(update_id, choices), 'saved')
        self.assertEqual(await self.vote(2, [0, 1]), 'duplicate')
        current = await self.session.get(PollVote, (123, 'new-poll', 'user', 9876543210))
        self.assertEqual(current.option_ids, [])
        self.assertEqual(current.last_update_id, 4)
        history = (await self.session.scalars(select(PollVoteEvent).order_by(PollVoteEvent.update_id))).all()
        self.assertEqual([event.option_ids for event in history], [[0], [0, 1], [1], []])
        self.assertTrue(all(event.received_at.tzinfo is not None for event in history))
        self.assertEqual(await self.vote(5, [0]), 'saved')
        await self.session.refresh(current)
        self.assertEqual(current.option_ids, [0])
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollRegistration)), 0)

    async def test_old_poll_and_other_bot_ignored(self):
        await self.prepare_vote_poll()
        self.assertEqual(await self.vote(poll_id='old-poll'), 'unknown')
        self.assertEqual(await self.vote(bot_id=124), 'unknown')
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollVoteEvent)), 0)

    async def test_voter_and_bot_isolation(self):
        await self.prepare_vote_poll()
        await self.prepare_vote_poll(bot_id=124)
        await self.vote()
        await self.vote(2, [1], voter_kind='chat')
        await self.vote(1, [1], bot_id=124)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollVote)), 3)
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollVoteEvent)), 3)
        special = await self.session.get(TelegramPollOption, (123, 'new-poll', 1))
        self.assertIsNone(special.game_id)

    async def test_delayed_update_and_sequence_reset(self):
        await self.prepare_vote_poll()
        now = datetime.now(timezone.utc)
        await self.vote(10, [], received_at=now)
        self.assertEqual(await self.vote(9, [0], received_at=now + timedelta(seconds=1)), 'stale')
        current = await self.session.get(PollVote, (123, 'new-poll', 'user', 9876543210))
        self.assertEqual(current.option_ids, [])
        self.assertEqual(await self.vote(1, [1], received_at=now + timedelta(days=8)), 'saved')

    async def test_invalid_option_rolls_back(self):
        await self.prepare_vote_poll()
        with self.assertRaises(ValueError):
            async with self.session.begin():
                await save_vote(self.session, bot_id=123, update_id=1, poll_id='new-poll',
                                voter_kind='user', voter_id=1, display_name='Test', username=None,
                                option_ids=[999], received_at=datetime.now(timezone.utc))
        self.assertEqual(await self.session.scalar(select(func.count()).select_from(PollVoteEvent)), 0)

    async def test_poll_answer_handler_user_and_chat(self):
        await self.prepare_vote_poll()
        user = SimpleNamespace(id=9876543210, full_name='User', username='test_user')
        chat = SimpleNamespace(id=-1009988776655, title='Chat', username=None)
        context = SimpleNamespace(bot=SimpleNamespace(id=123))

        @asynccontextmanager
        async def session_factory():
            yield self.session

        with patch.object(polls_bot, 'async_session', session_factory):
            for update_id, voter, choices in [(1, user, [0]), (2, user, []), (3, chat, [1])]:
                answer = SimpleNamespace(poll_id='new-poll', option_ids=choices,
                                         user=voter if voter is user else None,
                                         voter_chat=voter if voter is chat else None)
                await polls_bot.handle_poll_answer(SimpleNamespace(update_id=update_id, poll_answer=answer), context)
        rows = (await self.session.scalars(select(PollVote).order_by(PollVote.voter_kind))).all()
        self.assertEqual([(v.voter_kind, v.option_ids) for v in rows], [('chat', [1]), ('user', [])])


    async def test_reminder_voters_filter_and_deduplicate(self):
        await self.prepare_vote_poll()
        await self.prepare_vote_poll(bot_id=124)
        await self.vote(1, [0], voter_id=1)
        await self.vote(2, [0], voter_id=2)
        await self.vote(3, [], voter_id=2)
        await self.vote(4, [1], voter_id=3)
        await self.vote(5, [0], voter_id=4, voter_kind='chat')
        await self.vote(1, [0], voter_id=5, bot_id=124)
        games = await games_for_chat(self.session, self.chat)
        message = SimpleNamespace(poll=SimpleNamespace(id='another-poll'), chat=SimpleNamespace(id=self.chat),
                                  message_id=100, date=datetime.now(timezone.utc))
        await save_poll(self.session, 123, message, games, ['Game'])
        await self.vote(6, [0], voter_id=1, poll_id='another-poll', display_name='Latest name')
        rows = await game_voters(self.session, 123, self.chat, 'a' * 32)
        self.assertEqual([(r.voter_id, r.display_name) for r in rows], [(1, 'Latest name')])
        self.assertEqual(await game_voters(self.session, 123, self.chat + 1, 'a' * 32), [])
        self.assertEqual(await game_voters(self.session, 123, self.chat, 'other-game'), [])

    async def test_reminder_uses_current_votes(self):
        await self.prepare_vote_poll()
        await register_game(self.session, self.chat, 'a' * 32)
        await self.session.commit()
        await self.vote(1, [0], voter_id=1, display_name='Имя <&>')
        await self.vote(2, [0], voter_id=2)
        await self.vote(3, [], voter_id=2)
        context = SimpleNamespace(bot=SimpleNamespace(id=123, send_message=AsyncMock()))

        @asynccontextmanager
        async def session_factory():
            yield self.session

        with patch.object(polls_bot, 'async_session', session_factory):
            await polls_bot.notify_registered(context)
        context.bot.send_message.assert_awaited_once()
        args = context.bot.send_message.call_args.kwargs
        self.assertEqual(args['chat_id'], self.chat)
        self.assertEqual(args['parse_mode'], 'HTML')
        self.assertIn('tg://user?id=1', args['text'])
        self.assertIn('Имя &lt;&amp;&gt;', args['text'])
        self.assertNotIn('tg://user?id=2', args['text'])

    async def test_manual_reminder_does_not_send_to_other_chats(self):
        for chat_id in [self.chat, self.chat + 1]:
            await save_games(self.session, chat_id, [self.quiz()])
            await register_game(self.session, chat_id, 'a' * 32)
        await self.session.commit()
        context = SimpleNamespace(bot=SimpleNamespace(id=123, send_message=AsyncMock()))

        @asynccontextmanager
        async def session_factory():
            yield self.session

        with patch.object(polls_bot, 'async_session', session_factory):
            sent = await polls_bot.notify_registered(context, chat_id=self.chat)
        self.assertEqual(sent, 1)
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(context.bot.send_message.call_args.kwargs['chat_id'], self.chat)
