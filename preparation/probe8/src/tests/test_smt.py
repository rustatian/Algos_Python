import pytest
from probe8.main import URLShortener

URLS = [
    "https://www.example.com",
    "http://github.com/rustatian",
    "https://docs.python.org/3/library/urllib.parse.html",
]


@pytest.mark.parametrize("url", URLS)
def test_shortener(url: str):
    shortener = URLShortener()
    short_url: str = shortener.shorten(url)
    assert short_url is not None
    assert 0 < len(short_url) <= 6
    assert short_url != "error"


@pytest.mark.parametrize("url", URLS)
def test_shortener_idemp(url: str):
    shortener = URLShortener()
    short_url: str = shortener.shorten(url)
    assert short_url is not None
    assert len(short_url) > 0
    short_url2: str = shortener.shorten(url)
    assert short_url2 == short_url
