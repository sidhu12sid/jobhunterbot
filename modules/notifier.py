import logging
import os
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches webhook alerts for job application events (APPLIED and MANUAL_REQUIRED)."""

    def __init__(
        self,
        discord_webhook_url: Optional[str] = None,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None
    ):
        self.discord_webhook = discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")
        self.telegram_token = telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    async def notify_application_event(
        self,
        title: str,
        company: str,
        url: str,
        fit_score: int,
        status: str,
        reason: str = "",
        message: str = "",
        profile_used: str = ""
    ) -> None:
        """Dispatch notification to Discord and/or Telegram."""
        await self.send_discord_alert(title, company, url, fit_score, status, reason, message, profile_used)
        await self.send_telegram_alert(title, company, url, fit_score, status, reason, message, profile_used)

    async def send_discord_alert(
        self,
        title: str,
        company: str,
        url: str,
        fit_score: int,
        status: str,
        reason: str = "",
        message: str = "",
        profile_used: str = ""
    ) -> bool:
        """Send rich Discord embed for APPLIED or MANUAL_REQUIRED events."""
        if not self.discord_webhook or self.discord_webhook.startswith("your_discord_webhook"):
            logger.debug("Discord webhook not configured. Skipping alert.")
            return False

        is_applied = (status.upper() == "APPLIED")
        color = 0x2ECC71 if is_applied else 0xE67E22  # Emerald green vs Amber alert
        icon = "✅" if is_applied else "⚠️"

        fields = [
            {"name": "Fit Score", "value": f"**{fit_score}/100**", "inline": True},
            {"name": "Status", "value": f"`{status}`", "inline": True},
        ]
        if profile_used:
            fields.append({"name": "Track / Profile", "value": f"`{profile_used}`", "inline": True})

        fields.extend([
            {"name": "Evaluation Rationale", "value": (reason[:1000] if reason else "N/A"), "inline": False},
            {"name": "Submission / Action Notes", "value": (message[:1000] if message else "N/A"), "inline": False}
        ])

        payload = {
            "username": "Role Job Hunter Agent",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/3850/3850285.png",
            "embeds": [
                {
                    "title": f"{icon} [{status.upper()}] {title} @ {company}",
                    "url": url,
                    "color": color,
                    "fields": fields,
                    "footer": {
                        "text": "Role-Based Job Discovery & Application Engine • Google GenAI"
                    }
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.discord_webhook, json=payload)
                if res.status_code in (200, 204):
                    logger.info("Discord alert delivered for %s @ %s", title, company)
                    return True
                logger.warning("Discord webhook returned status %s: %s", res.status_code, res.text)
        except Exception as exc:
            logger.error("Failed to send Discord alert: %s", exc)

        return False

    async def send_telegram_alert(
        self,
        title: str,
        company: str,
        url: str,
        fit_score: int,
        status: str,
        reason: str = "",
        message: str = "",
        profile_used: str = ""
    ) -> bool:
        """Send Markdown formatted Telegram alert."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        icon = "✅" if status.upper() == "APPLIED" else "⚠️"
        track_line = f"🏷️ *Track:* `{profile_used}`\n" if profile_used else ""
        text = (
            f"{icon} *[{status.upper()}]* *{title}* at *{company}*\n"
            f"{track_line}"
            f"📊 *Score:* {fit_score}/100\n"
            f"📝 *Reason:* {reason}\n"
            f"ℹ️ *Notes:* {message}\n"
            f"🔗 [Open Job Application]({url})"
        )

        api_url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(api_url, json=payload)
                return res.status_code == 200
        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)
            return False
