"""환경 설정. .env 파일을 가장 먼저 읽는다 (pykrx는 import 시점에 KRX_ID/KRX_PW를 사용하므로)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = "왜올랐지"
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'app.db'}")
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-me")
    invite_codes: list[str] = field(default_factory=lambda: _list("INVITE_CODES"))
    # 외부 API
    dart_api_key: str = os.getenv("DART_API_KEY", "")
    krx_id: str = os.getenv("KRX_ID", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    llm_daily_limit: int = int(os.getenv("LLM_DAILY_LIMIT", "200"))
    # 분석 기준값 (베타에서 조정)
    move_threshold_pct: float = float(os.getenv("MOVE_THRESHOLD_PCT", "10"))
    volume_spike_ratio: float = float(os.getenv("VOLUME_SPIKE_RATIO", "3"))
    low_liquidity_krw: float = float(os.getenv("LOW_LIQUIDITY_KRW", "500000000"))  # 20일 평균 거래대금 5억 원
    surge_5d_pct: float = float(os.getenv("SURGE_5D_PCT", "30"))
    news_queries_per_run: int = int(os.getenv("NEWS_QUERIES_PER_RUN", "30"))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()

DISCLAIMER = (
    "본 서비스는 공개 데이터를 바탕으로 한 투자 참고 정보이며 특정 종목의 매수·매도를 권유하지 않습니다. "
    "AI 분석은 오류가 있을 수 있고 투자 수익을 보장하지 않습니다. 투자 판단과 그 결과의 책임은 이용자에게 있습니다."
)

# 기능 ID → 정식 출시 후 필요한 최소 등급. 베타 사용자는 모든 기능 사용 가능.
FEATURES: dict[str, str] = {
    "F01_dashboard": "free",
    "F02_search": "free",
    "F03_ranking": "free",
    "F04_exposure_detail": "pro",
    "F05_why_moved": "free",
    "F06_risk_badges": "free",
    "F07_stock_detail": "free",
    "F08_volatility_detail": "pro",
    "F09_ai_score": "pro",
    "F21_feedback": "free",
}
TIER_RANK = {"free": 0, "pro": 1, "beta": 2}


def can_use(tier: str, feature_id: str) -> bool:
    need = FEATURES.get(feature_id, "free")
    return TIER_RANK.get(tier, 0) >= TIER_RANK[need]


def is_pro_feature(feature_id: str) -> bool:
    return FEATURES.get(feature_id) == "pro"
