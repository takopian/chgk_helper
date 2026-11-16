import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bots import webserver
from parser import parse_quizzes
from quiz import Quiz
import utils


class WebserverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = patch.object(webserver, 'API_KEY', 'test-key')
        self.key.start()
        self.client = TestClient(TestServer(await webserver.init_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.key.stop()

    async def test_auth_rejects_before_fetch(self):
        with patch.object(webserver, 'request_lifejournal', new_callable=AsyncMock) as fetch:
            response = await self.client.get('/parse_quizzes')
            self.assertEqual(response.status, 401)
            fetch.assert_not_awaited()

    async def test_error_is_logged_with_traceback(self):
        with patch.object(webserver, 'request_lifejournal', new_callable=AsyncMock) as fetch:
            fetch.side_effect = ValueError('Upstream page changed')
            with self.assertLogs('bots.webserver', level='ERROR') as logs:
                response = await self.client.get('/parse_quizzes', headers={'X-API-Key': 'test-key'})
            self.assertEqual(response.status, 500)
            self.assertIn('Failed to parse', (await response.json())['error'])
            self.assertIsNotNone(logs.records[0].exc_info)
            self.assertIn('Upstream page changed', logs.output[0])

    async def test_difficulty_failure_does_not_fail_listing(self):
        game = Quiz('Game', url='https://example.test/game', date=datetime(2026, 11, 16))
        with patch.object(webserver, 'request_lifejournal', new_callable=AsyncMock), \
             patch.object(webserver, 'parse_quizzes', return_value=[game]), \
             patch.object(webserver, 'get_difficulty', new_callable=AsyncMock) as difficulty:
            difficulty.side_effect = TimeoutError('Upstream timeout')
            response = await self.client.get('/parse_quizzes', headers={'X-API-Key': 'test-key'})
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data[0]['poll_text'], 'Game')
            self.assertIsNone(data[0]['difficulty'])

    async def test_cleanup_closes_upstream_session(self):
        session = aiohttp.ClientSession()
        with patch.object(utils, 'session', session):
            await self.client.close()
            self.assertTrue(session.closed)
            self.assertIsNone(utils.session)

    def test_unexpected_listing_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'entry-content'):
            parse_quizzes('<html><h1>Access denied</h1></html>')
        with self.assertRaisesRegex(ValueError, 'entry-body'):
            parse_quizzes('<div class="entry-content"></div>')
