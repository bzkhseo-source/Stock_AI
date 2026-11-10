"""데이터 모델. 원천(수집) / 분석(배치가 미리 계산) / 사용자·베타 세 묶음."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ───────────────────────── 원천 데이터 ─────────────────────────
class Stock(Base):
    __tablename__ = "stocks"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # 국내 6자리, 해외 티커
    name: Mapped[str] = mapped_column(String(100), index=True)
    market: Mapped[str] = mapped_column(String(20))  # KOSPI/KOSDAQ/KONEX/NASDAQ...
    country: Mapped[str] = mapped_column(String(5), default="KR")
    currency: Mapped[str] = mapped_column(String(5), default="KRW")
    kind: Mapped[str] = mapped_column(String(20), default="보통주")  # 보통주/우선주/ETF/스팩
    status: Mapped[str] = mapped_column(String(20), default="상장")  # 상장/거래정지/상장폐지예정
    dart_corp_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class PriceDaily(Base):
    __tablename__ = "prices_daily"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)  # 거래대금
    market_cap: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class IndexDaily(Base):
    __tablename__ = "index_daily"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    close: Mapped[float] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Financial(Base):
    __tablename__ = "financials"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    period: Mapped[str] = mapped_column(String(10), primary_key=True)  # 예: 2025
    revenue: Mapped[float | None] = mapped_column(Float)
    operating_income: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    total_liabilities: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Disclosure(Base):
    __tablename__ = "disclosures"
    rcept_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    code: Mapped[str | None] = mapped_column(String(20), index=True)
    corp_name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(300))
    filed_day: Mapped[date] = mapped_column(Date, index=True)
    url: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40), default="DART")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class News(Base):
    __tablename__ = "news"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(600), unique=True)
    title: Mapped[str] = mapped_column(String(400))
    publisher: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # 최초 게시일
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    query: Mapped[str | None] = mapped_column(String(100))
    issue_key: Mapped[str | None] = mapped_column(String(64), index=True)  # 중복 기사 묶음
    source: Mapped[str] = mapped_column(String(40), default="GoogleNewsRSS")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class NewsStock(Base):
    """뉴스 제목에 종목명이 등장한 연결 (뉴스 동반 등장 = 테마 편입주 근거로만 사용)."""
    __tablename__ = "news_stocks"
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), primary_key=True)


# ───────────────────────── 분석 결과 ─────────────────────────
class Theme(Base):
    __tablename__ = "themes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    synonyms: Mapped[list] = mapped_column(JSON, default=list)
    trend: Mapped[str | None] = mapped_column(String(20))  # 급부상/상승 지속/초기 형성/과열 주의/하락 전환/중립
    ret_1d: Mapped[float | None] = mapped_column(Float)
    ret_5d: Mapped[float | None] = mapped_column(Float)
    value_change: Mapped[float | None] = mapped_column(Float)  # 거래대금 증가율
    news_7d: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date | None] = mapped_column(Date)


RELATIONS = ["직접 관련주", "핵심 수혜주", "공급망 관련주", "간접 관련주", "테마 편입주"]


class ThemeMember(Base):
    __tablename__ = "theme_members"
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    relation: Mapped[str] = mapped_column(String(20), default="테마 편입주")
    exposure_pct: Mapped[float | None] = mapped_column(Float)  # 테마 노출도 %
    evidence: Mapped[str | None] = mapped_column(Text)  # 근거 문장 (원문 그대로)
    evidence_url: Mapped[str | None] = mapped_column(String(300))
    basis: Mapped[str] = mapped_column(String(30), default="뉴스 동반 등장")  # 수동 검토/사업보고서/뉴스 동반 등장
    verified_at: Mapped[date | None] = mapped_column(Date)


class MetricDaily(Base):
    __tablename__ = "metrics_daily"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float | None] = mapped_column(Float)
    ret_1d: Mapped[float | None] = mapped_column(Float)
    ret_5d: Mapped[float | None] = mapped_column(Float)
    ret_1m: Mapped[float | None] = mapped_column(Float)
    ret_3m: Mapped[float | None] = mapped_column(Float)
    ret_6m: Mapped[float | None] = mapped_column(Float)
    ret_1y: Mapped[float | None] = mapped_column(Float)
    vol_daily: Mapped[float | None] = mapped_column(Float)  # 일간 수익률 표준편차(%) 60일
    vol_annual: Mapped[float | None] = mapped_column(Float)  # 연환산(%)
    mdd_1y: Mapped[float | None] = mapped_column(Float)  # %
    volume: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)  # 당일/20일 평균
    value: Mapped[float | None] = mapped_column(Float)
    avg_value_20d: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)


class Score(Base):
    __tablename__ = "scores"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    theme_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    momentum: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    industry: Mapped[float | None] = mapped_column(Float)
    news: Mapped[float | None] = mapped_column(Float)
    relation: Mapped[float | None] = mapped_column(Float)
    financial: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)
    coverage: Mapped[float] = mapped_column(Float)  # 데이터 충족률 %
    grade: Mapped[str] = mapped_column(String(20))


class MoveExplanation(Base):
    __tablename__ = "move_explanations"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    change_pct: Mapped[float] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    candidates: Mapped[list] = mapped_column(JSON, default=list)  # [{type, title, url, day}]
    unexplained: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_summary: Mapped[str | None] = mapped_column(Text)  # "AI 해석" 라벨로 표시


class RiskFlag(Base):
    __tablename__ = "risk_flags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    since: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(String(300))
    __table_args__ = (UniqueConstraint("code", "kind", "since"),)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)


# ───────────────────────── 사용자·베타 ─────────────────────────
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    tier: Mapped[str] = mapped_column(String(10), default="beta")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Watchlist(Base):
    __tablename__ = "watchlists"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), primary_key=True)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    event: Mapped[str] = mapped_column(String(40))
    feature_id: Mapped[str | None] = mapped_column(String(40))
    path: Mapped[str | None] = mapped_column(String(300))
    at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[str] = mapped_column(String(300))
    rating: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
