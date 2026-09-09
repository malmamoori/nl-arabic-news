"""Translation transport with explicit connection and read timeouts."""

import requests
from bs4 import BeautifulSoup


HTTP_TIMEOUT = (3, 8)


class NewsTranslator:
    def __init__(self, source="auto", target="ar"):
        self.source = source
        self.target = target

    def translate(self, text):
        with requests.get(
            "https://translate.google.com/m",
            params={"sl": self.source, "tl": self.target, "q": text},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=HTTP_TIMEOUT,
        ) as response:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            result = soup.select_one(".result-container, .t0")
            if result is None:
                raise ValueError("No translation in the response")
            return result.get_text(" ", strip=True)
