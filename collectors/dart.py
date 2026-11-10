"""OpenDART 수집: 고유번호, 공시 목록, 주요 재무, 사업보고서 '사업의 내용' 원문."""
from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select

from collectors.common import job_run
from config import settings
from db.models import Disclosure, Financial, Stock
from db.session import session_scope, upsert

log = logging.getLogger("dart")
API = "https://opendart.fss.or.kr/api"


class DartError(RuntimeError):
    pass


def _key() -> str:
    if not settings.dart_api_key:
        raise DartError("DART_API_KEY가 설정되지 않았습니다 (.env).")
    return settings.dart_api_key


def _get(path: str, **params) -> httpx.Response:
    params["crtfc_key"] = _key()
    r = httpx.get(f"{API}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r


def _json(path: str, **params) -> dict:
    data = _get(path, **params).json()
    status = data.get("status")
    if status == "013":  # 조회된 데이터 없음
        return {"list": []}
    if status != "000":
        raise DartError(f"{path}: {status} {data.get('message')}")
    return data


def sync_corp_codes() -> int:
    """종목코드 ↔ DART 고유번호 매핑."""
    with job_run("dart_corp_codes") as st:
        z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml").content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        mapping = {}
        for el in root.iter("list"):
            sc = (el.findtext("stock_code") or "").strip()
            if sc:
                mapping[sc] = el.findtext("corp_code")
        with session_scope() as s:
            n = 0
            for stock in s.scalars(select(Stock).where(Stock.country == "KR")):
                cc = mapping.get(stock.code)
                if cc and stock.dart_corp_code != cc:
                    stock.dart_corp_code = cc
                    n += 1
        st["rows"] = n
        return n


def collect_disclosures(days: int = 3) -> int:
    """최근 N일 전체 공시 목록 (상장사만)."""
    end = date.today()
    start = end - timedelta(days=days)
    with job_run("dart_disclosures") as st:
        rows, page = [], 1
        while True:
            data = _json("list.json", bgn_de=start.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                         page_no=page, page_count=100)
            for it in data.get("list", []):
                code = (it.get("stock_code") or "").strip()
                if not code:
                    continue
                rows.append(dict(rcept_no=it["rcept_no"], code=code, corp_name=it["corp_name"],
                                 title=it["report_nm"].strip(),
                                 filed_day=datetime.strptime(it["rcept_dt"], "%Y%m%d").date(),
                                 url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
                                 source="DART", collected_at=datetime.now()))
            if page >= int(data.get("total_page", 1) or 1):
                break
            page += 1
            time.sleep(0.2)
        with session_scope() as s:
            st["rows"] = upsert(s, Disclosure, rows, ["rcept_no"])
        return st["rows"]


ACCOUNTS = {"매출액": "revenue", "영업이익": "operating_income", "당기순이익": "net_income",
            "부채총계": "total_liabilities", "자본총계": "total_equity"}


def _amount(v: str | None) -> float | None:
    if not v or v.strip() in ("-", ""):
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        return None


def collect_financials(codes: list[str], year: int | None = None) -> int:
    """주요 재무 (사업보고서 기준, 연결 우선). 테마 구성 종목처럼 필요한 종목만 호출한다."""
    year = year or date.today().year - 1
    with job_run("dart_financials") as st:
        with session_scope() as s:
            corp = {c: s.get(Stock, c).dart_corp_code for c in codes if s.get(Stock, c)}
        rows = []
        for code, cc in corp.items():
            if not cc:
                continue
            data = _json("fnlttSinglAcnt.json", corp_code=cc, bsns_year=str(year), reprt_code="11011")
            items = data.get("list", [])
            for div in ("CFS", "OFS"):
                sel = [i for i in items if i.get("fs_div") == div]
                if sel:
                    break
            rec = dict(code=code, period=str(year), source="DART", collected_at=datetime.now())
            for i in sel:
                key = ACCOUNTS.get(i.get("account_nm", "").strip())
                if key and rec.get(key) is None:
                    rec[key] = _amount(i.get("thstrm_amount"))
            if len(rec) > 4:
                rows.append(rec)
            time.sleep(0.2)
        with session_scope() as s:
            st["rows"] = upsert(s, Financial, rows, ["code", "period"])
        return st["rows"]


def latest_business_report(corp_code: str) -> tuple[str, str] | None:
    """최신 사업보고서 (접수번호, 원문 링크)."""
    end = date.today()
    data = _json("list.json", corp_code=corp_code, bgn_de=(end - timedelta(days=550)).strftime("%Y%m%d"),
                 end_de=end.strftime("%Y%m%d"), pblntf_detail_ty="A001", page_count=10)
    reports = [i for i in data.get("list", []) if "사업보고서" in i.get("report_nm", "")]
    if not reports:
        return None
    rno = sorted(reports, key=lambda i: i["rcept_dt"])[-1]["rcept_no"]
    return rno, f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rno}"


def business_section_text(rcept_no: str, max_chars: int = 40000) -> str:
    """사업보고서 원문에서 'II. 사업의 내용' 부분 텍스트."""
    z = zipfile.ZipFile(io.BytesIO(_get("document.xml", rcept_no=rcept_no).content))
    raw = ""
    for name in z.namelist():
        try:
            raw += z.read(name).decode("utf-8")
        except UnicodeDecodeError:
            raw += z.read(name).decode("euc-kr", errors="ignore")
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    m = re.search(r"II\.\s*사업의\s*내용", text)
    start = m.start() if m else 0
    end_m = re.search(r"III\.\s*재무에\s*관한\s*사항", text[start + 10:])
    end = start + 10 + end_m.start() if end_m else start + max_chars
    return text[start:end][:max_chars]
