"""해외 지수 수집 (yfinance, 비공식 — 정식 출시 전 교체). 기준 시점은 각 시장의 현지 거래일."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from collectors.common import job_run, require_rows
from db.models import IndexDaily
from db.session import session_scope, upsert

GLOBAL_INDICES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "^GDAXI": "DAX",
    "^STOXX": "STOXX Europe 600",
}


def collect_global_indices(period: str = "1mo") -> int:
    import yfinance as yf

    with job_run("indices_global") as st:
        df = yf.download(list(GLOBAL_INDICES), period=period, interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker")
        require_rows(df, "해외 지수")
        rows = []
        for sym, name in GLOBAL_INDICES.items():
            if sym not in df.columns.get_level_values(0):
                continue
            close = df[sym]["Close"].dropna()
            prev = None
            for d, c in close.items():
                c = float(c)
                rows.append(dict(symbol=sym, day=pd.Timestamp(d).date(), name=name, close=c,
                                 change_pct=(c / prev - 1) * 100 if prev else None,
                                 source="yfinance", collected_at=datetime.now()))
                prev = c
        with session_scope() as s:
            st["rows"] = upsert(s, IndexDaily, rows, ["symbol", "day"])
        return st["rows"]
