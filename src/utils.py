import locale
import asyncio
import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from datetime import timezone

import aiohttp
import yaml
import socket

FORMAT = "%d %B %Y"
session = None


def with_locale(temp_locale):
    def decorator(func):
        def wrapper(*args, **kwargs):
            original_locale = locale.getlocale(locale.LC_TIME)
            try:
                locale.setlocale(locale.LC_TIME, temp_locale)
                result = func(*args, **kwargs)
            finally:
                locale.setlocale(locale.LC_TIME, original_locale)
            return result

        return wrapper

    return decorator


def datetime_serializer(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError("Type not serializable")


def read_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_yaml(path, data):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


async def fetch(session: aiohttp.ClientSession, url: str) -> str | None:
    for attempt in range(3):
        delay = 2 ** (attempt + 1)
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == 200:
                    return await resp.text()
                logging.warning('Fetch %s returned HTTP %s (attempt %s/3)', url, resp.status, attempt + 1)
                if resp.status != 429 and not 500 <= resp.status < 600:
                    return None
                retry_after = resp.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = max(0, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                    # Defer to the next job instead of retrying before a long server cooldown.
                    if delay > 60:
                        logging.error('Fetch %s deferred: Retry-After exceeds 60 seconds', url)
                        return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logging.warning('Fetch %s failed (attempt %s/3): %s', url, attempt + 1, exc)
        if attempt < 2:
            await asyncio.sleep(delay)
    logging.error('Fetch %s failed after 3 attempts', url)
    return None


async def request_lifejournal(link: str) -> str:
    global session
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(family=socket.AF_INET, limit=10)
        timeout = aiohttp.ClientTimeout(total=30)
        session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    async with session.get(link, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }) as response:
        response.raise_for_status()
        html = await response.text()
    return html


async def close_lifejournal_session():
    global session
    if session is not None:
        await session.close()
        session = None
