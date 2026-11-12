"""AI 종합점수 (트렌드 적합도, 100점). 수익률이나 상승 확률이 아니다.

누락 항목은 만점 처리하지 않는다. 평가 가능한 항목만으로 100점 환산하고 데이터 충족률을 함께 저장한다.
충족률 60% 미만이면 '평가 불가'.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from db.models import Financial, MetricDaily, News, NewsStock, Score, Theme, ThemeMember
from db.session import session_scope, upsert

WEIGHTS = {"momentum": 20, "volume": 15, "industry": 20, "news": 15, "relation": 15, "financial": 15}
LABELS = {"momentum": "주가 모멘텀", "volume": "거래량·거래대금", "industry": "산업 성장성",
          "news": "뉴스·이벤트 모멘텀", "relation": "사업 연관성", "financial": "재무 건전성"}
METHODS = {
    "momentum": "1개월·3개월 수익률 평균의 테마 내 백분위",
    "volume": "거래량 20일 평균 대비 배율의 테마 내 백분위",
    "industry": "테마 거래대금 증가율과 테마 5일 수익률",
    "news": "최근 7일 종목명이 제목에 나온 뉴스 건수 (10건 이상 만점)",
    "relation": "연관성 단계 (직접 15 · 핵심 12 · 공급망 10 · 간접 6 · 편입 3)",
    "financial": "영업이익 흑자 여부 + 부채비율 (DART 사업보고서)",
}
RELATION_POINTS = {"직접 관련주": 15, "핵심 수혜주": 12, "공급망 관련주": 10, "간접 관련주": 6, "테마 편입주": 3}
MIN_COVERAGE = 60.0


def grade(total: float | None) -> str:
    if total is None:
        return "평가 불가"
    if total >= 80:
        return "높음"
    if total >= 65:
        return "양호"
    if total >= 50:
        return "관찰 필요"
    return "낮음"


def combine(parts: dict[str, float | None]) -> tuple[float | None, float, str]:
    """항목 점수(각 배점 기준) → (총점, 데이터 충족률 %, 등급). 순수 함수."""
    avail = {k: v for k, v in parts.items() if v is not None}
    coverage = sum(WEIGHTS[k] for k in avail) / sum(WEIGHTS.values()) * 100
    if coverage < MIN_COVERAGE or not avail:
        return None, round(coverage, 1), "평가 불가"
    total = sum(avail.values()) / sum(WEIGHTS[k] for k in avail) * 100
    total = round(total, 1)
    return total, round(coverage, 1), grade(total)


def percentile(value: float | None, population: list[float]) -> float | None:
    if value is None:
        return None
    pop = [p for p in population if p is not None]
    if len(pop) <= 1:
        return 0.5
    below = sum(1 for p in pop if p < value)
    equal = sum(1 for p in pop if p == value)
    return (below + 0.5 * (equal - 1)) / (len(pop) - 1)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def financial_points(f: Financial | None) -> float | None:
    if f is None or (f.operating_income is None and f.total_liabilities is None):
        return None
    pts = 0.0
    if f.operating_income is not None and f.operating_income > 0:
        pts += 7.5
    if f.total_liabilities is not None and f.total_equity and f.total_equity > 0:
        ratio = f.total_liabilities / f.total_equity * 100
        pts += 7.5 if ratio < 100 else 4.0 if ratio < 200 else 0.0
    return pts


def compute_scores(as_of: date) -> int:
    rows = []
    with session_scope() as s:
        has_news = (s.scalar(select(func.count(News.id))) or 0) > 0
        week_ago = datetime.combine(as_of, datetime.min.time()) - timedelta(days=7)
        news_counts = dict(s.execute(select(NewsStock.code, func.count()).join(News, News.id == NewsStock.news_id)
                                     .where(News.published_at >= week_ago).group_by(NewsStock.code)).all())
        latest_fin: dict[str, Financial] = {}
        for f in s.scalars(select(Financial)):
            if f.code not in latest_fin or f.period > latest_fin[f.code].period:
                latest_fin[f.code] = f
        for t in s.scalars(select(Theme)):
            members = list(s.scalars(select(ThemeMember).where(ThemeMember.theme_id == t.id)))
            if not members:
                continue
            ms = {m.code: m for m in s.scalars(select(MetricDaily).where(
                MetricDaily.day == as_of, MetricDaily.code.in_([x.code for x in members])))}

            def mom(m):
                vals = [v for v in (m.ret_1m, m.ret_3m) if v is not None]
                return sum(vals) / len(vals) if vals else None

            mom_pop = [mom(m) for m in ms.values()]
            vol_pop = [m.volume_ratio for m in ms.values()]
            industry = None
            if t.value_change is not None or t.ret_5d is not None:
                industry = (_clip(((t.value_change or 1) - 0.5) / 1.5) * 10 + _clip(((t.ret_5d or 0) + 5) / 15) * 10)
            for mem in members:
                m = ms.get(mem.code)
                if m is None:
                    continue
                p_mom = percentile(mom(m), mom_pop)
                p_vol = percentile(m.volume_ratio, vol_pop)
                news_n = news_counts.get(mem.code, 0) if has_news else None
                fin = latest_fin.get(mem.code)
                parts = {
                    "momentum": None if p_mom is None else round(p_mom * 20, 1),
                    "volume": None if p_vol is None else round(p_vol * 15, 1),
                    "industry": None if industry is None else round(industry, 1),
                    "news": None if news_n is None else round(min(news_n, 10) / 10 * 15, 1),
                    "relation": float(RELATION_POINTS.get(mem.relation, 3)),
                    "financial": financial_points(fin),
                }
                total, coverage, g = combine(parts)
                rows.append(dict(code=mem.code, theme_id=t.id, day=as_of, **parts, total=total,
                                 coverage=coverage, grade=g))
        return upsert(s, Score, rows, ["code", "theme_id", "day"])
