"""
数据库连接和管理
"""
import os
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

from .config import CONFIG

# 确保数据目录存在
DATA_DIR = Path("./data")
DATA_DIR.mkdir(exist_ok=True)

# 创建引擎
database_url = os.getenv("DATABASE_URL", CONFIG.database.url)
engine = create_engine(
    database_url,
    echo=CONFIG.database.echo,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {}
)


def _migrate_sqlite_fetcher_states():
    """
    create_all 不会给已有 SQLite 表加新列。旧库缺 last_cursor 等字段时，
    ORM 读行会报 AttributeError: last_cursor。
    """
    url = database_url
    if not url.startswith("sqlite"):
        return
    insp = inspect(engine)
    if not insp.has_table("fetcher_states"):
        return
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(fetcher_states)")).fetchall()
    existing = {r[1] for r in rows}
    extra = [
        ("last_success_at", "DATETIME"),
        ("last_cursor", "TEXT"),
        ("last_count", "INTEGER NOT NULL DEFAULT 0"),
        ("total_fetches", "INTEGER NOT NULL DEFAULT 0"),
        ("error_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_error", "TEXT"),
        ("last_error_at", "DATETIME"),
    ]
    for col, ddl in extra:
        if col not in existing:
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE fetcher_states ADD COLUMN {col} {ddl}")
                )


def init_db():
    """初始化数据库表"""
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite_fetcher_states()


@contextmanager
def get_session():
    """获取数据库会话的上下文管理器"""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
