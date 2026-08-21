import string
from urllib.parse import urlparse

# 0-9a-zA-Z, the conventional base62 table
_B62_ALPHABET = string.digits + string.ascii_letters


class URLShortener:
    def __init__(self) -> None:
        return None

    def shorten(self, url: str) -> str:
        if not self._valid(url):
            return "error"
        return self._b62(url)

    def _b62(self, s: str) -> str:
        n = int.from_bytes(s.encode())
        digits: list[str] = []
        while n > 0:
            n, rem = divmod(n, 62)
            digits.append(_B62_ALPHABET[rem])
        return "".join(reversed(digits))[:6]

    def _valid[T: str | bytes](self, url: T) -> bool:
        if len(url) == 0:
            return False

        p = urlparse(str(url))
        return p.scheme != "" and p.scheme in ["http", "https"] and p.netloc != ""
