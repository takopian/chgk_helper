import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from parser import GotQuestionsParseError, get_packs_by_complexity_sync, parse_pack_questions
from utils import fetch

FIXTURES = Path(__file__).parent / 'fixtures'


def transport(stream, split=None):
    chunks = [stream] if split is None else [stream[:split], stream[split:]]
    return ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk]) + ')</script>' for chunk in chunks)


def package(question=None):
    return {'id': 1, 'title': 'Package', 'tours': [
        {'questions': [question or {'id': 2, 'text': 'Question', 'answer': 'Answer'}]}
    ]}


class ParserTests(unittest.TestCase):
    def test_real_listing(self):
        html = (FIXTURES / 'home.html').read_text(encoding='utf-8')
        self.assertEqual(get_packs_by_complexity_sync(html), ['/pack/7365', '/pack/7364'])
        self.assertEqual(get_packs_by_complexity_sync(html, 90, 100), [])

    def test_real_package(self):
        questions = parse_pack_questions((FIXTURES / 'pack.html').read_text(encoding='utf-8'))
        self.assertEqual(len(questions), 36)
        self.assertEqual(questions[0]['answer'], 'Обратный отсчет.')
        self.assertEqual(questions[0]['authors'][0], {'id': 5401, 'name': 'Константин Ильин'})
        self.assertEqual(questions[0]['tournaments'][0]['id'], 13733)
        self.assertTrue(questions[0]['tournaments'][0]['title'])
        self.assertEqual(questions[1]['handout'], 'лецирп')
        self.assertEqual(questions[11]['image_path'], '/pics/7365/pic_498280.jpeg')

    def test_escaping_and_split_chunks(self):
        text = 'Кавычки "ответ", слеш \\, перенос\nи скобки }]}]}'
        q = {'id': 2, 'text': text, 'answer': '$$literal'}
        stream = '1:' + json.dumps(package(q), ensure_ascii=False) + '\n'
        for split in [3, len(stream) // 2, len(stream) - 3]:
            parsed = parse_pack_questions(transport(stream, split))[0]
            self.assertEqual(parsed['body'], text)
            self.assertEqual(parsed['answer'], '$literal')

    def test_utf8_text_record_and_references(self):
        text = 'Длинный текст\nс "кавычками" и : скобками []'
        q = {'id': 2, 'text': '$a', 'answer': 'Answer'}
        stream = '1:' + json.dumps(package(q)) + '\n'
        stream += 'a:T' + format(len(text.encode('utf-8')), 'x') + ',' + text
        self.assertEqual(parse_pack_questions(transport(stream, len(stream) - 4))[0]['body'], text)

    def test_all_tours_deduplication_and_removed_questions(self):
        pack = package()
        pack['tours'].append({'questions': [
            {'id': 2, 'text': 'Question', 'answer': 'Answer'},
            {'id': 3, 'text': 'Second', 'answer': '', 'authors': []},
            {'id': 4, 'takenDown': True},
        ]})
        result = parse_pack_questions(transport('1:' + json.dumps(pack) + '\n'))
        self.assertEqual([q['body'] for q in result], ['Question', 'Second'])
        self.assertEqual(result[1]['comment'], '')

    def test_difficulty_boundaries_nulls_and_duplicate_metadata(self):
        packs = [{'id': i, 'title': 'Pack', 'truedls': values}
                 for i, values in enumerate([[None], [2.5], [5.0], [5.1], [1.2, 3.1]], 1)]
        html = ''.join(f'<a href="/pack/{i}">Pack</a>' for i in range(1, 6))
        html += transport('1:' + json.dumps(packs + packs) + '\n')
        self.assertEqual(get_packs_by_complexity_sync(html), ['/pack/2', '/pack/3', '/pack/5'])

    def test_unrecognized_pages_fail_explicitly(self):
        for html in ['<h1>429 Too Many Requests</h1>', transport('1:{"newSchema":true}\n')]:
            for parse in [get_packs_by_complexity_sync, parse_pack_questions]:
                with self.assertRaises(GotQuestionsParseError):
                    parse(html)

    def test_missing_reference_or_answer_fails(self):
        for q in [{'text': '$a', 'answer': 'A'}, {'text': 'Question'}]:
            with self.assertRaises(GotQuestionsParseError):
                parse_pack_questions(transport('1:' + json.dumps(package(q)) + '\n'))


class FetchTests(unittest.IsolatedAsyncioTestCase):
    def session(self, statuses, headers=None):
        session = MagicMock()
        responses = []
        for status in statuses:
            response = MagicMock(status=status, headers=headers or {})
            response.text = AsyncMock(return_value='HTML')
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=response)
            context.__aexit__ = AsyncMock(return_value=False)
            responses.append(context)
        session.get.side_effect = responses
        return session

    async def test_success(self):
        session = self.session([200])
        self.assertEqual(await fetch(session, 'https://example.test'), 'HTML')
        self.assertEqual(session.get.call_count, 1)

    async def test_rate_limit_then_success(self):
        session = self.session([429, 200], {'Retry-After': '3'})
        with patch('utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertEqual(await fetch(session, 'https://example.test'), 'HTML')
            sleep.assert_awaited_once_with(3)

    async def test_retries_are_bounded(self):
        session = self.session([503, 503, 503])
        with patch('utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
            self.assertIsNone(await fetch(session, 'https://example.test'))
            self.assertEqual(session.get.call_count, 3)
            self.assertEqual(sleep.await_count, 2)

    async def test_no_retry_on_not_found_or_long_cooldown(self):
        for status, headers in [(404, {}), (429, {'Retry-After': '120'})]:
            session = self.session([status], headers)
            with patch('utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
                self.assertIsNone(await fetch(session, 'https://example.test'))
                sleep.assert_not_awaited()

    async def test_timeout_then_success(self):
        session = self.session([200])
        response = session.get.side_effect
        session.get.side_effect = [asyncio.TimeoutError(), next(response)]
        with patch('utils.asyncio.sleep', new_callable=AsyncMock):
            self.assertEqual(await fetch(session, 'https://example.test'), 'HTML')


if __name__ == '__main__':
    unittest.main()
