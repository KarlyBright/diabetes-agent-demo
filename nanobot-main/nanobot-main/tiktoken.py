from typing import List


class _Encoding:
    def encode(self, text: str) -> List[int]:
        return list(text.encode("utf-8"))


def get_encoding(_: str) -> _Encoding:
    return _Encoding()
