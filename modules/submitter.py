import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from playwright.async_api import BrowserContext, Page, async_playwright

# Playwright stealth support
try:
    from playwright_stealth import Stealth
    _stealth = Stealth()
    async def apply_stealth(page_or_context):
        await _stealth.apply_stealth_async(page_or_context)
except Exception:
    try:
        from playwright_stealth import stealth_async as apply_stealth
    except Exception:
        async def apply_stealth(page_or_context):
            pass

logger = logging.getLogger(__name__)


class ApplicationSubmitter:
    """Automates form filling using Playwright with persistent context, dynamic resume attachment,

    stealth evasions, and CAPTCHA detection.
    """

    CAPTCHA_SELECTORS = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        ".g-recaptcha",
        ".h-captcha",
        "#cf-turnstile",
        "div[data-sitekey]",
        "iframe[src*='arkoselabs']"
    ]

    UNHANDLED_ASSESSMENT_SELECTORS = [
        "iframe[src*='hackerrank']",
        "iframe[src*='codility']",
        "iframe[src*='testgorilla']",
        "div[class*='assessment-container']",
        "div[id*='assessment']"
    ]

    def __init__(
        self,
        user_data_dir: str = ".browser_profile",
        headless: bool = True
    ):
        self.user_data_dir = Path(user_data_dir)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless

    async def detect_manual_intervention_triggers(self, page: Page) -> Tuple[bool, str]:
        """Detect CAPTCHAs, bot challenges, or multi-step technical assessments."""
        for selector in self.CAPTCHA_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    return True, f"CAPTCHA challenge detected ({selector})"
            except Exception:
                continue

        for selector in self.UNHANDLED_ASSESSMENT_SELECTORS:
            try:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    return True, f"Interactive assessment detected ({selector})"
            except Exception:
                continue

        return False, ""

    async def fill_generic_form(
        self,
        page: Page,
        personal_info: Dict[str, Any],
        resume_path: Optional[str] = None,
        answers: Optional[Dict[str, str]] = None
    ) -> None:
        """Fill standard application fields, attach dynamic resume, and fill screening answers."""
        first_name = personal_info.get("first_name", "")
        last_name = personal_info.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip()
        email = personal_info.get("email", "")
        phone = personal_info.get("phone", "")
        location = personal_info.get("location", "")
        linkedin = personal_info.get("linkedin", "")
        github = personal_info.get("github", "")
        website = personal_info.get("website", "")

        # 1. Names
        await self._type_if_found(page, "input[name*='first_name' i], input[id*='first_name' i]", first_name)
        await self._type_if_found(page, "input[name*='last_name' i], input[id*='last_name' i]", last_name)
        await self._type_if_found(page, "input[name='name' i], input[id='name' i], input[autocomplete='name' i]", full_name)

        # 2. Contact & Location
        await self._type_if_found(page, "input[type='email' i], input[name*='email' i]", email)
        await self._type_if_found(page, "input[type='tel' i], input[name*='phone' i]", phone)
        await self._type_if_found(page, "input[name*='location' i], input[name*='city' i]", location)

        # 3. Social / Portfolio
        await self._type_if_found(page, "input[name*='linkedin' i], input[id*='linkedin' i]", linkedin)
        await self._type_if_found(page, "input[name*='github' i], input[id*='github' i]", github)
        await self._type_if_found(page, "input[name*='website' i], input[name*='portfolio' i]", website)

        # 4. Attach Designated Resume for this Role Track
        if resume_path:
            resume_file = Path(resume_path).resolve()
            if resume_file.exists():
                file_input = page.locator("input[type='file']").first
                if await file_input.count() > 0:
                    try:
                        logger.info("Attaching track-specific resume: %s", resume_file.name)
                        await file_input.set_input_files(str(resume_file))
                        await asyncio.sleep(1.0)
                    except Exception as exc:
                        logger.debug("Resume file upload error: %s", exc)
            else:
                logger.warning("Designated resume path %s does not exist on disk.", resume_path)

        # 5. Populate Screening Q&A and Salary
        if answers:
            # Check for standard salary inputs
            salary_val = (
                answers.get("Expected CTC / Salary")
                or answers.get("salary_expectation")
                or answers.get("expected_salary")
            )
            if salary_val:
                await self._type_if_found(
                    page,
                    "input[name*='salary' i], input[name*='compensation' i], input[name*='ctc' i], input[id*='salary' i], input[id*='compensation' i]",
                    str(salary_val)
                )

            for q_key, answer_val in answers.items():
                sanitized_key = q_key[:20].lower()
                await self._type_if_found(
                    page,
                    f"input[name*='{sanitized_key}'], textarea[name*='{sanitized_key}']",
                    str(answer_val)
                )

    async def _type_if_found(self, page: Page, selector: str, text: str) -> None:
        if not text:
            return
        try:
            elem = page.locator(selector).first
            if await elem.count() > 0 and await elem.is_visible():
                await elem.scroll_into_view_if_needed()
                await elem.fill(str(text))
        except Exception:
            pass

    async def submit_application(
        self,
        job_url: str,
        personal_info: Dict[str, Any],
        resume_path: str,
        answers: Optional[Dict[str, str]] = None,
        dry_run: bool = True
    ) -> Dict[str, str]:
        """Navigate to application page, verify resume, fill inputs, and return structured status dict.

        Returns:
            {"status": "APPLIED" | "MANUAL_REQUIRED" | "FAILED", "message": "..."}
        """
        # Verify resume file existence in assets/ or project before launching browser
        target_resume = Path(resume_path).resolve()
        if not target_resume.exists():
            logger.warning("Resume file %s not found. Checking assets fallback.", resume_path)
            fallback = Path("assets/resume.pdf").resolve()
            if fallback.exists():
                target_resume = fallback
            else:
                return {
                    "status": "FAILED",
                    "message": f"Resume file not found at {resume_path} and no fallback available"
                }

        async with async_playwright() as p:
            context: BrowserContext = await p.chromium.launch_persistent_context(
                user_data_dir=str(self.user_data_dir),
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            page = await context.new_page()
            await apply_stealth(page)

            try:
                logger.info("Opening application URL: %s", job_url)
                await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2.0)

                # Pre-interaction check for CAPTCHA or blocking assessment
                blocked, reason = await self.detect_manual_intervention_triggers(page)
                if blocked:
                    logger.warning("Automated submission blocked: %s", reason)
                    return {"status": "MANUAL_REQUIRED", "message": reason}

                # Populate form with track-specific resume
                await self.fill_generic_form(
                    page=page,
                    personal_info=personal_info,
                    resume_path=str(target_resume),
                    answers=answers
                )

                # Post-interaction check for dynamic challenges
                blocked, reason = await self.detect_manual_intervention_triggers(page)
                if blocked:
                    logger.warning("Automated submission blocked post-fill: %s", reason)
                    return {"status": "MANUAL_REQUIRED", "message": reason}

                if dry_run:
                    logger.info("Dry-run active: Form populated successfully without clicking final submit.")
                    return {"status": "APPLIED", "message": "Form populated successfully (dry-run mode)"}

                # Attempt live submission click
                submit_btn = page.locator(
                    "button[type='submit'], input[type='submit'], button:has-text('Submit'), button:has-text('Apply')"
                ).first

                if await submit_btn.count() > 0 and await submit_btn.is_visible():
                    await submit_btn.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    return {"status": "APPLIED", "message": "Application submitted successfully"}
                else:
                    return {
                        "status": "MANUAL_REQUIRED",
                        "message": "Submit button not interactable or application requires multi-step manual review"
                    }

            except Exception as exc:
                logger.error("Exception during browser submission: %s", exc)
                return {"status": "MANUAL_REQUIRED", "message": f"Navigation or interaction exception: {str(exc)}"}
            finally:
                await context.close()

    # Backwards compatibility alias returning tuple
    async def process_application(
        self,
        job_url: str,
        profile: Dict[str, Any],
        custom_answers: Optional[Dict[str, str]] = None,
        dry_run: bool = True
    ) -> Tuple[str, str]:
        personal = profile.get("personal", {})
        resume_path = personal.get("resume_path", "assets/resume.pdf")
        res = await self.submit_application(
            job_url=job_url,
            personal_info=personal,
            resume_path=resume_path,
            answers=custom_answers,
            dry_run=dry_run
        )
        return res["status"], res["message"]
