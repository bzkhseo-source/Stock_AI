"""가격 지표 계산 (코드로만 계산, LLM 사용 안 함)."""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select

from db.models import MetricDaily, PriceDaily
from db.session import session_scope, upsert

PERIODS = {"ret_1d": 1, "ret_5d": 5, "ret_1m": 21, "ret_3m": 63, "ret_6m": 126, "ret_1y": 252}
TRADING_DAYS = 252


def _ret(close: pd.Series, n: int) -> float | None:
    if len(close) <= n:
        return None
    base = close.iloc[-1 - n]
    if not base:
        return None
    return float((close.iloc[-1] / base - 1) * 100)


def compute_for_series(df: pd.DataFrame) -> dict:
    """df: 한 종목의 일별 데이터 (day 오름차순, columns: close, high, low, volume, value, market_cap)."""
    df = df.sort_values("day")
    close = df["close"].astype(float)
    out: dict = {"close": float(close.iloc[-1])}
    for k, n in PERIODS.items():
        out[k] = _ret(close, n)

    rets = close.pct_change().dropna().tail(60)
    if len(rets) >= 20:
        sd = float(rets.std(ddof=1))
        out["vol_daily"] = sd * 100
        out["vol_annual"] = sd * math.sqrt(TRADING_DAYS) * 100
    else:
        out["vol_daily"] = out["vol_annual"] = None

    last_year = close.tail(TRADING_DAYS)
    if len(last_year) >= 20:
        dd = last_year / last_year.cummax() - 1
        out["mdd_1y"] = float(dd.min() * 100)
    else:
        out["mdd_1y"] = None

    vol = df["volume"].astype(float)
    out["volume"] = float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else None
    prev20 = vol.iloc[-21:-1]
    out["volume_ratio"] = float(vol.iloc[-1] / prev20.mean()) if len(prev20) >= 10 and prev20.mean() > 0 else None

    val = df["value"].astype(float) if "value" in df else pd.Series(dtype=float)
    out["value"] = float(val.iloc[-1]) if len(val) and pd.notna(val.iloc[-1]) else None
    v20 = val.tail(20).dropna()
    out["avg_value_20d"] = float(v20.mean()) if len(v20) >= 5 else None

    cap = df["market_cap"].iloc[-1] if "market_cap" in df else None
    out["market_cap"] = float(cap) if cap is not None and pd.notna(cap) else None

    hi = df["high"].fillna(df["close"]).tail(TRADING_DAYS) if "high" in df else close.tail(TRADING_DAYS)
    lo = df["low"].fillna(df["close"]).tail(TRADING_DAYS) if "low" in df else close.tail(TRADING_DAYS)
    out["high_52w"] = float(hi.max())
    out["low_52w"] = float(lo.min())
    return {k: (None if isinstance(v, float) and (np.isnan(v) or np.isinf(v)) else v) for k, v in out.items()}


def latest_trading_day(session) -> date | None:
    return session.scalar(select(PriceDaily.day).order_by(PriceDaily.day.desc()).limit(1))


def compute_all(as_of: date | None = None) -> tuple[date | None, int]:
    with session_scope() as s:
        as_of = as_of or latest_trading_day(s)
        if as_of is None:
            return None, 0
        start = as_of - timedelta(days=400)
        q = select(PriceDaily.code, PriceDaily.day, PriceDaily.close, PriceDaily.high, PriceDaily.low,
                   PriceDaily.volume, PriceDaily.value, PriceDaily.market_cap) \
            .where(PriceDaily.day >= start, PriceDaily.day <= as_of)
        df = pd.DataFrame(s.execute(q).all(),
                          columns=["code", "day", "close", "high", "low", "volume", "value", "market_cap"])
    if df.empty:
        return as_of, 0
    rows = []
    for code, g in df.groupby("code"):
        if g["day"].max() != as_of:  # 기준일에 거래가 없는 종목(거래정지 등)은 건너뜀
            continue
        m = compute_for_series(g)
        m.update(code=code, day=as_of)
        rows.append(m)
    with session_scope() as s:
        n = upsert(s, MetricDaily, rows, ["code", "day"])
    return as_of, n
