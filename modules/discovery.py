import html
import logging
import os
import re
from typing import Any, Dict, List, Optional
import httpx
from dotenv import load_dotenv

from database.db import DEFAULT_DB_PATH, generate_job_hash, job_exists, save_application

load_dotenv()
logger = logging.getLogger(__name__)


def clean_text(raw_text: Optional[str]) -> str:
    """Clean HTML tags, decode entities, and normalize whitespace."""
    if not raw_text:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = html.unescape(text)
    return " ".join(text.split())


class RoleJobDiscovery:
    """Discovers job postings by role and location using an aggregator API (JSearch / RapidAPI)

    with country-specific targeting ('in' for India) and SQLite deduplication.
    """

    JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"
    JSEARCH_V1_URL = "https://jsearch.p.rapidapi.com/search"

    def __init__(
        self,
        rapidapi_key: Optional[str] = None,
        db_path: str = DEFAULT_DB_PATH,
        timeout: float = 45.0
    ):
        self.rapidapi_key = rapidapi_key or os.getenv("RAPIDAPI_KEY", "")
        self.db_path = db_path
        self.timeout = timeout

    async def search_by_role(
        self,
        role: str,
        location: str = "Remote",
        date_posted: str = "today",
        country: str = "in",
        sources: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Query JSearch aggregator API for postings matching the role, location, country, and target sources.

        Deduplicates against SQLite applications table before returning.
        Returns list of normalized dicts:
        {job_hash, company, title, url, description, location, is_remote, source, platform}
        """
        logger.info(
            "Searching jobs for role='%s', location='%s', country='%s', date_posted='%s', sources=%s",
            role, location, country, date_posted, sources
        )
        if not self.rapidapi_key or self.rapidapi_key == "your_rapidapi_jsearch_key":
            error_msg = "RAPIDAPI_KEY is not configured or is a placeholder in .env. Cannot search jobs without RapidAPI."
            logger.error(error_msg)
            raise ValueError(error_msg)

        raw_jobs = await self._fetch_from_jsearch(role, location, date_posted, country, sources)

        # Deduplicate against SQLite
        fresh_jobs: List[Dict[str, Any]] = []
        for job in raw_jobs:
            company = job.get("company", "Unknown")
            title = job.get("title", "Untitled")
            url = job.get("url", "")
            description = job.get("description", "")
            source = job.get("source", "aggregator")
            job_loc = job.get("location", location)
            is_remote = bool(job.get("is_remote", False) or "remote" in location.lower() or "remote" in title.lower())

            if not url or not title:
                continue

            job_hash = generate_job_hash(company, title, url)
            already_exists = await job_exists(job_hash, db_path=self.db_path)

            if not already_exists:
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
                    "location": job_loc,
                    "is_remote": is_remote,
                    "source": source,
                    "platform": job.get("platform", source)
                })
            else:
                logger.debug("Skipping already indexed job: %s @ %s", title, company)

        logger.info("Found %d fresh unseen postings for '%s' in '%s'", len(fresh_jobs), role, location)
        return fresh_jobs

    # Alias for backwards compatibility
    async def search_jobs_for_role(
        self,
        role: str,
        location: str = "Remote",
        date_posted: str = "today",
        country: str = "in",
        sources: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        return await self.search_by_role(role, location, date_posted, country, sources)

    async def _fetch_from_jsearch(
        self,
        role: str,
        location: str,
        date_posted: str,
        country: str,
        sources: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch listings from RapidAPI JSearch API with country and source filtering."""
        headers = {
            "x-rapidapi-key": self.rapidapi_key,
            "x-rapidapi-host": "jsearch.p.rapidapi.com"
        }

        # Build query string with source targeting if specified
        source_suffix = f" ({' OR '.join(sources)})" if sources else ""
        if location.strip().lower() == "remote":
            query_str = f"{role} remote{source_suffix}"
        else:
            query_str = f"{role} in {location}{source_suffix}"

        params = {
            "query": query_str,
            "num_pages": "1",
            "date_posted": date_posted,
            "country": country
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Primary call to search-v2
                resp = await client.get(self.JSEARCH_URL, headers=headers, params=params)

                # Fallback to search v1 if v2 returns 404
                if resp.status_code == 404:
                    logger.debug("search-v2 returned 404, attempting search-v1...")
                    resp = await client.get(self.JSEARCH_V1_URL, headers=headers, params=params)

                if resp.status_code == 429:
                    error_msg = f"RapidAPI JSearch rate limit reached (HTTP 429) for role='{role}' in '{location}'. Check your RapidAPI quota."
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

                if resp.status_code in (401, 403):
                    error_msg = f"RapidAPI JSearch authorization failed (HTTP {resp.status_code}): {resp.text[:200]}. Verify your RAPIDAPI_KEY."
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

                if resp.status_code != 200:
                    error_msg = f"RapidAPI JSearch request failed with HTTP {resp.status_code}: {resp.text[:200]}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)

                payload = resp.json()
                raw_data = payload.get("data", {})
                if isinstance(raw_data, dict):
                    data = raw_data.get("jobs", [])
                elif isinstance(raw_data, list):
                    data = raw_data
                else:
                    data = []

                results: List[Dict[str, Any]] = []
                for item in data:
                    company = item.get("employer_name") or "Unknown"
                    title = item.get("job_title") or "Untitled"
                    url = item.get("job_apply_link") or item.get("job_google_link") or ""
                    description = clean_text(item.get("job_description", ""))
                    city = item.get("job_city")
                    country_name = item.get("job_country")
                    loc = f"{city}, {country_name}" if city and country_name else (city or location)
                    is_remote = bool(item.get("job_is_remote", False))
                    publisher = item.get("job_publisher") or item.get("job_source") or "Web"

                    # Source matching filter if specified
                    if sources:
                        pub_lower = publisher.lower()
                        url_lower = url.lower()
                        if not any(s.lower() in pub_lower or s.lower() in url_lower for s in sources):
                            continue

                    results.append({
                        "company": company,
                        "title": title,
                        "url": url,
                        "location": loc,
                        "is_remote": is_remote,
                        "description": description,
                        "source": publisher,
                        "platform": publisher
                    })
                return results
        except httpx.TimeoutException as exc:
            error_msg = f"RapidAPI JSearch timed out after {self.timeout}s for role='{role}' in '{location}'."
            logger.error(error_msg)
            raise TimeoutError(error_msg) from exc
        except Exception as exc:
            logger.error("RapidAPI JSearch error for role='%s' in '%s': %s", role, location, exc)
            raise
