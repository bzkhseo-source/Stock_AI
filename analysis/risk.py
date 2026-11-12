"""리스크 배지: 공시 제목 키워드 + 가격·유동성 조건. 모든 등급에 제공한다."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from config import settings
from db.models import Disclosure, MetricDaily, MoveExplanation, RiskFlag, Stock
from db.session import session_scope, upsert

DISCLOSURE_RULES = [
    (("전환사채권발행결정", "신주인수권부사채권발행결정", "교환사채권발행결정"), "CB·BW 오버행"),
    (("유상증자결정",), "유상증자"),
    (("관리종목",), "관리종목"),
    (("매매거래정지", "거래정지"), "거래정지"),
    (("불성실공시",), "불성실공시"),
    (("상장적격성",), "상장적격성 심사"),
    (("의견거절", "감사의견비적정", "한정의견"), "감사의견 이슈"),
    (("보호예수", "의무보유"), "보호예수 관련"),
]

# 화면 설명용
RISK_HELP = {
    "CB·BW 오버행": "전환사채·신주인수권부사채 발행 공시. 주식 전환 시 물량 부담이 생길 수 있음",
    "유상증자": "신주 발행으로 지분 희석 가능성",
    "관리종목": "거래소 관리종목 지정",
    "거래정지": "매매거래 정지 관련 공시",
    "불성실공시": "불성실공시법인 지정 관련",
    "상장적격성 심사": "상장 유지 여부 심사 관련",
    "감사의견 이슈": "감사의견 관련 공시",
    "보호예수 관련": "의무보유(보호예수) 관련 공시. 해제 시 매도 물량 가능성",
    "단기 급등": f"5일 수익률 {settings.surge_5d_pct:.0f}% 이상. 추격매수 위험",
    "저유동성": f"20일 평균 거래대금 {settings.low_liquidity_krw / 1e8:.0f}억 원 미만. 원하는 가격에 사고팔기 어려울 수 있음",
    "원인 불명 급등락": "관련 공시·뉴스를 찾지 못한 큰 변동",
}


def detect(as_of: date, lookback_days: int = 30) -> int:
    rows = []
    with session_scope() as s:
        since = as_of - timedelta(days=lookback_days)
        for d in s.scalars(select(Disclosure).where(Disclosure.filed_day >= since, Disclosure.filed_day <= as_of)):
            for words, kind in DISCLOSURE_RULES:
                if any(w in d.title.replace(" ", "") for w in words):
                    rows.append(dict(code=d.code, kind=kind, since=d.filed_day, evidence=f"{d.title} ({d.url})"[:300]))
                    break
        foreign = set(s.scalars(select(Stock.code).where(Stock.country != "KR")))  # 한 번에 조회
        for m in s.scalars(select(MetricDaily).where(MetricDaily.day == as_of)):
            if m.ret_5d is not None and m.ret_5d >= settings.surge_5d_pct:
                rows.append(dict(code=m.code, kind="단기 급등", since=as_of, evidence=f"5일 수익률 {m.ret_5d:+.1f}%"))
            if m.code not in foreign and m.avg_value_20d is not None \
                    and m.avg_value_20d < settings.low_liquidity_krw:
                rows.append(dict(code=m.code, kind="저유동성", since=as_of,
                                 evidence=f"20일 평균 거래대금 {m.avg_value_20d / 1e8:.1f}억 원"))
        for e in s.scalars(select(MoveExplanation).where(MoveExplanation.day == as_of,
                                                         MoveExplanation.unexplained.is_(True))):
            if abs(e.change_pct) >= settings.move_threshold_pct:
                rows.append(dict(code=e.code, kind="원인 불명 급등락", since=as_of,
                                 evidence=f"등락률 {e.change_pct:+.1f}%, 관련 공시·뉴스 없음"))
        return upsert(s, RiskFlag, rows, ["code", "kind", "since"])


def active_flags(s, code: str, as_of: date, days: int = 30) -> list[RiskFlag]:
    """최근 N일 내 플래그 (유형별 최신 1건). 가격 기반 플래그는 기준일 것만."""
    price_kinds = {"단기 급등", "저유동성", "원인 불명 급등락"}
    out: dict[str, RiskFlag] = {}
    for f in s.scalars(select(RiskFlag).where(RiskFlag.code == code, RiskFlag.since >= as_of - timedelta(days=days))
                       .order_by(RiskFlag.since.desc())):
        if f.kind in price_kinds and f.since != as_of:
            continue
        out.setdefault(f.kind, f)
    return list(out.values())


def active_flags_many(s, codes: list[str], as_of: date, days: int = 30) -> dict[str, list[RiskFlag]]:
    """여러 종목의 플래그를 한 번의 질의로 가져온다 (화면 속도용). 규칙은 active_flags와 같다."""
    price_kinds = {"단기 급등", "저유동성", "원인 불명 급등락"}
    out: dict[str, dict[str, RiskFlag]] = {c: {} for c in codes}
    if not codes:
        return {}
    q = select(RiskFlag).where(RiskFlag.code.in_(codes), RiskFlag.since >= as_of - timedelta(days=days)) \
        .order_by(RiskFlag.since.desc())
    for f in s.scalars(q):
        if f.kind in price_kinds and f.since != as_of:
            continue
        out[f.code].setdefault(f.kind, f)
    return {c: list(v.values()) for c, v in out.items()}
