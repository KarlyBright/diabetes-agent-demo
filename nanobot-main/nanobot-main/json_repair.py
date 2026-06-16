import json
from typing import Any


def loads(value: str | bytes | bytearray | Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode('utf-8')
    if isinstance(value, str):
        return json.loads(value)
    return value
