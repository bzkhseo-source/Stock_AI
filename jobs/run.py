"""배치 작업 진입점.

사용법:
  python -m jobs.run init                # 테이블 생성 + 테마 사전 로드
  python -m jobs.run backfill --days 260 # 최초 1회: 과거 시세 적재
  python -m jobs.run daily               # 장 마감 후 (평일 16:30 KST 권장)
  python -m jobs.run news                # 15~30분 간격 뉴스 수집
  python -m jobs.run analyze             # 분석만 다시 계산
  python -m jobs.run verify --limit 10   # 테마 노출도 검증 (DART + Gemini)
  python -m jobs.run demo                # API 키 없이 데모 데이터로 실행
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402,F401  (.env 로드)
from sqlalchemy import func, select  # noqa: E402

from db.models import MetricDaily, Stock, Theme, ThemeMember  # noqa: E402
from db.session import init_db, session_scope  # noqa: E402

log = logging.getLogger("jobs")


def step(name: str, fn, *a, **kw):
    try:
        res = fn(*a, **kw)
        print(f"  ✔ {name}: {res}")
        return res
    except Exception as e:
        print(f"  ✘ {name}: {type(e).__name__}: {e}")
        return None


def cmd_init(_):
    from analysis.themes import load_manual_members, load_themes
    init_db()
    step("테마 사전", load_themes)
    step("수동 검토 종목", load_manual_members)


def cmd_analyze(args):
    from analysis import explain, metrics, risk, scoring, themes
    as_of, n = metrics.compute_all()
    print(f"  ✔ 지표 계산: 기준일 {as_of}, {n}종목")
    if as_of is None:
        print("  ! 시세 데이터가 없습니다. backfill 또는 demo를 먼저 실행하세요.")
        return
    step("테마 통계·트렌드", themes.update_theme_stats, as_of)
    step("원인 추적", explain.explain_moves, as_of, use_llm=not getattr(args, "no_llm", False))
    step("리스크 배지", risk.detect, as_of)
    step("종합점수", scoring.compute_scores, as_of)


def news_queries() -> list[str]:
    """테마 핵심 키워드 + 당일 급등락 종목명."""
    with session_scope() as s:
        qs = [k for t in s.scalars(select(Theme)) for k in t.keywords]
        day = s.scalar(select(func.max(MetricDaily.day)))
        if day:
            movers = s.scalars(select(MetricDaily).where(MetricDaily.day == day)
                               .order_by(func.abs(MetricDaily.ret_1d).desc()).limit(10))
            for m in movers:
                st = s.get(Stock, m.code)
                if st:
                    qs.append(st.name)
    return list(dict.fromkeys(qs))


def cmd_news(_):
    from analysis.themes import members_from_news
    from collectors.news import collect_news
    step("뉴스 수집", collect_news, news_queries())
    step("뉴스 기반 테마 편입주", members_from_news)


def cmd_verify(args):
    """뉴스로만 연결된 종목부터 사업보고서로 검증."""
    from analysis.exposure import verify_member
    with session_scope() as s:
        todo = [(m.theme_id, m.code) for m in s.scalars(
            select(ThemeMember).where(ThemeMember.basis == "뉴스 동반 등장").limit(args.limit))]
    for tid, code in todo:
        step(f"노출도 {tid}/{code}", verify_member, tid, code)


def cmd_daily(args):
    from collectors import dart, prices_global, prices_kr
    from config import settings
    cmd_init(args)
    step("종목 목록", prices_kr.collect_stock_master)
    step("국내 시세", prices_kr.collect_prices, days=args.days)
    step("국내 지수", prices_kr.collect_kr_indices, days=30)
    step("해외 지수", prices_global.collect_global_indices)
    if settings.dart_api_key:
        with session_scope() as s:
            missing = s.scalar(select(func.count()).select_from(Stock).where(
                Stock.country == "KR", Stock.dart_corp_code.is_(None)))
        if missing:
            step("DART 고유번호", dart.sync_corp_codes)
        step("DART 공시", dart.collect_disclosures, days=3)
        with session_scope() as s:
            codes = list(s.scalars(select(ThemeMember.code).distinct()))
        step("DART 재무", dart.collect_financials, codes)
    else:
        print("  ! DART_API_KEY가 없어 공시·재무 수집을 건너뜁니다.")
    cmd_news(args)
    cmd_analyze(args)


def cmd_backfill(args):
    from collectors import prices_kr
    cmd_init(args)
    step("종목 목록", prices_kr.collect_stock_master)
    step(f"국내 시세 {args.days}일", prices_kr.collect_prices, days=args.days)
    step("국내 지수", prices_kr.collect_kr_indices, days=int(args.days * 1.6))
    cmd_analyze(args)


def cmd_demo(args):
    from jobs.demo import seed
    init_db()
    step("데모 데이터", seed, reset=True)
    args.no_llm = True
    cmd_analyze(args)


def main():
    from config import settings
    if os.getenv("GITHUB_ACTIONS") and settings.is_sqlite:
        print("✘ DATABASE_URL이 비어 있습니다. GitHub 저장소 Settings → Secrets and variables → Actions 에 "
              "DATABASE_URL(Neon 연결 문자열)을 등록하세요.")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="왜올랐지 배치 작업")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("analyze")
    a.add_argument("--no-llm", action="store_true")
    sub.add_parser("news")
    v = sub.add_parser("verify")
    v.add_argument("--limit", type=int, default=10)
    d = sub.add_parser("daily")
    d.add_argument("--days", type=int, default=1)
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=int, default=260)
    sub.add_parser("demo")
    args = p.parse_args()
    print(f"[{args.cmd}]")
    {"init": cmd_init, "analyze": cmd_analyze, "news": cmd_news, "verify": cmd_verify,
     "daily": cmd_daily, "backfill": cmd_backfill, "demo": cmd_demo}[args.cmd](args)


if __name__ == "__main__":
    main()
