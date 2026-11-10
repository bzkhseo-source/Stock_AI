"""테마 사전 로드, 키워드 확장, 테마 구성 종목 관리, 테마 트렌드 분류."""
from __future__ import annotations

import csv
import statistics
from datetime import date, datetime, timedelta

import yaml
from sqlalchemy import func, select

from config import BASE_DIR
from db.models import MetricDaily, News, NewsStock, Stock, Theme, ThemeMember
from db.session import session_scope, upsert

THEMES_FILE = BASE_DIR / "data" / "themes.yaml"
MANUAL_FILE = BASE_DIR / "data" / "theme_members_manual.csv"

# 트렌드 분류 기준 (화면에 공개, 베타 데이터로 조정)
TREND_RULES = [
    ("과열 주의", "5일 수익률 ≥ 15% 그리고 거래대금 20일 평균 대비 ≥ 3배"),
    ("급부상", "거래대금 20일 평균 대비 ≥ 2배 그리고 5일 수익률 ≥ 5%"),
    ("하락 전환", "5일 수익률 ≤ -5%"),
    ("상승 지속", "5일 수익률 > 0% 그리고 1개월 수익률 ≥ 10%"),
    ("초기 형성", "최근 7일 관련 뉴스 ≥ 10건 그리고 5일 수익률 절댓값 < 3%"),
    ("중립", "위 조건에 해당하지 않음"),
]


def load_themes() -> int:
    data = yaml.safe_load(THEMES_FILE.read_text(encoding="utf-8"))
    rows = [dict(id=t["id"], name=t["name"], keywords=t.get("keywords", []), synonyms=t.get("synonyms", []))
            for t in data]
    with session_scope() as s:
        return upsert(s, Theme, rows, ["id"])


def expand_keyword(session, q: str) -> tuple[list[Theme], list[str]]:
    """검색어 → 관련 테마와 확장 키워드."""
    q = q.strip()
    ql = q.lower()
    matched, expanded = [], [q]
    for t in session.scalars(select(Theme)):
        words = [t.name, *t.keywords, *t.synonyms]
        if any(ql in w.lower() or (len(w) >= 2 and w.lower() in ql) for w in words):
            matched.append(t)
            for w in [*t.keywords, *t.synonyms]:
                if w not in expanded:
                    expanded.append(w)
    return matched, expanded[:20]


RELATION_RANK = {"직접 관련주": 0, "핵심 수혜주": 1, "공급망 관련주": 2, "간접 관련주": 3, "테마 편입주": 4}
BASIS_RANK = {"수동 검토": 0, "사업보고서": 1, "뉴스 동반 등장": 2}


def load_manual_members() -> int:
    """data/theme_members_manual.csv (사람이 검토한 연결) → 최우선 근거."""
    if not MANUAL_FILE.exists():
        return 0
    rows = []
    with MANUAL_FILE.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("theme_id") or not r.get("code"):
                continue
            rows.append(dict(theme_id=r["theme_id"].strip(), code=r["code"].strip().zfill(6),
                             relation=r.get("relation") or "간접 관련주", evidence=r.get("evidence") or None,
                             evidence_url=r.get("evidence_url") or None, basis="수동 검토",
                             verified_at=date.today()))
    with session_scope() as s:
        return upsert(s, ThemeMember, rows, ["theme_id", "code"])


def members_from_news(min_news: int = 2, days: int = 30) -> int:
    """테마 키워드로 수집된 뉴스 제목에 2회 이상 등장한 종목 → '테마 편입주' (사업 연관성 미검증)."""
    since = datetime.now() - timedelta(days=days)
    added = 0
    with session_scope() as s:
        for t in s.scalars(select(Theme)):
            words = [*t.keywords, *t.synonyms]
            q = select(NewsStock.code, func.count()).join(News, News.id == NewsStock.news_id) \
                .where(News.query.in_(words), News.published_at >= since).group_by(NewsStock.code)
            for code, cnt in s.execute(q):
                if cnt < min_news:
                    continue
                if s.get(ThemeMember, (t.id, code)) is None:
                    s.add(ThemeMember(theme_id=t.id, code=code, relation="테마 편입주", basis="뉴스 동반 등장",
                                      evidence=f"최근 {days}일 테마 관련 뉴스 제목 {cnt}건에 등장 (사업 연관성 미검증)"))
                    added += 1
    return added


def classify_trend(ret_5d, ret_1m, value_change, news_7d) -> str:
    r5 = ret_5d or 0.0
    r1m = ret_1m or 0.0
    vc = value_change or 0.0
    if r5 >= 15 and vc >= 3:
        return "과열 주의"
    if vc >= 2 and r5 >= 5:
        return "급부상"
    if r5 <= -5:
        return "하락 전환"
    if r5 > 0 and r1m >= 10:
        return "상승 지속"
    if (news_7d or 0) >= 10 and abs(r5) < 3:
        return "초기 형성"
    return "중립"


def update_theme_stats(as_of: date) -> int:
    with session_scope() as s:
        n = 0
        week_ago = datetime.combine(as_of, datetime.min.time()) - timedelta(days=7)
        for t in s.scalars(select(Theme)):
            codes = list(s.scalars(select(ThemeMember.code).where(ThemeMember.theme_id == t.id)))
            ms = list(s.scalars(select(MetricDaily).where(MetricDaily.day == as_of, MetricDaily.code.in_(codes))))
            words = [*t.keywords, *t.synonyms]
            t.news_7d = s.scalar(select(func.count(News.id)).where(News.query.in_(words), News.published_at >= week_ago))
            if not ms:
                t.ret_1d = t.ret_5d = t.value_change = None
                t.trend = None
                t.as_of = as_of
                continue

            def med(attr):
                vals = [getattr(m, attr) for m in ms if getattr(m, attr) is not None]
                return statistics.median(vals) if vals else None

            t.ret_1d, t.ret_5d = med("ret_1d"), med("ret_5d")
            today_val = sum(m.value or 0 for m in ms)
            avg_val = sum(m.avg_value_20d or 0 for m in ms)
            t.value_change = today_val / avg_val if avg_val else None
            t.trend = classify_trend(t.ret_5d, med("ret_1m"), t.value_change, t.news_7d)
            t.as_of = as_of
            n += 1
        return n


def stock_search(session, q: str, limit: int = 20) -> list[Stock]:
    return list(session.scalars(select(Stock).where(
        (Stock.name.contains(q)) | (Stock.code == q)).order_by(func.length(Stock.name)).limit(limit)))
