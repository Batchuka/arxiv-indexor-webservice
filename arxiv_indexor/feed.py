import urllib.request
import feedparser
from arxiv_indexor.db import get_conn, insert_article

CATEGORIES = ["quant-ph", "cs.CL", "cs.LG"]
RSS_URL = "https://rss.arxiv.org/rss/{category}"


def _fetch_xml(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-indexor/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_articles() -> int:
    conn = get_conn()
    total = 0

    for category in CATEGORIES:
        url = RSS_URL.format(category=category)
        xml = _fetch_xml(url)
        feed = feedparser.parse(xml)

        for entry in feed.entries:
            authors = entry.get("author", "")
            # arXiv RSS uses <dc:creator> which feedparser maps to 'author'
            article = {
                "id": entry.get("id") or entry.get("link", ""),
                "title": entry.get("title", "").strip(),
                "authors": authors,
                "abstract": entry.get("summary", "").strip(),
                "category": category,
                "published": entry.get("published", ""),
                "link": entry.get("link", ""),
            }
            if article["id"] and article["title"]:
                insert_article(conn, article)
                total += 1

    conn.commit()
    conn.close()
    return total
