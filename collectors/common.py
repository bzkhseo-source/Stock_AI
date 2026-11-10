from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime

from db.models import JobRun
from db.session import session_scope

log = logging.getLogger("collect")


class EmptyResultError(RuntimeError):
    """수집 결과가 비어 있음. pykrx는 로그인 없이 호출하면 오류 없이 빈 결과를 돌려주므로 반드시 검사한다."""


def require_rows(df, what: str, hint: str = "") -> None:
    if df is None or len(df) == 0:
        raise EmptyResultError(f"{what}: 결과가 비어 있습니다. {hint}".strip())


@contextmanager
def job_run(name: str):
    """작업 실행 기록 (성공 여부, 적재 행 수, 메시지)."""
    with session_scope() as s:
        run = JobRun(job=name)
        s.add(run)
        s.flush()
        run_id = run.id
    state = {"rows": 0, "message": ""}
    ok = False
    try:
        yield state
        ok = True
    except Exception as e:  # 기록 후 다시 올림
        state["message"] = f"{type(e).__name__}: {e}"
        log.exception("%s 실패", name)
        raise
    finally:
        with session_scope() as s:
            r = s.get(JobRun, run_id)
            r.finished_at = datetime.now()
            r.ok = ok
            r.rows = state["rows"]
            r.message = state["message"][:2000] or None
