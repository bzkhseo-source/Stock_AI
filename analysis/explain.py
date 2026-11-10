"""'왜 올랐지?' 원인 추적.

급등락·거래량 급증 종목에 대해 같은 시점의 공시, 뉴스, 같은 테마 종목의 동반 움직임을 모은다.
LLM은 모은 자료 안에서만 원인 후보를 고른다. 자료가 없으면 '원인 불명'으로 표시한다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select

from config import settings
from db.models import Disclosure, MetricDaily, MoveExplanation, News, NewsStock, PriceDaily, Stock, Theme, ThemeMember
from db.session import session_scope, upsert
from llm.client import LLMUnavailable, available, generate_json

log = logging.getLogger("explain")

SYSTEM = (
    "너는 주가 변동의 원인 후보를 정리하는 보조다. 반드시 주어진 자료 목록 안에서만 고르고, "
    "자료에 없는 사실이나 전망, 매수·매도 의견을 쓰지 않는다. 한국어로 두 문장 이내."
)


def is_move(m: MetricDaily) -> bool:
    big = m.ret_1d is not None and abs(m.ret_1d) >= settings.move_threshold_pct
    spike = m.volume_ratio is not None and m.volume_ratio >= settings.volume_spike_ratio
    return big or spike


def _prev_trading_day(s, code: str, day: date) -> date:
    d = s.scalar(select(PriceDaily.day).where(PriceDaily.code == code, PriceDaily.day < day)
                 .order_by(PriceDaily.day.desc()).limit(1))
    return d or day - timedelta(days=1)


def gather_candidates(s, m: MetricDaily) -> list[dict]:
    prev = _prev_trading_day(s, m.code, m.day)
    cands: list[dict] = []
    for d in s.scalars(select(Disclosure).where(Disclosure.code == m.code, Disclosure.filed_day >= prev,
                                                Disclosure.filed_day <= m.day)):
        cands.append(dict(type="공시", title=d.title, url=d.url, day=d.filed_day.isoformat()))
    start = datetime.combine(prev, datetime.min.time())
    end = datetime.combine(m.day, datetime.max.time())
    seen = set()
    q = select(News).join(NewsStock, NewsStock.news_id == News.id) \
        .where(NewsStock.code == m.code, News.published_at >= start, News.published_at <= end) \
        .order_by(News.published_at.desc())
    for n in s.scalars(q):
        if n.issue_key in seen:
            continue
        seen.add(n.issue_key)
        cands.append(dict(type="뉴스", title=n.title, url=n.url, day=n.published_at.strftime("%Y-%m-%d %H:%M"),
                          publisher=n.publisher))
        if len(seen) >= 5:
            break
    # 같은 테마 종목의 같은 방향 동반 움직임
    sign = 1 if (m.ret_1d or 0) >= 0 else -1
    for tm in s.scalars(select(ThemeMember).where(ThemeMember.code == m.code)):
        peers = list(s.scalars(select(ThemeMember.code).where(ThemeMember.theme_id == tm.theme_id,
                                                              ThemeMember.code != m.code)))
        if not peers:
            continue
        moved = s.scalars(select(MetricDaily).where(MetricDaily.day == m.day, MetricDaily.code.in_(peers))).all()
        same = [p for p in moved if p.ret_1d is not None and p.ret_1d * sign >= settings.move_threshold_pct / 2]
        if len(same) >= 2:
            theme = s.get(Theme, tm.theme_id)
            cands.append(dict(type="테마 동반", title=f"{theme.name} 테마 {len(same)}종목이 같은 방향으로 "
                                                   f"{settings.move_threshold_pct / 2:.0f}% 이상 움직임",
                              url=f"/theme/{tm.theme_id}", day=m.day.isoformat()))
    return cands


def ai_summary(name: str, m: MetricDaily, cands: list[dict]) -> str | None:
    if not available() or not cands:
        return None
    listing = "\n".join(f"[{i}] ({c['type']}) {c['title']}" for i, c in enumerate(cands))
    prompt = (f"종목: {name}, 등락률 {m.ret_1d:+.1f}%, 거래량 20일 평균 대비 {m.volume_ratio or 0:.1f}배\n"
              f"자료:\n{listing}\n\n가장 관련 있어 보이는 자료 번호와 요약을 JSON으로 답하라: "
              '{"cited": [번호], "summary": "두 문장 이내"}')
    try:
        res = generate_json(prompt, SYSTEM)
    except LLMUnavailable as e:
        log.info("원인 요약 생략: %s", e)
        return None
    cited = [i for i in res.get("cited", []) if isinstance(i, int) and 0 <= i < len(cands)]
    if not cited or not res.get("summary"):
        return None
    return f"{res['summary'].strip()} (근거: " + ", ".join(f"[{i + 1}]" for i in cited) + ")"


def explain_moves(as_of: date, use_llm: bool = True) -> int:
    rows = []
    with session_scope() as s:
        ms = [m for m in s.scalars(select(MetricDaily).where(MetricDaily.day == as_of)) if is_move(m)]
        for m in ms:
            cands = gather_candidates(s, m)
            stock = s.get(Stock, m.code)
            name = stock.name if stock else m.code
            summary = ai_summary(name, m, cands) if use_llm else None
            rows.append(dict(code=m.code, day=as_of, change_pct=m.ret_1d or 0.0, volume_ratio=m.volume_ratio,
                             candidates=cands,
                             unexplained=not any(c["type"] in ("공시", "뉴스") for c in cands),
                             ai_summary=summary))
        return upsert(s, MoveExplanation, rows, ["code", "day"])
