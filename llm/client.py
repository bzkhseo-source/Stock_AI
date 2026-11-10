"""LLM 호출 계층. 공급사를 바꿀 수 있도록 이 파일 밖에서는 generate_json()만 사용한다.

원칙: LLM은 주어진 자료 안에서 문서를 해석만 한다. 수치 계산과 검증은 코드가 한다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

import httpx

from config import settings
from db.models import LLMUsage
from db.session import session_scope

log = logging.getLogger("llm")


class LLMUnavailable(RuntimeError):
    pass


def _take_quota() -> None:
    with session_scope() as s:
        row = s.get(LLMUsage, date.today())
        if row is None:
            row = LLMUsage(day=date.today(), calls=0)
            s.add(row)
        if row.calls >= settings.llm_daily_limit:
            raise LLMUnavailable(f"오늘 LLM 호출 상한({settings.llm_daily_limit}회)에 도달했습니다.")
        row.calls += 1


def available() -> bool:
    return bool(settings.gemini_api_key)


def _gemini(prompt: str, system: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    r = httpx.post(url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}, timeout=120)
    if r.status_code == 429:
        raise LLMUnavailable("Gemini 무료 티어 호출 한도를 초과했습니다 (429).")
    r.raise_for_status()
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMUnavailable(f"Gemini 응답 형식 오류: {str(data)[:300]}") from e


def generate_json(prompt: str, system: str) -> dict:
    if not available():
        raise LLMUnavailable("GEMINI_API_KEY가 설정되지 않았습니다.")
    _take_quota()
    text = _gemini(prompt, system)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"JSON 파싱 실패: {text[:200]}") from e
