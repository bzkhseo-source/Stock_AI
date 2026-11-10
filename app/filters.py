"""템플릿 표시 형식. 값이 없으면 '데이터 없음'을 뜻하는 '—'를 표시한다 (추정하지 않음)."""
from __future__ import annotations

NA = "—"


def krw_big(v) -> str:
    """시가총액·거래대금: 조/억 단위."""
    if v is None:
        return NA
    if abs(v) >= 1e12:
        return f"{v / 1e12:,.2f}조원"
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    return f"{v / 1e4:,.0f}만원"


def price(v, currency: str = "KRW") -> str:
    if v is None:
        return NA
    return f"{v:,.0f}원" if currency == "KRW" else f"{v:,.2f} {currency}"


def num(v, digits: int = 0) -> str:
    return NA if v is None else f"{v:,.{digits}f}"


def pct(v, digits: int = 1) -> str:
    return NA if v is None else f"{v:+.{digits}f}%"


def pct_plain(v, digits: int = 1) -> str:
    return NA if v is None else f"{v:.{digits}f}%"


def ratio(v) -> str:
    return NA if v is None else f"{v:.1f}배"


def updown(v) -> str:
    """한국 관례: 상승 빨강, 하락 파랑."""
    if v is None or v == 0:
        return "text-slate-500"
    return "text-rose-600" if v > 0 else "text-blue-600"


def shares(v) -> str:
    if v is None:
        return NA
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억주"
    if v >= 1e4:
        return f"{v / 1e4:,.0f}만주"
    return f"{v:,.0f}주"


TREND_STYLE = {
    "급부상": "bg-rose-50 text-rose-700 ring-rose-200",
    "상승 지속": "bg-orange-50 text-orange-700 ring-orange-200",
    "초기 형성": "bg-violet-50 text-violet-700 ring-violet-200",
    "과열 주의": "bg-amber-50 text-amber-800 ring-amber-300",
    "하락 전환": "bg-blue-50 text-blue-700 ring-blue-200",
    "중립": "bg-slate-50 text-slate-600 ring-slate-200",
}

RELATION_STYLE = {
    "직접 관련주": "bg-emerald-50 text-emerald-700 ring-emerald-200",
    "핵심 수혜주": "bg-teal-50 text-teal-700 ring-teal-200",
    "공급망 관련주": "bg-sky-50 text-sky-700 ring-sky-200",
    "간접 관련주": "bg-slate-50 text-slate-600 ring-slate-200",
    "테마 편입주": "bg-amber-50 text-amber-800 ring-amber-200",
}


def register(env) -> None:
    env.filters.update(krw_big=krw_big, price=price, num=num, pct=pct, pct_plain=pct_plain, ratio=ratio,
                       updown=updown, shares=shares)
    env.globals.update(TREND_STYLE=TREND_STYLE, RELATION_STYLE=RELATION_STYLE, NA=NA)
