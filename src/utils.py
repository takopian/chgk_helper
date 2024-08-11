import locale
import logging
from datetime import datetime

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
