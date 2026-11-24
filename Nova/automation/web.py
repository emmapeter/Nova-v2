"""Web tools. Opening a page uses the default browser; reading a result needs internet."""
from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import requests

from automation import apps
from automation.registry import ToolResult

UA = {"User-Agent": "Mozilla/5.0 (Nova assistant)"}


def _safe_url(url: str) -> str | None:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc and " " not in url else None


def open_url(url: str) -> ToolResult:
    safe = _safe_url(url)
    if not safe:
        return ToolResult(False, "That doesn't look like a valid web address.")
    ok = apps.open_path(safe)
    return ToolResult(ok, "Opening that page." if ok else "I couldn't open the browser.")


def search_web(query: str) -> ToolResult:
    ok = apps.open_path("https://www.google.com/search?q=" + quote_plus(query))
    return ToolResult(ok, f"Searching for {query}." if ok else "I couldn't open the browser.")


def web_read(query: str) -> ToolResult:
    """Find the first search result for a query and return its readable text (needs internet)."""
    try:
        r = requests.get("https://html.duckduckgo.com/html/", params={"q": query}, headers=UA, timeout=8)
        link = re.search(r'class="result__a" href="([^"]+)"', r.text)
        if not link:
            return ToolResult(False, "I couldn't find a result for that search.")
        target = html.unescape(link.group(1))
        if "uddg=" in target:
            target = unquote(parse_qs(urlparse(target).query).get("uddg", [target])[0])
        if target.startswith("//"):
            target = "https:" + target
        safe = _safe_url(target)
        if not safe:
            return ToolResult(False, "The first result had an unusable address.")
        page = requests.get(safe, headers=UA, timeout=10)
        text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", page.text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()[:6000]
        if len(text) < 200:
            return ToolResult(False, "That page didn't contain readable text.")
        apps.open_path(safe)
        return ToolResult(True, text, verified=True, data={"url": safe, "text": text})
    except requests.RequestException:
        return ToolResult(False, "I couldn't reach the internet to read that page.")
