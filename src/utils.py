import locale
import logging
from datetime import datetime

import aiohttp
import yaml

FORMAT = "%d %B %Y"


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
    try:
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                return None
            return await resp.text()
    except Exception as e:
        return None
