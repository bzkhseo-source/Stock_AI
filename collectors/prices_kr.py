"""국내 시세 수집.

- 종목 목록: FinanceDataReader StockListing('KRX')
- 일별 전 종목 스냅샷: pykrx get_market_ohlcv / get_market_cap (KRX 계정 로그인 필요)
- 국내 지수: FinanceDataReader (KS11, KQ11)

주의: pykrx와 FinanceDataReader는 공개 웹 데이터를 가져오는 비공식 라이브러리다.
비공개 베타용이며 정식 출시 전 재배포가 허용된 소스로 교체한다.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import pandas as pd

import config  # noqa: F401  .env를 pykrx import 전에 읽기 위해
from collectors.common import EmptyResultError, job_run, require_rows
from db.models import IndexDaily, PriceDaily, RiskFlag, Stock
from db.session import session_scope, upsert

log = logging.getLogger("prices_kr")

MARKET_NAMES = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KOSDAQ GLOBAL": "KOSDAQ", "KONEX": "KONEX"}


def classify_kind(code: str, name: str) -> str:
    if "스팩" in name:
        return "스팩"
    if code[-1] != "0" or name.endswith(("우", "우B", "우C")) or "우(" in name:
        return "우선주"
    return "보통주"


def collect_stock_master() -> int:
    """종목 기본정보. 관리종목 표시가 있으면 리스크 플래그로 남긴다."""
    import FinanceDataReader as fdr

    with job_run("stock_master") as st:
        df = fdr.StockListing("KRX")
        require_rows(df, "KRX 종목 목록")
        rows, flags = [], []
        today = date.today()
        for r in df.to_dict("records"):
            code = str(r.get("Code", "")).zfill(6)
            name = str(r.get("Name", "")).strip()
            market = MARKET_NAMES.get(str(r.get("Market", "")), str(r.get("Market", "")))
            if not code or not name:
                continue
            rows.append(dict(code=code, name=name, market=market, country="KR", currency="KRW",
                             kind=classify_kind(code, name), sector=str(r.get("Dept") or "") or None,
                             source="FinanceDataReader"))
            dept = str(r.get("Dept") or "")
            if "관리" in dept:
                flags.append(dict(code=code, kind="관리종목", since=today, evidence=f"KRX 소속부: {dept}"))
        with session_scope() as s:
            st["rows"] = upsert(s, Stock, rows, ["code"])
            upsert(s, RiskFlag, flags, ["code", "kind", "since"]) if flags else None
        return st["rows"]


def _pykrx():
    from pykrx import stock  # import 시점에 KRX_ID/KRX_PW로 로그인
    return stock


class KrxLoginRequired(RuntimeError):
    pass


def collect_snapshot(day: date) -> int:
    """하루치 전 종목 OHLCV + 시가총액 (pykrx). 휴장일이면 0. 로그인 없으면 KrxLoginRequired."""
    stock = _pykrx()
    ds = day.strftime("%Y%m%d")
    try:
        ohlcv = stock.get_market_ohlcv(ds, market="ALL")
    except Exception as e:  # pykrx 1.2.x: 로그인 없으면 KeyError 등으로 실패
        raise KrxLoginRequired(f"pykrx 시세 조회 실패 ({type(e).__name__}). KRX_ID/KRX_PW를 확인하세요.") from e
    if ohlcv is None or ohlcv.empty or "종가" not in ohlcv or ohlcv["종가"].sum() == 0:
        return 0  # 휴장일
    try:
        cap = None if "시가총액" in ohlcv.columns else stock.get_market_cap(ds, market="ALL")
    except Exception as e:
        cap = None
        log.warning("시가총액 조회 실패(%s): %s", ds, e)
    if "시가총액" in ohlcv.columns:  # pykrx 버전에 따라 시세에 시가총액이 이미 들어 있음
        df = ohlcv
    else:
        if cap is None or cap.empty or "시가총액" not in cap:
            log.warning("시가총액이 비어 있습니다. .env의 KRX_ID/KRX_PW(KRX 정보데이터시스템 계정)를 확인하세요.")
            cap = pd.DataFrame(columns=["시가총액"])
        df = ohlcv.join(cap[["시가총액"]], how="left")
    rows = []
    for code, r in df.iterrows():
        if not r.get("종가"):
            continue
        rows.append(dict(code=str(code).zfill(6), day=day, open=float(r["시가"]), high=float(r["고가"]),
                         low=float(r["저가"]), close=float(r["종가"]), volume=float(r["거래량"]),
                         value=float(r["거래대금"]) if "거래대금" in r and pd.notna(r["거래대금"]) else None,
                         market_cap=float(r["시가총액"]) if pd.notna(r.get("시가총액")) else None,
                         source="pykrx", collected_at=datetime.now()))
    with session_scope() as s:
        return upsert(s, PriceDaily, rows, ["code", "day"])


def _f(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def snapshot_from_listing(day: date) -> int:
    """KRX 로그인 없이 쓰는 대체 경로: FinanceDataReader 종목 목록에 들어 있는 최근 거래일 시세·시가총액.
    날짜 정보가 없으므로 '가장 최근 거래일(day)'에만 사용한다."""
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    require_rows(df, "KRX 종목 목록 (시세 스냅샷)")
    rows = []
    for r in df.to_dict("records"):
        close = _f(r.get("Close"))
        if not close:
            continue
        rows.append(dict(code=str(r["Code"]).zfill(6), day=day, open=_f(r.get("Open")), high=_f(r.get("High")),
                         low=_f(r.get("Low")), close=close, volume=_f(r.get("Volume")), value=_f(r.get("Amount")),
                         market_cap=_f(r.get("Marcap")), source="FinanceDataReader(listing)",
                         collected_at=datetime.now()))
    with session_scope() as s:
        return upsert(s, PriceDaily, rows, ["code", "day"])


def history_from_fdr(start: date, end: date) -> int:
    """KRX 로그인 없이 과거 시세 적재 (종목별 호출이라 느림: 전 종목 약 20~40분). 시가총액은 없음."""
    import FinanceDataReader as fdr
    from sqlalchemy import select

    with session_scope() as s:
        codes = list(s.scalars(select(Stock.code).where(Stock.country == "KR", Stock.kind == "보통주",
                                                        Stock.market.in_(["KOSPI", "KOSDAQ"]))))
    total = 0
    for i, code in enumerate(codes, 1):
        try:
            df = fdr.DataReader(code, start, end)
        except Exception as e:
            log.warning("%s 과거 시세 실패: %s", code, e)
            continue
        if df is None or df.empty:
            continue
        rows = [dict(code=code, day=d.date(), open=_f(r.get("Open")), high=_f(r.get("High")), low=_f(r.get("Low")),
                     close=float(r["Close"]), volume=_f(r.get("Volume")),
                     value=(_f(r.get("Volume")) or 0) * float(r["Close"]) or None,  # 근사 거래대금 = 거래량 × 종가
                     source="FinanceDataReader", collected_at=datetime.now())
                for d, r in df.iterrows() if r.get("Close")]
        with session_scope() as s:
            total += upsert(s, PriceDaily, rows, ["code", "day"])
        if i % 200 == 0:
            log.info("과거 시세 %d/%d 종목", i, len(codes))
        time.sleep(0.05)
    return total


def trading_days(start: date, end: date) -> list[date]:
    """KOSPI 지수의 실제 거래일로 영업일 목록을 만든다 (휴장일 자동 반영)."""
    import FinanceDataReader as fdr

    idx = fdr.DataReader("KS11", start, end)
    require_rows(idx, "KOSPI 지수 (거래일 계산)")
    return [d.date() for d in idx.index]


def collect_prices(days: int = 1, end: date | None = None) -> int:
    """최근 N 거래일 수집. pykrx(KRX 로그인)를 먼저 쓰고, 로그인이 없으면 FinanceDataReader로 대체한다."""
    end = end or date.today()
    start = end - timedelta(days=int(days * 1.6) + 10)
    with job_run(f"prices_kr({days}d)") as st:
        dlist = trading_days(start, end)[-days:]
        total = 0
        try:
            for d in dlist:
                n = collect_snapshot(d)
                log.info("%s: %d 종목", d, n)
                total += n
                time.sleep(0.5)  # 과도한 요청 방지
        except KrxLoginRequired as e:
            log.warning("%s → FinanceDataReader 대체 경로로 수집합니다.", e)
            st["message"] = "KRX 로그인 없음: FinanceDataReader 대체 경로 사용"
            if len(dlist) > 1:
                total += history_from_fdr(dlist[0], dlist[-1])
            total += snapshot_from_listing(dlist[-1])  # 최근일은 시가총액·거래대금 포함 스냅샷으로 덮어씀
        if total == 0:
            raise EmptyResultError("국내 시세가 한 건도 적재되지 않았습니다. KRX 계정과 네트워크를 확인하세요.")
        st["rows"] = total
        return total


def collect_kr_indices(days: int = 400) -> int:
    import FinanceDataReader as fdr

    with job_run("indices_kr") as st:
        rows = []
        for sym, name in [("KS11", "KOSPI"), ("KQ11", "KOSDAQ")]:
            df = fdr.DataReader(sym, date.today() - timedelta(days=days))
            require_rows(df, f"{name} 지수")
            prev = None
            for d, r in df.iterrows():
                close = float(r["Close"])
                rows.append(dict(symbol=sym, day=d.date(), name=name, close=close,
                                 change_pct=(close / prev - 1) * 100 if prev else None,
                                 source="FinanceDataReader", collected_at=datetime.now()))
                prev = close
        with session_scope() as s:
            st["rows"] = upsert(s, IndexDaily, rows, ["symbol", "day"])
        return st["rows"]
