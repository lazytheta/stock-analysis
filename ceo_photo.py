"""A CEO's photo for the Management tab, from Wikipedia (Wikimedia Commons,
freely licensed).

Only used when the Wikipedia article is plainly about this company's CEO --
its text names the company -- so a namesake's face never ends up on the
card. Anything else returns None and the page draws initials instead.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_UA = "LazyTheta/1.0 (https://lazytheta.io; ligtermoetarjan@gmail.com)"
_GENERIC = {"inc", "corp", "corporation", "company", "co", "plc", "ltd", "group",
            "holdings", "the", "platforms", "technologies", "international", "sa", "nv", "ag"}


def company_words(company):
    """The distinctive words of a company name: "Meta Platforms, Inc." ->
    ["meta"]; "Lam Research Corporation" -> ["lam", "research"]."""
    words = re.findall(r"[a-z0-9&]+", (company or "").lower())
    return [w for w in words if w not in _GENERIC and len(w) > 1]


def matches_company(summary, company):
    """True when the article text names the company (its first distinctive
    word, or all of them)."""
    text = f'{summary.get("description") or ""} {summary.get("extract") or ""}'.lower()
    words = company_words(company)
    return bool(words) and (words[0] in text or all(w in text for w in words))


def photo_url(name, company, timeout=4):
    """The article thumbnail URL for `name`, or None. Never raises."""
    if not name or not company:
        return None
    try:
        url = _API + urllib.parse.quote(name.strip().replace(" ", "_"))
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            summary = json.loads(resp.read())
    except Exception as e:
        logger.debug("No Wikipedia summary for %s: %s", name, e)
        return None
    if summary.get("type") == "disambiguation" or not matches_company(summary, company):
        return None
    return (summary.get("thumbnail") or {}).get("source")
