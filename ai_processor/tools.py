from typing import List

import requests
from bs4 import BeautifulSoup


def perform_search(urls: List[str]):
    results = {}

    for url in urls:
        try:
            res = requests.get(url)
            res.raise_for_status()
            soup = BeautifulSoup(res.text, "lxml")

            for script in soup(["script", "style"]):
                script.decompose()

            text = soup.get_text(separator="\n", strip=True)
            results[url] = text
        except Exception as e:
            results[url] = f"Error fetching {url}: {e}"
    return results
