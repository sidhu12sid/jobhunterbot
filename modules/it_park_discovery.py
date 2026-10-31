import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from database.db import DEFAULT_DB_PATH, generate_job_hash, job_exists, save_application

logger = logging.getLogger(__name__)


def clean_text(raw_text: Optional[str]) -> str:
    """Clean HTML tags, decode entities, and normalize whitespace."""
    if not raw_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = html.unescape(text)
    return " ".join(text.split())


class ITParkDiscovery:
    """Dedicated job discovery engine for major Kerala IT Park portals:

    - Infopark (Kochi / Cherthala / Thrissur): https://infopark.in/companies-job
    - Technopark (Trivandrum / Kollam): https://technopark.in/job-search
    - Cyberpark (Calicut / Kozhikode): https://cyberparks.in/careers/
    """

    INFOPARK_URL = "https://infopark.in/companies-job"
    TECHNOPARK_API_URL = "https://technopark.in/api/paginated-jobs"
    CYBERPARK_AJAX_URL = "https://cyberparks.in/jm-ajax/get_listings/"

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        timeout: float = 20.0
    ):
        self.db_path = db_path
        self.timeout = timeout

    async def fetch_infopark_jobs(self, search_query: str = "") -> List[Dict[str, Any]]:
        """Scrape current job postings from Infopark Kochi companies portal."""
        results: List[Dict[str, Any]] = []
        params = {"search": search_query} if search_query else {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False, follow_redirects=True) as client:
                resp = await client.get(self.INFOPARK_URL, headers=self.DEFAULT_HEADERS, params=params)
                if resp.status_code != 200:
                    logger.warning("Infopark returned HTTP %s", resp.status_code)
                    return []

                html_content = resp.text
                rows = re.findall(
                    r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*<a href=\"([^\"]+)\"",
                    html_content,
                    re.DOTALL
                )

                for row in rows:
                    post_date, raw_title, raw_company, closing_date, detail_url = row
                    title = clean_text(raw_title)
                    company = clean_text(raw_company)
                    close_info = clean_text(closing_date)
                    post_info = clean_text(post_date)

                    if not title or not detail_url:
                        continue

                    description = f"Infopark Kochi Opening: {title} at {company}. Posted: {post_info}, Closing: {close_info}."

                    results.append({
                        "company": company,
                        "title": title,
                        "url": detail_url,
                        "description": description,
                        "location": "Kochi, Kerala (Infopark)",
                        "is_remote": False,
                        "source": "Infopark",
                        "platform": "Infopark"
                    })

                logger.info("Discovered %d postings from Infopark for query '%s'", len(results), search_query)
        except Exception as exc:
            logger.error("Error scraping Infopark for '%s': %s", search_query, exc)

        return results

    async def fetch_technopark_jobs(self, search_query: str = "") -> List[Dict[str, Any]]:
        """Query Technopark Trivandrum JSON API."""
        results: List[Dict[str, Any]] = []
        params = {
            "page": "1",
            "search": search_query,
            "type": ""
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                resp = await client.get(self.TECHNOPARK_API_URL, headers=self.DEFAULT_HEADERS, params=params)
                if resp.status_code != 200:
                    logger.warning("Technopark API returned HTTP %s", resp.status_code)
                    return []

                payload = resp.json()
                data = payload.get("data", []) if isinstance(payload, dict) else []

                for item in data:
                    job_id = item.get("id")
                    title = item.get("job_title") or "Untitled"
                    comp_data = item.get("company", {})
                    company = comp_data.get("company") if isinstance(comp_data, dict) else "Technopark Company"
                    closing_date = item.get("closing_date") or ""
                    posted_date = item.get("posted_date") or ""

                    if not job_id:
                        continue

                    detail_url = f"https://technopark.in/job-details/{job_id}"
                    description = f"Technopark Trivandrum Opening: {title} at {company}. Posted: {posted_date}, Closing: {closing_date}."

                    results.append({
                        "company": company,
                        "title": title,
                        "url": detail_url,
                        "description": description,
                        "location": "Trivandrum, Kerala (Technopark)",
                        "is_remote": False,
                        "source": "Technopark",
                        "platform": "Technopark"
                    })

                logger.info("Discovered %d postings from Technopark for query '%s'", len(results), search_query)
        except Exception as exc:
            logger.error("Error fetching Technopark API for '%s': %s", search_query, exc)

        return results

    async def fetch_cyberpark_jobs(self, search_query: str = "") -> List[Dict[str, Any]]:
        """Scrape active career postings from Cyberpark Calicut."""
        results: List[Dict[str, Any]] = []
        data_payload = {
            "search_keywords": search_query,
            "page": 1,
            "per_page": 25
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                resp = await client.post(
                    self.CYBERPARK_AJAX_URL,
                    headers=self.DEFAULT_HEADERS,
                    data=data_payload
                )

                if resp.status_code != 200:
                    logger.warning("Cyberpark AJAX returned HTTP %s", resp.status_code)
                    return []

                html_str = resp.json().get("html", "")
                matches = re.findall(
                    r"<li[^>]*class=\"[^\"]*job_listing[^\"]*\"[^>]*>.*?<a href=\"([^\"]+)\".*?<h3[^>]*>(.*?)</h3>.*?<div class=\"company\">\s*<strong>(.*?)</strong>",
                    html_str,
                    re.DOTALL
                )

                for url, raw_title, raw_comp in matches:
                    title = clean_text(raw_title)
                    company = clean_text(raw_comp)

                    if not url or not title:
                        continue

                    description = f"Cyberpark Calicut Opening: {title} at {company}."

                    results.append({
                        "company": company,
                        "title": title,
                        "url": url,
                        "description": description,
                        "location": "Calicut, Kerala (Cyberpark)",
                        "is_remote": False,
                        "source": "Cyberpark",
                        "platform": "Cyberpark"
                    })

                logger.info("Discovered %d postings from Cyberpark for query '%s'", len(results), search_query)
        except Exception as exc:
            logger.error("Error scraping Cyberpark for '%s': %s", search_query, exc)

        return results

    async def fetch_job_details(self, job_url: str, source: str) -> str:
        """Fetch full job description text for a specific posting when evaluating."""
        try:
            async with httpx.AsyncClient(timeout=15.0, verify=False, follow_redirects=True) as client:
                resp = await client.get(job_url, headers=self.DEFAULT_HEADERS)
                if resp.status_code != 200:
                    return ""

                # Technopark detail inspection (Inertia data-page extraction)
                if source == "Technopark":
                    m = re.search(r"data-page=\"([^\"]+)\"", resp.text)
                    if m:
                        import json
                        data = json.loads(html.unescape(m.group(1)))
                        job = data.get("props", {}).get("jobListing", {})
                        if isinstance(job, dict):
                            desc = clean_text(job.get("job_description_content") or job.get("job_description") or "")
                            skills = clean_text(job.get("preferred_skills_content") or job.get("preferred_skills") or "")
                            return f"{desc}\nPreferred Skills: {skills}".strip()

                # Generic HTML extraction for Infopark and Cyberpark
                clean = clean_text(resp.text)
                return clean[:4000]
        except Exception as exc:
            logger.debug("Failed to fetch job details from %s: %s", job_url, exc)
            return ""

    async def search_all_it_parks(
        self,
        query: str,
        core_anchors: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search Infopark, Technopark, and Cyberpark in parallel.

        Deduplicates against SQLite applications table and returns fresh postings.
        """
        logger.info("Executing IT Park multi-portal search for query='%s'...", query)

        # Run portal scrapers in parallel
        results = await asyncio.gather(
            self.fetch_infopark_jobs(query),
            self.fetch_technopark_jobs(query),
            self.fetch_cyberpark_jobs(query),
            return_exceptions=True
        )

        all_raw_jobs: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, list):
                all_raw_jobs.extend(res)
            elif isinstance(res, Exception):
                logger.error("IT park portal task raised exception: %s", res)

        fresh_jobs: List[Dict[str, Any]] = []

        for job in all_raw_jobs:
            company = job.get("company", "Unknown")
            title = job.get("title", "Untitled")
            url = job.get("url", "")
            source = job.get("source", "IT_Park")
            location = job.get("location", "Kerala")

            if not url or not title:
                continue

            job_hash = generate_job_hash(company, title, url)
            already_exists = await job_exists(job_hash, db_path=self.db_path)

            if not already_exists:
                # Enrich with full job description
                full_desc = await self.fetch_job_details(url, source)
                description = full_desc if full_desc else job.get("description", "")

                await save_application(
                    company=company,
                    title=title,
                    url=url,
                    source=source,
                    status="DISCOVERED",
                    db_path=self.db_path
                )

                fresh_jobs.append({
                    "job_hash": job_hash,
                    "company": company,
                    "title": title,
                    "url": url,
                    "description": description,
                    "location": location,
                    "is_remote": False,
                    "source": source,
                    "platform": source
                })
            else:
                logger.debug("Skipping existing IT park job: %s @ %s", title, company)

        logger.info("Found %d fresh unseen IT park postings for query '%s'", len(fresh_jobs), query)
        return fresh_jobs
