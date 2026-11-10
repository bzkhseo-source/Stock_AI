"""분석 로직 단위 테스트 (외부 API 호출 없음)."""
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(__file__).parent / "_test.db"))

from analysis.exposure import compute_exposure  # noqa: E402
from analysis.metrics import compute_for_series  # noqa: E402
from analysis.scoring import combine, percentile  # noqa: E402
from analysis.themes import classify_trend  # noqa: E402
from collectors.news import decode_google_link, issue_key, name_in_title, split_title  # noqa: E402
from collectors.prices_kr import classify_kind  # noqa: E402


def _series(closes):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    return pd.DataFrame(dict(day=days, close=closes, high=closes, low=closes, volume=[100.0] * len(closes),
                             value=[1e9] * len(closes), market_cap=[1e12] * len(closes)))


def test_returns_and_mdd():
    closes = [100.0] * 30 + [120.0, 90.0, 110.0]
    m = compute_for_series(_series(closes))
    assert round(m["ret_1d"], 4) == round((110 / 90 - 1) * 100, 4)
    assert round(m["mdd_1y"], 4) == -25.0  # 120 → 90
    assert m["ret_1y"] is None  # 데이터 부족이면 추정하지 않음


def test_annual_vol_formula():
    closes = [100 * (1.01 if i % 2 else 0.99) ** 1 for i in range(80)]
    m = compute_for_series(_series(closes))
    rets = pd.Series(closes).pct_change().dropna().tail(60)
    assert math.isclose(m["vol_annual"], rets.std(ddof=1) * math.sqrt(252) * 100, rel_tol=1e-9)


def test_volume_ratio():
    df = _series([100.0] * 25)
    df.loc[24, "volume"] = 500.0
    assert compute_for_series(df)["volume_ratio"] == 5.0


def test_score_missing_items_not_full_marks():
    total, cov, grade = combine({"momentum": 20, "volume": 15, "industry": 20, "news": None,
                                 "relation": 15, "financial": None})
    assert cov == 70.0
    assert total == 100.0 and grade == "높음"  # 평가 가능한 항목만으로 환산
    total, cov, grade = combine({"momentum": 10, "volume": None, "industry": None, "news": None,
                                 "relation": 15, "financial": None})
    assert total is None and grade == "평가 불가"  # 충족률 35% < 60%


def test_percentile():
    assert percentile(5, [1, 5, 9]) == 0.5
    assert percentile(9, [1, 5, 9]) == 1.0
    assert percentile(None, [1, 2]) is None


def test_trend_rules():
    assert classify_trend(16, 20, 3.5, 5) == "과열 주의"
    assert classify_trend(6, 0, 2.1, 0) == "급부상"
    assert classify_trend(-7, 0, 1, 0) == "하락 전환"
    assert classify_trend(1, 12, 1, 0) == "상승 지속"
    assert classify_trend(0.5, 0, 1, 12) == "초기 형성"
    assert classify_trend(None, None, None, None) == "중립"


SOURCE = "당사는 HBM 패키징 장비를 생산합니다. 장비 부문 매출액은 300억원입니다. 기타 부문은 소모품 판매입니다."


def test_exposure_verified():
    res = compute_exposure({"total_revenue": 1000, "segments": [
        {"name": "장비", "revenue": 300, "role": "직접", "evidence": "당사는 HBM 패키징 장비를 생산합니다."},
        {"name": "기타", "revenue": 700, "role": "무관", "evidence": "기타 부문은 소모품 판매입니다."}]}, SOURCE)
    assert res.pct == 30.0 and res.relation == "직접 관련주"


def test_exposure_rejects_hallucinated_evidence():
    res = compute_exposure({"total_revenue": 1000, "segments": [
        {"name": "AI", "revenue": 900, "role": "직접", "evidence": "당사는 세계 1위 AI 기업입니다."}]}, SOURCE)
    assert res.pct is None and res.relation == "테마 편입주"


def test_exposure_no_segment_revenue():
    res = compute_exposure({"total_revenue": None, "segments": [
        {"name": "장비", "revenue": None, "role": "공급망", "evidence": "당사는 HBM 패키징 장비를 생산합니다."}]}, SOURCE)
    assert res.pct is None and res.relation == "공급망 관련주"


def test_news_helpers():
    assert split_title("삼성전자, HBM 공급 - 데모일보") == ("삼성전자, HBM 공급", "데모일보")
    assert issue_key("[속보] 삼성전자 HBM 공급") == issue_key("삼성전자 HBM 공급 (종합)")
    assert decode_google_link("https://example.com/a") == "https://example.com/a"


def test_classify_kind():
    assert classify_kind("005930", "삼성전자") == "보통주"
    assert classify_kind("005935", "삼성전자우") == "우선주"
    assert classify_kind("123450", "데모스팩1호") == "스팩"


def test_name_boundary():
    assert name_in_title("이닉스", "SK하이닉스 HBM 공급") is False
    assert name_in_title("SK하이닉스", "SK하이닉스 HBM 공급") is True
    assert name_in_title("한미반도체", "[속보]한미반도체, 장비 수주") is True
