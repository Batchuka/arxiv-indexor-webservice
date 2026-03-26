import urllib.request
from typing import Any
import feedparser
from arxiv_indexor.db import get_conn, insert_article

CATEGORIES = ["quant-ph", "cs.CL", "cs.LG"]
RSS_URL = "https://rss.arxiv.org/rss/{category}"


def _fetch_xml(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "arxiv-indexor/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_articles() -> tuple[int, list[dict[str, Any]]]:
    """Fetch articles from arXiv RSS feeds.

    Returns (count_new, new_articles) where new_articles contains only
    articles not previously seen in the database.
    """
    conn = get_conn()
    new_articles: list[dict[str, Any]] = []

    for category in CATEGORIES:
        url = RSS_URL.format(category=category)
        xml = _fetch_xml(url)
        feed = feedparser.parse(xml)

        for entry in feed.entries:
            authors = str(entry.get("author", ""))
            # arXiv RSS uses <dc:creator> which feedparser maps to 'author'
            article = {
                "id": str(entry.get("id") or entry.get("link", "")),
                "title": str(entry.get("title", "")).strip(),
                "authors": authors,
                "abstract": str(entry.get("summary", "")).strip(),
                "category": category,
                "published": str(entry.get("published", "")),
                "link": str(entry.get("link", "")),
            }
            if article["id"] and article["title"]:
                if insert_article(conn, article):
                    new_articles.append(article)

    conn.commit()
    conn.close()
    return len(new_articles), new_articles
