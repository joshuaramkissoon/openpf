from __future__ import annotations

import html
from urllib.parse import quote_plus

import httpx

from app.core.config import get_settings

settings = get_settings()


def web_search(query: str, max_results: int = 5) -> list[dict]:
    q = query.strip()
    if not q:
        return []

    # DuckDuckGo instant answer API (no key required) as a default fallback.
    url = f"https://api.duckduckgo.com/?q={quote_plus(q)}&format=json&no_redirect=1&no_html=1"
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return []
        body = response.json()
    except Exception:
        return []

    results: list[dict] = []

    if body.get("AbstractText"):
        results.append(
            {
                "title": body.get("Heading") or q,
                "url": body.get("AbstractURL") or "",
                "snippet": html.unescape(str(body.get("AbstractText") or "")),
                "source": "duckduckgo",
            }
        )

    for topic in body.get("RelatedTopics", [])[: max_results * 2]:
        if isinstance(topic, dict) and topic.get("FirstURL") and topic.get("Text"):
            results.append(
                {
                    "title": str(topic.get("Text", "")).split(" - ")[0][:120],
                    "url": topic.get("FirstURL"),
                    "snippet": html.unescape(str(topic.get("Text") or "")),
                    "source": "duckduckgo",
                }
            )
        if len(results) >= max_results:
            break

    return results[:max_results]


def fetch_news(query: str, max_results: int = 8) -> list[dict]:
    key = (settings.newsapi_api_key or "").strip()
    if not key:
        return []

    q = query.strip() or "markets"
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": q,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max(1, min(max_results, 20)),
        "apiKey": key,
    }

    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url, params=params)
        if response.status_code >= 400:
            return []
        body = response.json()
    except Exception:
        return []

    out: list[dict] = []
    for article in body.get("articles", [])[:max_results]:
        out.append(
            {
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "source": (article.get("source") or {}).get("name", "newsapi"),
                "publishedAt": article.get("publishedAt"),
                "snippet": article.get("description") or article.get("content") or "",
            }
        )
    return out


def fetch_x_posts(query: str, max_results: int = 10) -> list[dict]:
    bearer = (settings.x_api_bearer_token or "").strip()
    if not bearer:
        return []

    q = query.strip()
    if not q:
        return []

    headers = {"Authorization": f"Bearer {bearer}"}
    params = {
        "query": q,
        "max_results": max(10, min(max_results, 100)),
        "tweet.fields": "created_at,author_id,public_metrics,lang",
    }

    url = "https://api.x.com/2/tweets/search/recent"

    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url, headers=headers, params=params)
        if response.status_code >= 400:
            return []
        body = response.json()
    except Exception:
        return []

    data = body.get("data", [])
    out: list[dict] = []
    for post in data[:max_results]:
        out.append(
            {
                "id": post.get("id"),
                "author_id": post.get("author_id"),
                "text": post.get("text", ""),
                "created_at": post.get("created_at"),
                "public_metrics": post.get("public_metrics", {}),
                "lang": post.get("lang"),
            }
        )
    return out
