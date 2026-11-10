"""데모 데이터: 가상의 회사와 가상의 시세로 화면과 분석 흐름을 확인하기 위한 데이터.

모든 회사명에 '(데모)'가 붙고 source='DEMO'로 저장된다. 실제 시장 정보가 아니다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np

from analysis.themes import load_themes
from db.models import (Base, Disclosure, Financial, IndexDaily, News, NewsStock, PriceDaily, Stock,
                       ThemeMember)
from db.session import engine, init_db, session_scope, upsert

# (코드, 이름, 시장, 테마, 연관성, 노출도, 기준가, 일평균 거래량)
COMPANIES = [
    ("900010", "가온반도체", "KOSPI", "semiconductor", "직접 관련주", 78.0, 84000, 900_000),
    ("900020", "누리테크", "KOSDAQ", "semiconductor", "공급망 관련주", 41.5, 23500, 1_200_000),
    ("900030", "다올소재", "KOSDAQ", "semiconductor", "공급망 관련주", 22.0, 15800, 600_000),
    ("900040", "라온패키징", "KOSDAQ", "semiconductor", "핵심 수혜주", 18.3, 9200, 2_400_000),
    ("900110", "마루로보틱스", "KOSDAQ", "robot", "직접 관련주", 64.0, 31200, 1_500_000),
    ("900120", "바른감속기", "KOSDAQ", "robot", "공급망 관련주", 37.2, 12700, 900_000),
    ("900130", "새봄자동화", "KOSPI", "robot", "핵심 수혜주", 14.8, 45600, 300_000),
    ("900140", "아름엔터", "KOSDAQ", "robot", "테마 편입주", None, 3100, 4_000_000),
    ("900210", "자연원자력", "KOSPI", "nuclear", "직접 관련주", 55.0, 61000, 700_000),
    ("900220", "차오름기공", "KOSDAQ", "nuclear", "공급망 관련주", 33.0, 18900, 800_000),
    ("900230", "큰빛중공업", "KOSPI", "nuclear", "간접 관련주", 6.5, 142000, 250_000),
    ("900310", "타래전선", "KOSPI", "power", "직접 관련주", 71.0, 27400, 1_100_000),
    ("900320", "파랑변압기", "KOSPI", "power", "직접 관련주", 88.0, 312000, 150_000),
    ("900330", "하늘전력기기", "KOSDAQ", "power", "핵심 수혜주", 26.0, 8700, 1_300_000),
    ("900410", "가람바이오", "KOSDAQ", "bio", "직접 관련주", 92.0, 54300, 500_000),
    ("900420", "나래제약", "KOSPI", "bio", "직접 관련주", 81.0, 128000, 120_000),
    ("900430", "다솜진단", "KOSDAQ", "bio", "간접 관련주", 8.0, 4150, 30_000),
    ("900510", "마음조선", "KOSPI", "shipbuilding", "직접 관련주", 90.0, 23800, 3_000_000),
    ("900520", "벼리엔진", "KOSPI", "shipbuilding", "공급망 관련주", 47.0, 39500, 600_000),
    ("900530", "산들기자재", "KOSDAQ", "shipbuilding", "공급망 관련주", 58.0, 11200, 700_000),
    ("900610", "온새미냉각", "KOSDAQ", "datacenter", "직접 관련주", 44.0, 16300, 1_800_000),
    ("900620", "윤슬광통신", "KOSDAQ", "datacenter", "공급망 관련주", 35.0, 7800, 2_100_000),
    ("900630", "은하서버", "KOSPI", "datacenter", "핵심 수혜주", 19.0, 52100, 400_000),
    ("900710", "지음방산", "KOSPI", "defense", "직접 관련주", 76.0, 98400, 350_000),
    ("900720", "참빛탄약", "KOSDAQ", "defense", "직접 관련주", 69.0, 21300, 650_000),
]

DEMO_URL = "https://example.com/demo"


def business_days(n: int, end: date) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def seed(reset: bool = True, n_days: int = 300) -> str:
    if reset:
        Base.metadata.drop_all(engine)
        init_db()
    load_themes()
    rng = np.random.default_rng(7)
    end = date.today() - timedelta(days=1)
    days = business_days(n_days, end)
    last = days[-1]

    # 테마 공통 요인 (로봇·데이터센터는 최근 강세, 바이오 약세)
    theme_drift = {"semiconductor": 0.0009, "robot": 0.0012, "nuclear": 0.0006, "power": 0.0010, "bio": -0.0004,
                   "shipbuilding": 0.0007, "datacenter": 0.0011, "defense": 0.0005}
    theme_factor = {t: rng.normal(mu, 0.012, n_days) for t, mu in theme_drift.items()}
    theme_factor["robot"][-5:] += [0.02, 0.025, 0.03, 0.02, 0.04]  # 최근 5일 급부상
    theme_factor["bio"][-5:] -= [0.01, 0.015, 0.01, 0.02, 0.015]  # 하락 전환

    stocks, prices, members, fins = [], [], [], []
    for code, name, market, theme, rel, expo, base, avg_vol in COMPANIES:
        dname = f"{name}(데모)"
        stocks.append(dict(code=code, name=dname, market=market, country="KR", currency="KRW", kind="보통주",
                           source="DEMO"))
        idio = rng.normal(0, 0.018, n_days)
        rets = theme_factor[theme] + idio
        vols = rng.lognormal(np.log(avg_vol), 0.35, n_days)
        if code == "900040":  # 공시가 있는 급등
            rets[-1], vols[-1] = 0.183, avg_vol * 6.2
        if code == "900140":  # 공시·뉴스 없는 급등 → 원인 불명
            rets[-1], vols[-1] = 0.256, avg_vol * 9.0
        if code == "900110":
            vols[-1] = avg_vol * 4.1
        if code == "900410":  # 공시가 있는 급락
            rets[-1], vols[-1] = -0.142, avg_vol * 5.0
        closes = base * np.cumprod(1 + rets) / np.prod(1 + rets) * (1 + rets[-1])
        shares = 50_000_000 if base < 50_000 else 20_000_000
        for i, d in enumerate(days):
            c = round(float(closes[i]), -1) or 10.0
            o = c / (1 + rets[i]) if i else c
            hi, lo = max(o, c) * (1 + abs(rng.normal(0, 0.006))), min(o, c) * (1 - abs(rng.normal(0, 0.006)))
            v = float(int(vols[i]))
            prices.append(dict(code=code, day=d, open=round(o, -1), high=round(hi, -1), low=round(lo, -1), close=c,
                               volume=v, value=v * c, market_cap=c * shares, source="DEMO"))
        evidence = ("데모 데이터: 가상의 사업보고서 문장입니다." if expo is not None
                    else "최근 30일 테마 관련 뉴스 제목 3건에 등장 (사업 연관성 미검증)")
        members.append(dict(theme_id=theme, code=code, relation=rel, exposure_pct=expo, evidence=evidence,
                            evidence_url=DEMO_URL if expo is not None else None,
                            basis="사업보고서" if expo is not None else "뉴스 동반 등장", verified_at=last))
        if code not in ("900140", "900430"):
            rev = base * shares * 0.6
            fins.append(dict(code=code, period=str(last.year - 1), revenue=rev,
                             operating_income=rev * (0.08 if code != "900410" else -0.05), net_income=rev * 0.05,
                             total_liabilities=rev * (0.6 if code != "900220" else 1.7), total_equity=rev,
                             source="DEMO"))

    # 공시
    discl = [
        ("900040", "단일판매ㆍ공급계약체결", last), ("900040", "주요사항보고서(신규시설투자)", last - timedelta(days=1)),
        ("900410", "임상시험계획 변경 및 중단 안내", last), ("900120", "전환사채권발행결정", last - timedelta(days=6)),
        ("900620", "유상증자결정", last - timedelta(days=12)), ("900320", "단일판매ㆍ공급계약체결", last - timedelta(days=3)),
        ("900720", "의무보유등에관한사항", last - timedelta(days=2)),
    ]
    name_of = {c[0]: f"{c[1]}(데모)" for c in COMPANIES}
    discl_rows = [dict(rcept_no=f"DEMO{i:06d}", code=c, corp_name=name_of[c], title=t, filed_day=d,
                       url=f"{DEMO_URL}/disclosure/{i}", source="DEMO") for i, (c, t, d) in enumerate(discl)]

    # 뉴스 (가상)
    at = lambda d, h: datetime.combine(d, time(h, 0))  # noqa: E731
    news = [
        ("로봇", "휴머노이드 부품 수요 확대 기대…마루로보틱스(데모) 거래량 급증", "데모경제", at(last, 10)),
        ("로봇", "바른감속기(데모), 협동로봇용 감속기 공급 확대 계획", "데모산업", at(last - timedelta(days=1), 14)),
        ("로봇", "로봇 관련주 강세…새봄자동화(데모) 스마트팩토리 수주", "데모일보", at(last - timedelta(days=2), 9)),
        ("반도체", "라온패키징(데모), 대규모 공급계약 체결 공시", "데모경제", at(last, 11)),
        ("반도체", "HBM 후공정 투자 확대…라온패키징(데모) 신규 시설 투자", "데모산업", at(last - timedelta(days=1), 16)),
        ("반도체", "가온반도체(데모) 차세대 메모리 양산 일정 공개", "데모일보", at(last - timedelta(days=4), 8)),
        ("바이오", "가람바이오(데모) 임상 계획 변경…주가 영향 주목", "데모헬스", at(last, 13)),
        ("전력", "파랑변압기(데모) 북미 변압기 수주", "데모경제", at(last - timedelta(days=3), 10)),
        ("데이터센터", "AI 데이터센터 냉각 수요 증가, 온새미냉각(데모) 수혜 기대", "데모IT", at(last - timedelta(days=2), 15)),
        ("원전", "원전 수출 협상 진전 소식에 자연원자력(데모) 관심", "데모일보", at(last - timedelta(days=5), 9)),
        ("조선", "LNG선 발주 증가…마음조선(데모) 수주 잔고 확대", "데모산업", at(last - timedelta(days=1), 11)),
        ("방산", "지음방산(데모) 수출 계약 협상 보도", "데모경제", at(last - timedelta(days=2), 17)),
    ]
    news_rows = [dict(url=f"{DEMO_URL}/news/{i}", title=t, publisher=p, published_at=dt, query=q,
                      issue_key=f"demo{i}", source="DEMO") for i, (q, t, p, dt) in enumerate(news)]

    # 지수 (가상)
    idx_rows = []
    for sym, name, base_v in [("KS11", "KOSPI", 3200), ("KQ11", "KOSDAQ", 880), ("^GSPC", "S&P 500", 6500),
                              ("^IXIC", "NASDAQ Composite", 21000), ("^DJI", "Dow Jones", 45000),
                              ("^N225", "Nikkei 225", 42000), ("^HSI", "Hang Seng", 25000), ("^GDAXI", "DAX", 23500)]:
        r = rng.normal(0.0004, 0.009, 40)
        lv = base_v * np.cumprod(1 + r)
        for i, d in enumerate(days[-40:]):
            idx_rows.append(dict(symbol=sym, day=d, name=name, close=float(lv[i]),
                                 change_pct=float(r[i] * 100) if i else None, source="DEMO"))

    with session_scope() as s:
        upsert(s, Stock, stocks, ["code"])
        upsert(s, PriceDaily, prices, ["code", "day"])
        upsert(s, ThemeMember, members, ["theme_id", "code"])
        upsert(s, Financial, fins, ["code", "period"])
        upsert(s, Disclosure, discl_rows, ["rcept_no"])
        upsert(s, IndexDaily, idx_rows, ["symbol", "day"])
        objs = [News(**r) for r in news_rows]
        s.add_all(objs)
        s.flush()
        for n in objs:
            for code, dname in name_of.items():
                if dname in n.title:
                    s.add(NewsStock(news_id=n.id, code=code))
    return f"가상 종목 {len(stocks)}개, 시세 {len(prices)}행, 기준일 {last}"
