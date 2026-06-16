import logging
from typing import Any


class _LoggerShim:
    def __init__(self) -> None:
        logging.basicConfig(level=logging.INFO)
        self._logger = logging.getLogger("nanobot")

    def _format(self, message: str, *args: Any, **kwargs: Any) -> str:
        if args:
            try:
                return message.format(*args)
            except Exception:
                return " ".join([message, *[str(arg) for arg in args]])
        if kwargs:
            try:
                return message.format(**kwargs)
            except Exception:
                return f"{message} {kwargs}"
        return message

    def bind(self, **_: Any) -> "_LoggerShim":
        return self

    def opt(self, **_: Any) -> "_LoggerShim":
        return self

    def add(self, *_: Any, **__: Any) -> int:
        return 0

    def remove(self, *_: Any, **__: Any) -> None:
        return None

    def trace(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(self._format(message, *args, **kwargs))

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(self._format(message, *args, **kwargs))

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(self._format(message, *args, **kwargs))

    def success(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(self._format(message, *args, **kwargs))

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(self._format(message, *args, **kwargs))

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(self._format(message, *args, **kwargs))

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.critical(self._format(message, *args, **kwargs))

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(self._format(message, *args, **kwargs))


logger = _LoggerShim()
