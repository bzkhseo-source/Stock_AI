"""Google 뉴스 RSS 검색 피드 수집.

- 키 불필요. 과도한 요청 시 차단될 수 있으므로 15~30분 간격, 질의 수 제한.
- 제목·언론사·게시일·링크만 저장한다 (기사 원문 저장·재게시 금지).
- 상업적 이용 조건은 정식 출시 전 법률 검토 대상.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

import feedparser
import httpx
from sqlalchemy import select

from collectors.common import job_run
from config import settings
from db.models import News, NewsStock, Stock
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from db.session import session_scope, upsert

log = logging.getLogger("news")
FEED = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
UA = {"User-Agent": "Mozilla/5.0 (compatible; waeollatji-beta/0.1)"}


def decode_google_link(url: str) -> str:
    """구형 Google 뉴스 링크는 원문 URL이 base64로 들어 있어 풀 수 있다. 신형은 풀리지 않으면 그대로 둔다."""
    m = re.search(r"/articles/([^?]+)", url)
    if not m:
        return url
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        found = re.search(rb"https?://[\x21-\x7e]+", raw)
        if found:
            cand = found.group(0).decode("ascii", errors="ignore")
            cand = re.split(r"[\x00-\x1f]", cand)[0]
            if "." in cand and "google.com" not in cand:
                return cand
    except Exception:
        pass
    return url


def split_title(title: str) -> tuple[str, str | None]:
    """'기사 제목 - 언론사' → (제목, 언론사)"""
    head, pub = title, None
    if " - " in title:
        head, _, pub = title.rpartition(" - ")
        pub = pub.strip() or None
    head = re.sub(r"(\s+-|\s+By)\s*$", "", head.strip())
    return head.strip(), pub


def issue_key(title: str) -> str:
    """같은 사건의 중복 기사를 묶기 위한 키 (정규화한 제목 앞부분)."""
    norm = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", title)
    norm = re.sub(r"[^0-9A-Za-z가-힣]", "", norm)[:24]
    return hashlib.md5(norm.encode()).hexdigest()[:16]


def fetch_feed(query: str, days: int = 7) -> list[dict]:
    url = FEED.format(q=quote(f"{query} when:{days}d"))
    r = httpx.get(url, headers=UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    items = []
    for e in feed.entries:
        title, pub = split_title(e.get("title", ""))
        src = e.get("source") or {}
        published = datetime(*e.published_parsed[:6]) + timedelta(hours=9) if e.get("published_parsed") else None
        items.append(dict(url=decode_google_link(e.get("link", "")), title=title,
                          publisher=(src.get("title") if isinstance(src, dict) else None) or pub,
                          published_at=published, query=query, issue_key=issue_key(title)))
    return items


_GENERIC = {"대상", "동방", "서울", "한국", "세방", "신세계", "삼성", "현대", "LG", "SK", "GS", "KT"}


def link_stocks(session, news_rows: list[News], names: dict[str, str] | None = None) -> int:
    """뉴스 제목에 종목명이 등장하면 연결. 짧거나 흔한 이름은 오탐이 많아 제외."""
    if names is None:
        names = stock_names(session)
    pairs = set()
    for row in news_rows:
        for name, code in names.items():
            if name in row.title and name_in_title(name, row.title):  # 빠른 포함 검사 후 경계 검사
                pairs.add((row.id, code))
    if pairs:
        upsert(session, NewsStock, [dict(news_id=i, code=c) for i, c in pairs], ["news_id", "code"])
    return len(pairs)


def stock_names(session) -> dict[str, str]:
    return {s.name: s.code for s in session.scalars(select(Stock).where(Stock.country == "KR", Stock.kind == "보통주"))
            if len(s.name) >= 3 and s.name not in _GENERIC}


def name_in_title(name: str, title: str) -> bool:
    """종목명이 다른 단어의 일부가 아닐 때만 일치 ('SK하이닉스' 안의 '이닉스'는 제외)."""
    return re.search(rf"(?<![0-9A-Za-z가-힣]){re.escape(name)}", title) is not None


def collect_news(queries: list[str]) -> int:
    with job_run("news") as st:
        saved = 0
        with session_scope() as s:
            names = stock_names(s)
        for q in queries[: settings.news_queries_per_run]:
            try:
                items = fetch_feed(q)
            except Exception as e:
                log.warning("뉴스 수집 실패 '%s': %s", q, e)
                continue
            rows = []
            for it in items:
                if not it["url"] or len(it["url"]) > 600:
                    continue
                it["title"] = it["title"][:400]
                it["publisher"] = (it["publisher"] or "")[:100] or None
                it["query"] = (it["query"] or "")[:100]
                rows.append(it)
            if not rows:
                continue
            with session_scope() as s:
                ins = sqlite_insert if settings.is_sqlite else pg_insert
                # 이미 있는 기사(다른 배치가 먼저 저장한 것 포함)는 건너뛴다
                stmt = ins(News).values(rows).on_conflict_do_nothing(index_elements=["url"]).returning(News.id)
                new_ids = list(s.scalars(stmt))
                if new_ids:
                    new_rows = list(s.scalars(select(News).where(News.id.in_(new_ids))))
                    link_stocks(s, new_rows, names)
                saved += len(new_ids)
            time.sleep(2)  # 질의 간 간격
        st["rows"] = saved
        return saved
