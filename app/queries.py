"""화면용 조회. 화면은 배치가 미리 계산한 결과를 읽기만 한다."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select

from analysis.risk import active_flags
from analysis.themes import expand_keyword, stock_search
from db.models import (Disclosure, Financial, IndexDaily, JobRun, MetricDaily, MoveExplanation, News, NewsStock,
                       PriceDaily, Score, Stock, Theme, ThemeMember)

KR_INDICES = ["KS11", "KQ11"]
GLOBAL_INDICES = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^N225", "^HSI", "^GDAXI", "^STOXX"]

SORTS = {
    "score": ("AI 종합점수 높은 순", lambda r: r["score"], True),
    "ret_1d": ("1일 상승률 높은 순", lambda r: r["m"].ret_1d, True),
    "ret_1d_asc": ("1일 하락률 높은 순", lambda r: r["m"].ret_1d, False),
    "ret_5d": ("5일 상승률 높은 순", lambda r: r["m"].ret_5d, True),
    "ret_1m": ("1개월 상승률 높은 순", lambda r: r["m"].ret_1m, True),
    "vol": ("변동성 높은 순 (연환산)", lambda r: r["m"].vol_annual, True),
    "cap": ("시가총액 높은 순", lambda r: r["m"].market_cap, True),
    "volume": ("거래량 많은 순", lambda r: r["m"].volume, True),
    "volume_ratio": ("거래량 증가율 높은 순", lambda r: r["m"].volume_ratio, True),
    "exposure": ("테마 노출도 높은 순", lambda r: r["exposure"], True),
}


def as_of_day(s) -> date | None:
    return s.scalar(select(func.max(MetricDaily.day)))


def is_demo(s) -> bool:
    return s.scalar(select(func.count()).select_from(Stock).where(Stock.source == "DEMO")) > 0


def latest_indices(s, symbols: list[str]) -> list[IndexDaily]:
    out = []
    for sym in symbols:
        row = s.scalar(select(IndexDaily).where(IndexDaily.symbol == sym).order_by(IndexDaily.day.desc()).limit(1))
        if row:
            out.append(row)
    return out


def dashboard(s) -> dict:
    day = as_of_day(s)
    themes = [t for t in s.scalars(select(Theme)) if t.ret_1d is not None]
    up = sorted([t for t in themes if t.ret_1d > 0], key=lambda t: t.ret_1d, reverse=True)[:10]
    down = sorted([t for t in themes if t.ret_1d < 0], key=lambda t: t.ret_1d)[:10]
    spikes = []
    if day:
        q = select(MetricDaily, Stock).join(Stock, Stock.code == MetricDaily.code).where(
            MetricDaily.day == day, MetricDaily.volume_ratio >= 3).order_by(MetricDaily.volume_ratio.desc()).limit(10)
        spikes = s.execute(q).all()
    moves = []
    if day:
        q = select(MoveExplanation, Stock).join(Stock, Stock.code == MoveExplanation.code).where(
            MoveExplanation.day == day).order_by(MoveExplanation.unexplained.desc(),
                                                 func.abs(MoveExplanation.change_pct).desc()).limit(12)
        moves = s.execute(q).all()
    news, seen = [], set()
    for n in s.scalars(select(News).order_by(News.published_at.desc()).limit(40)):
        if n.issue_key in seen:
            continue
        seen.add(n.issue_key)
        news.append(n)
        if len(news) >= 10:
            break
    keywords = sorted([t for t in s.scalars(select(Theme)) if t.news_7d], key=lambda t: t.news_7d, reverse=True)[:10]
    return dict(day=day, kr=latest_indices(s, KR_INDICES), glob=latest_indices(s, GLOBAL_INDICES),
                up=up, down=down, spikes=spikes, moves=moves, news=news, keywords=keywords,
                runs=list(s.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(8))))


def member_rows(s, members: list[ThemeMember], day: date | None) -> list[dict]:
    rows = {}
    for mem in members:
        stock = s.get(Stock, mem.code)
        m = s.get(MetricDaily, (mem.code, day)) if day else None
        if stock is None or m is None:
            continue
        sc = s.get(Score, (mem.code, mem.theme_id, day))
        r = rows.get(mem.code)
        if r is None or (sc and sc.total is not None and (r["score"] is None or sc.total > r["score"])):
            rows[mem.code] = dict(stock=stock, m=m, mem=mem, sc=sc, score=sc.total if sc else None,
                                  exposure=mem.exposure_pct, flags=active_flags(s, mem.code, day))
    return list(rows.values())


def sort_rows(rows: list[dict], key: str) -> list[dict]:
    label, fn, desc = SORTS.get(key, SORTS["score"])
    have = [r for r in rows if fn(r) is not None]
    none = [r for r in rows if fn(r) is None]
    return sorted(have, key=fn, reverse=desc) + none


def search(s, q: str, sort: str) -> dict:
    day = as_of_day(s)
    themes, expanded = expand_keyword(s, q)
    members = list(s.scalars(select(ThemeMember).where(ThemeMember.theme_id.in_([t.id for t in themes])))) if themes else []
    direct = stock_search(s, q)
    rows = member_rows(s, members, day)
    have = {r["stock"].code for r in rows}
    for st in direct:  # 종목명으로 직접 찾은 결과
        if st.code in have:
            continue
        m = s.get(MetricDaily, (st.code, day)) if day else None
        if m:
            rows.append(dict(stock=st, m=m, mem=None, sc=None, score=None, exposure=None,
                             flags=active_flags(s, st.code, day)))
    return dict(day=day, themes=themes, expanded=expanded, rows=sort_rows(rows, sort), sort=sort,
                sort_label=SORTS.get(sort, SORTS["score"])[0])


def stock_detail(s, code: str) -> dict | None:
    stock = s.get(Stock, code)
    if not stock:
        return None
    day = as_of_day(s)
    m = s.get(MetricDaily, (code, day)) if day else None
    prices = list(s.scalars(select(PriceDaily).where(PriceDaily.code == code).order_by(PriceDaily.day.desc()).limit(260)))[::-1]
    mems = list(s.scalars(select(ThemeMember).where(ThemeMember.code == code)))
    themes = {t.id: t for t in s.scalars(select(Theme).where(Theme.id.in_([x.theme_id for x in mems])))} if mems else {}
    scores = {sc.theme_id: sc for sc in s.scalars(select(Score).where(Score.code == code, Score.day == day))}
    moves = list(s.scalars(select(MoveExplanation).where(MoveExplanation.code == code)
                           .order_by(MoveExplanation.day.desc()).limit(5)))
    since = (day or date.today()) - timedelta(days=60)
    discl = list(s.scalars(select(Disclosure).where(Disclosure.code == code, Disclosure.filed_day >= since)
                           .order_by(Disclosure.filed_day.desc()).limit(15)))
    news = list(s.scalars(select(News).join(NewsStock, NewsStock.news_id == News.id).where(NewsStock.code == code)
                          .order_by(News.published_at.desc()).limit(10)))
    fin = s.scalar(select(Financial).where(Financial.code == code).order_by(Financial.period.desc()).limit(1))
    return dict(stock=stock, m=m, day=day, prices=prices, members=mems, themes=themes, scores=scores, moves=moves,
                discl=discl, news=news, fin=fin, flags=active_flags(s, code, day) if day else [],
                last_price=prices[-1] if prices else None)


def theme_detail(s, theme_id: str, sort: str) -> dict | None:
    t = s.get(Theme, theme_id)
    if not t:
        return None
    day = as_of_day(s)
    members = list(s.scalars(select(ThemeMember).where(ThemeMember.theme_id == theme_id)))
    return dict(theme=t, day=day, rows=sort_rows(member_rows(s, members, day), sort), sort=sort,
                sort_label=SORTS.get(sort, SORTS["score"])[0])
