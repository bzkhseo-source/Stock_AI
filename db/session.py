from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker

from config import settings
from db.models import Base

if settings.is_sqlite:
    Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
else:
    # Neon 등 PostgreSQL: postgresql+psycopg://... 형태 권장, sslmode=require
    url = settings.database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def upsert(session, model, rows: list[dict], keys: list[str]) -> int:
    """기본키 충돌 시 갱신. SQLite / PostgreSQL 모두 지원."""
    if not rows:
        return 0
    ins = sqlite_insert if settings.is_sqlite else pg_insert
    cols = {c.name for c in model.__table__.columns}
    dedup: dict = {}
    for r in rows:  # 같은 키가 두 번 들어오면 PostgreSQL이 거부하므로 마지막 것만 남긴다
        dedup[tuple(r.get(k) for k in keys)] = r
    rows = list(dedup.values())
    total = 0
    for i in range(0, len(rows), 500):
        chunk = [{k: v for k, v in r.items() if k in cols} for r in rows[i:i + 500]]
        stmt = ins(model).values(chunk)
        update_cols = {c: stmt.excluded[c] for c in chunk[0] if c not in keys}
        stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update_cols) if update_cols \
            else stmt.on_conflict_do_nothing(index_elements=keys)
        session.execute(stmt)
        total += len(chunk)
    return total
