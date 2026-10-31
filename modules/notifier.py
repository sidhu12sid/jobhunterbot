import logging
import os
from typing import Any, Dict, Optional
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def build_discord_payload(job: dict, score: int, reason: str, email_subject: Optional[str] = None) -> dict:
    """Standardize Discord embed construction into a strictly validated, clamped payload."""
    status = str(job.get("status", "MATCH_FOUND")).strip()
    source = str(job.get("source", "Job")).strip()
    title = str(job.get("title", "Unknown Title")).strip()
    company = str(job.get("company", "Unknown Company")).strip()
    location = str(job.get("location", "N/A")).strip()
    track = str(job.get("track") or job.get("profile_used") or "General").strip()
    hr_email = job.get("hr_email")
    email_draft = job.get("email_draft")

    fields = [
        {"name": "Status / Outcome", "value": (status or "MATCH_FOUND")[:250], "inline": True},
        {"name": "Fit Score", "value": f"{score}/100", "inline": True},
        {"name": "Platform / Source", "value": (source or "Unknown")[:100], "inline": True},
    ]

    if hr_email:
        fields.append({"name": "Direct HR Email", "value": str(hr_email)[:250], "inline": False})

    if email_subject:
        fields.append({"name": "Generated Subject", "value": str(email_subject)[:250], "inline": False})

    if email_draft and isinstance(email_draft, dict):
        if email_draft.get("resume_name"):
            fields.append({"name": "Resume to Attach", "value": str(email_draft["resume_name"])[:250], "inline": True})
        if email_draft.get("mailto_url"):
            fields.append({"name": "1-Click Direct Application", "value": f"[🚀 **Tap Here to Open Pre-Filled Email**]({email_draft['mailto_url']})"[:1024], "inline": False})

    if reason:
        fields.append({"name": "Evaluation Rationale", "value": str(reason)[:1000], "inline": False})

    job_note = job.get("message") or job.get("note")
    if job_note:
        fields.append({"name": "Submission / Action Notes", "value": str(job_note)[:1000], "inline": False})

    # Validate each field: Discord requires name (1-256) and value (1-1024)
    valid_fields = []
    for f in fields[:25]:
        name_str = (str(f.get("name") or "Field")).strip()[:256] or "Field"
        val_str = (str(f.get("value") or "N/A")).strip()[:1024] or "N/A"
        valid_fields.append({
            "name": name_str,
            "value": val_str,
            "inline": bool(f.get("inline", False))
        })

    title_text = f"[{source}] {title} @ {company}"[:256]
    desc_text = f"**Location:** {location}\n**Track:** {track}"[:4096]
    color = 3066993 if score >= 80 else 15844367  # Green for high match, Gold/Orange for review

    embed_obj: Dict[str, Any] = {
        "title": title_text,
        "color": color,
        "description": desc_text,
        "fields": valid_fields,
        "footer": {"text": "Role-Based Job Discovery & Application Engine"}
    }

    raw_url = str(job.get("url") or "").strip()
    if raw_url.startswith(("http://", "https://")):
        embed_obj["url"] = raw_url

    return {
        "username": "Job Hunter Bot",
        "embeds": [embed_obj]
    }


async def send_discord_notification(
    webhook_url: str,
    job: dict,
    score: int,
    reason: str,
    email_subject: Optional[str] = None
) -> bool:
    """Send standard, clamped Discord notification with complete error body logging."""
    if not webhook_url or webhook_url.startswith("your_discord_webhook"):
        logger.debug("Discord webhook not configured or placeholder.")
        return False

    payload = build_discord_payload(job, score, reason, email_subject)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            if res.status_code in (200, 204):
                logger.info("Discord alert delivered for '%s' @ '%s'", job.get("title"), job.get("company"))
                return True
            logger.warning(
                "Discord webhook failed with HTTP %s: %s (Payload: %s)",
                res.status_code, res.text, payload
            )
    except Exception as exc:
        logger.error("Failed to send Discord alert: %s", exc)

    return False


class NotificationDispatcher:
    """Dispatches webhook alerts for job application events (APPLIED, MANUAL_REQUIRED, EMAIL_DRAFT_READY)."""

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
        profile_used: str = "",
        source: str = "",
        email_draft: Optional[Dict[str, str]] = None
    ) -> None:
        """Dispatch notification to Discord and/or Telegram."""
        await self.send_discord_alert(title, company, url, fit_score, status, reason, message, profile_used, source, email_draft)
        await self.send_telegram_alert(title, company, url, fit_score, status, reason, message, profile_used, source, email_draft)

    async def send_discord_alert(
        self,
        title: str,
        company: str,
        url: str,
        fit_score: int,
        status: str,
        reason: str = "",
        message: str = "",
        profile_used: str = "",
        source: str = "",
        email_draft: Optional[Dict[str, str]] = None
    ) -> bool:
        """Send rich Discord embed with length-clamped, strictly validated fields."""
        if not self.discord_webhook or self.discord_webhook.startswith("your_discord_webhook"):
            logger.debug("Discord webhook not configured. Skipping alert.")
            return False

        job_dict = {
            "title": title,
            "company": company,
            "url": url,
            "status": status,
            "source": source or "Job",
            "track": profile_used or "General",
            "message": message,
            "hr_email": email_draft.get("to") if email_draft else None,
            "email_draft": email_draft
        }
        email_sub = email_draft.get("subject") if email_draft else None
        return await send_discord_notification(self.discord_webhook, job_dict, fit_score, reason, email_sub)

    async def send_telegram_alert(
        self,
        title: str,
        company: str,
        url: str,
        fit_score: int,
        status: str,
        reason: str = "",
        message: str = "",
        profile_used: str = "",
        source: str = "",
        email_draft: Optional[Dict[str, str]] = None
    ) -> bool:
        """Send Markdown formatted Telegram alert."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False

        if status.upper() == "APPLIED":
            icon = "✅"
        elif status.upper() == "EMAIL_DRAFT_READY":
            icon = "📩"
        else:
            icon = "⚠️"

        track_line = f"🏷️ *Track:* `{profile_used}`\n" if profile_used else ""
        source_line = f"🌐 *Platform:* `{source}`\n" if source else ""

        draft_lines = ""
        if email_draft:
            draft_lines = (
                f"✉️ *HR Email:* `{email_draft.get('to', '')}`\n"
                f"📎 *Resume:* `{email_draft.get('resume_name', '')}`\n"
                f"📝 *Subject:* `{email_draft.get('subject', '')}`\n"
                f"🚀 [Tap Here to Open Pre-Filled Email]({email_draft.get('mailto_url', '')})\n"
            )

        text = (
            f"{icon} *[{status.upper()}]* *{title}* at *{company}*\n"
            f"{track_line}"
            f"{source_line}"
            f"📊 *Score:* {fit_score}/100\n"
            f"{draft_lines}"
            f"📝 *Reason:* {reason}\n"
            f"ℹ️ *Notes:* {message}\n"
            f"🔗 [Open Original Job Posting]({url})"
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
