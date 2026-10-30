"""Database package initialization."""
from database.db import (
    DEFAULT_DB_PATH,
    generate_job_hash,
    get_recent_applications,
    init_db,
    job_exists,
    save_application,
    update_application_status,
)

__all__ = [
    "DEFAULT_DB_PATH",
    "init_db",
    "generate_job_hash",
    "job_exists",
    "save_application",
    "update_application_status",
    "get_recent_applications",
]
