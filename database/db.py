import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "database/applications.db")


def generate_job_hash(company: str, title: str, url: str) -> str:
    """Generate deterministic SHA256 hash from company, title, and URL."""
    clean_company = (company or "").strip().lower()
    clean_title = (title or "").strip().lower()
    clean_url = (url or "").strip().lower()

    raw_str = f"{clean_company}|{clean_title}|{clean_url}"
    return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize SQLite database with applications schema, indexes, and migrations."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_hash TEXT UNIQUE NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT NOT NULL,
                fit_score INTEGER,
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                profile_used TEXT,
                screening_data TEXT,
                error_message TEXT,
                created_at TIMESTAMP NOT NULL,
                applied_at TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_apps_job_hash ON applications (job_hash)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_apps_status ON applications (status)"
        )
        await db.commit()

        # Automatic schema migration: ensure profile_used exists in pre-existing tables
        async with db.execute("PRAGMA table_info(applications)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]

        if "profile_used" not in columns:
            logger.info("Migrating applications table: adding 'profile_used' column.")
            await db.execute("ALTER TABLE applications ADD COLUMN profile_used TEXT")
            await db.commit()

    logger.info("Database initialized at %s", db_path)


async def job_exists(job_hash: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Check if a job already exists in SQLite by its unique hash."""
    path = Path(db_path)
    if not path.exists():
        await init_db(db_path)

    async with aiosqlite.connect(str(path)) as db:
        async with db.execute(
            "SELECT 1 FROM applications WHERE job_hash = ? LIMIT 1",
            (job_hash,)
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None


async def save_application(
    company: str,
    title: str,
    url: str,
    source: str = "aggregator",
    fit_score: Optional[int] = None,
    status: str = "DISCOVERED",
    profile_used: Optional[str] = None,
    screening_data: Optional[Dict[str, Any] | str] = None,
    error_message: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> Optional[str]:
    """Insert newly discovered or evaluated application. Returns job_hash on success, None if duplicate."""
    job_hash = generate_job_hash(company, title, url)
    now = datetime.now(timezone.utc).isoformat()

    screening_str: Optional[str] = None
    if isinstance(screening_data, dict):
        screening_str = json.dumps(screening_data)
    elif isinstance(screening_data, str):
        screening_str = screening_data

    path = Path(db_path)
    if not path.exists():
        await init_db(db_path)

    try:
        async with aiosqlite.connect(str(path)) as db:
            await db.execute(
                """
                INSERT INTO applications (
                    job_hash, company, title, url, source, fit_score, status, profile_used, screening_data, error_message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_hash,
                    company,
                    title,
                    url,
                    source,
                    fit_score,
                    status,
                    profile_used,
                    screening_str,
                    error_message,
                    now,
                )
            )
            await db.commit()
            return job_hash
    except aiosqlite.IntegrityError:
        logger.debug("Job %s already exists in database.", job_hash)
        return None


async def update_application_status(
    job_hash: str,
    status: str,
    error_message: Optional[str] = None,
    fit_score: Optional[int] = None,
    profile_used: Optional[str] = None,
    screening_data: Optional[Dict[str, Any] | str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> None:
    """Update application lifecycle status (e.g. APPLIED, MANUAL_REQUIRED, SKIPPED, FAILED)."""
    path = Path(db_path)
    if not path.exists():
        await init_db(db_path)

    applied_at = None
    if status.upper() == "APPLIED":
        applied_at = datetime.now(timezone.utc).isoformat()

    screening_str: Optional[str] = None
    if isinstance(screening_data, dict):
        screening_str = json.dumps(screening_data)
    elif isinstance(screening_data, str):
        screening_str = screening_data

    updates = ["status = ?"]
    params: List[Any] = [status]

    if error_message is not None:
        updates.append("error_message = ?")
        params.append(error_message)

    if fit_score is not None:
        updates.append("fit_score = ?")
        params.append(fit_score)

    if profile_used is not None:
        updates.append("profile_used = ?")
        params.append(profile_used)

    if screening_str is not None:
        updates.append("screening_data = ?")
        params.append(screening_str)

    if applied_at is not None:
        updates.append("applied_at = ?")
        params.append(applied_at)

    params.append(job_hash)
    sql = f"UPDATE applications SET {', '.join(updates)} WHERE job_hash = ?"

    async with aiosqlite.connect(str(path)) as db:
        await db.execute(sql, tuple(params))
        await db.commit()


async def get_recent_applications(limit: int = 50, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Retrieve recent applications from database."""
    path = Path(db_path)
    if not path.exists():
        await init_db(db_path)

    async with aiosqlite.connect(str(path)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, job_hash, company, title, url, source, fit_score, status, profile_used, screening_data, error_message, created_at, applied_at
            FROM applications
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
