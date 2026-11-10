"""테마 노출도 % : 사업보고서 '사업의 내용'에서 테마 관련 부문 매출 비중을 구한다.

1. LLM이 부문별 매출과 근거 문장(원문 그대로)을 추출한다.
2. 코드가 근거 문장이 원문에 실제로 있는지 대조하고, 비중을 직접 계산한다.
3. 검증에 실패하거나 부문 구분이 없으면 '산출 불가'로 남긴다 (추정 금지).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from collectors import dart
from db.models import Stock, Theme, ThemeMember
from db.session import session_scope
from llm.client import LLMUnavailable, generate_json

log = logging.getLogger("exposure")

SYSTEM = (
    "너는 한국 상장사 사업보고서를 읽는 분석 보조다. 주어진 원문 안의 정보만 사용한다. "
    "숫자를 추정하거나 계산하지 말고, 원문에 적힌 매출액 숫자와 단위를 그대로 옮긴다. "
    "evidence에는 원문 문장을 한 글자도 바꾸지 말고 그대로 복사한다. 원문에 없으면 null로 둔다."
)

PROMPT = """테마: {theme} (키워드: {keywords})

아래 사업보고서 '사업의 내용' 원문에서 사업부문(또는 주요 제품)별 매출액을 찾아 JSON으로 답하라.
각 부문이 테마와 어떤 관계인지 role을 고른다:
- "직접": 테마의 제품·서비스를 직접 개발·생산·판매
- "공급망": 테마 제품에 들어가는 부품·소재·장비 공급
- "간접": 테마 성장에 간접적으로 영향
- "무관": 관련 없음

형식:
{{"total_revenue": 숫자 또는 null, "unit": "원문 단위(예: 백만원)",
  "segments": [{{"name": "부문명", "revenue": 숫자 또는 null, "role": "직접|공급망|간접|무관",
                "evidence": "근거가 되는 원문 문장 그대로"}}]}}

원문:
\"\"\"{text}\"\"\"
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


@dataclass
class ExposureResult:
    pct: float | None
    relation: str
    evidence: str | None
    reason: str


def compute_exposure(result: dict, source_text: str) -> ExposureResult:
    """LLM 결과를 원문과 대조해 노출도를 계산한다 (순수 함수, 테스트 대상)."""
    src = _norm(source_text)
    total = result.get("total_revenue")
    segs = result.get("segments") or []
    verified = [s for s in segs if s.get("evidence") and _norm(s["evidence"]) in src]
    related = [s for s in verified if s.get("role") in ("직접", "공급망", "간접")]
    if not related:
        return ExposureResult(None, "테마 편입주", None, "사업보고서에서 테마 관련 부문의 근거를 확인하지 못함")
    roles = {s["role"] for s in related}
    relation = "직접 관련주" if "직접" in roles else "공급망 관련주" if "공급망" in roles else "간접 관련주"
    evidence = " / ".join(s["evidence"].strip() for s in related)[:1500]
    try:
        total = float(total) if total is not None else sum(float(s["revenue"]) for s in segs if s.get("revenue") is not None)
        rel_rev = sum(float(s["revenue"]) for s in related if s.get("revenue") is not None)
    except (TypeError, ValueError):
        return ExposureResult(None, relation, evidence, "매출액 숫자를 읽을 수 없어 비중 산출 불가")
    if not total or total <= 0 or rel_rev <= 0:
        return ExposureResult(None, relation, evidence, "부문별 매출 구분이 없어 비중 산출 불가")
    pct = round(rel_rev / total * 100, 1)
    if pct > 100:
        return ExposureResult(None, relation, evidence, "합계 불일치로 비중 산출 불가")
    if relation == "직접 관련주" and pct >= 30:
        relation = "직접 관련주"
    elif relation == "직접 관련주" and pct >= 10:
        relation = "핵심 수혜주"
    elif relation == "직접 관련주":
        relation = "간접 관련주"
    return ExposureResult(pct, relation, evidence, "사업보고서 부문별 매출 기준")


def verify_member(theme_id: str, code: str) -> ExposureResult | None:
    """한 종목의 테마 노출도를 검증해 저장한다. 수동 검토 결과는 덮어쓰지 않는다."""
    with session_scope() as s:
        stock, theme = s.get(Stock, code), s.get(Theme, theme_id)
        if not stock or not theme or not stock.dart_corp_code:
            return None
        member = s.get(ThemeMember, (theme_id, code))
        if member and member.basis == "수동 검토":
            return None
        corp, tname, kw = stock.dart_corp_code, theme.name, ", ".join([*theme.keywords, *theme.synonyms][:12])
    rep = dart.latest_business_report(corp)
    if not rep:
        return None
    rno, url = rep
    text = dart.business_section_text(rno)
    try:
        result = generate_json(PROMPT.format(theme=tname, keywords=kw, text=text), SYSTEM)
    except LLMUnavailable as e:
        log.warning("노출도 LLM 생략: %s", e)
        return None
    res = compute_exposure(result, text)
    with session_scope() as s:
        m = s.get(ThemeMember, (theme_id, code)) or ThemeMember(theme_id=theme_id, code=code)
        m.relation, m.exposure_pct, m.evidence = res.relation, res.pct, res.evidence or res.reason
        m.evidence_url, m.basis, m.verified_at = url, "사업보고서", date.today()
        s.merge(m)
    return res
