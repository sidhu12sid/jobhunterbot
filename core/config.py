from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # API Keys & LLM
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Database
    DATABASE_PATH: str = "database/jobs.db"

    # Notifications
    DISCORD_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    # Submitter Browser Settings
    HEADLESS: bool = True
    RESUME_PATH: str = "assets/sample_resume.txt"
    USER_DATA_DIR: str = ".browser_profile"

    # Target Boards
    GREENHOUSE_BOARDS: str = "figma,stripe"
    LEVER_COMPANIES: str = "netflix,palantir"

    @property
    def greenhouse_board_list(self) -> List[str]:
        return [b.strip() for b in self.GREENHOUSE_BOARDS.split(",") if b.strip()]

    @property
    def lever_company_list(self) -> List[str]:
        return [c.strip() for c in self.LEVER_COMPANIES.split(",") if c.strip()]


settings = Settings()
