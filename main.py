import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

from database.db import (
    DEFAULT_DB_PATH,
    get_recent_applications,
    init_db,
    update_application_status
)
from modules.discovery import RoleJobDiscovery
from modules.evaluator import JobFitEvaluator
from modules.notifier import NotificationDispatcher
from modules.submitter import ApplicationSubmitter

# Configure UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
# Silence verbose SDK internal logging (AFC, HTTP connection pooling)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("multi_role_job_engine")
console = Console(highlight=False)


def load_candidate_profile(profile_path: str = "config/profile.json") -> Dict[str, Any]:
    """Load candidate profile, role tracks, and criteria from JSON."""
    path = Path(profile_path)
    if not path.exists():
        console.print(f"[bold red]Profile file not found at {profile_path}![/bold red]")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def display_applications_summary(db_path: str = DEFAULT_DB_PATH) -> None:
    """Print the contents of the SQLite applications table including track/profile used."""
    apps = await get_recent_applications(limit=35, db_path=db_path)
    table = Table(title=f"Applications Registry ({db_path})")
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Company", style="magenta")
    table.add_column("Title", style="green")
    table.add_column("Track", style="yellow")
    table.add_column("Source", style="dim")
    table.add_column("Fit", justify="center")
    table.add_column("Status", style="bold")
    table.add_column("Action / Note", style="white")
    table.add_column("Created", style="dim")

    for app in apps:
        score_val = str(app["fit_score"]) if app["fit_score"] is not None else "-"
        track_val = app.get("profile_used") or "-"
        note = (app.get("error_message") or "")[:35]
        status = app["status"]
        status_style = "green" if status == "APPLIED" else ("red" if "MANUAL" in status else ("dim" if status == "SKIPPED" else "yellow"))

        table.add_row(
            str(app["id"]),
            app["company"][:18],
            app["title"][:25],
            track_val[:16],
            app["source"],
            score_val,
            f"[{status_style}]{status}[/{status_style}]",
            note,
            str(app["created_at"])[:16]
        )
    console.print(table)


async def run_engine(
    profile_path: str = "config/profile.json",
    dry_run: bool = True,
    track_filter: Optional[str] = None,
    roles_override: Optional[List[str]] = None,
    locations_override: Optional[List[str]] = None,
    sources_override: Optional[List[str]] = None,
    date_posted_override: Optional[str] = None,
    threshold_override: Optional[int] = None,
    max_apps_total: int = 10,
    rate_limit_sleep: float = 1.5
) -> None:
    """Orchestrate nested multi-role, multi-location job discovery and application."""
    console.rule("[bold cyan]Role-Based Job Discovery & Application Engine[/bold cyan]")

    # 1. Database Init & Migration
    db_path = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    await init_db(db_path)

    # 2. Load Profile Configurations
    profile = load_candidate_profile(profile_path)
    personal = profile.get("personal", {})
    search_criteria = profile.get("search_criteria", {})
    standard_screening = profile.get("standard_screening_answers", {})
    role_profiles: Dict[str, Any] = profile.get("role_profiles", {})

    target_locations = locations_override or search_criteria.get(
        "target_locations",
        ["Calicut, Kerala", "Kochi, Kerala", "Bangalore, Karnataka", "Remote"]
    )
    target_sources = sources_override or search_criteria.get(
        "target_sources",
        ["LinkedIn", "Indeed", "Naukri", "Glassdoor"]
    )
    date_posted = date_posted_override or search_criteria.get("date_posted", "today")
    min_score = threshold_override if threshold_override is not None else search_criteria.get("min_fit_score", 70)

    console.print(f"[bold]Candidate:[/bold] {personal.get('first_name')} {personal.get('last_name')} ({personal.get('location')})")
    console.print(f"[bold]Active Tracks:[/bold] {', '.join(role_profiles.keys())}")
    console.print(f"[bold]Target Locations:[/bold] {', '.join(target_locations)}")
    console.print(f"[bold]Target Platforms:[/bold] {', '.join(target_sources)}")
    console.print(f"[bold]Execution Mode:[/bold] {'[yellow]Dry-Run[/yellow]' if dry_run else '[red]LIVE APPLICATION[/red]'} | [bold]Min Score:[/bold] {min_score}\n")

    # 3. Initialize Modules
    discovery = RoleJobDiscovery(db_path=db_path)
    evaluator = JobFitEvaluator()
    submitter = ApplicationSubmitter(headless=True)
    notifier = NotificationDispatcher()

    total_submitted_or_flagged = 0

    # 4. Nested Loops: Role Profiles -> Target Roles -> Target Locations
    for profile_key, profile_data in role_profiles.items():
        if track_filter and profile_key != track_filter:
            continue

        if total_submitted_or_flagged >= max_apps_total:
            console.print(f"[yellow]Reached session limit of {max_apps_total} applications. Stopping.[/yellow]")
            break

        resume_path = profile_data.get("resume_path", "assets/resume.pdf")
        target_roles = roles_override or profile_data.get("target_roles", [])
        expected_salary = profile_data.get("expected_salary")

        # Track-specific screening answers with customized expected salary
        track_screening = dict(standard_screening)
        if expected_salary:
            track_screening["salary_expectation"] = str(expected_salary)

        salary_display = f" | Expected Salary: {expected_salary} INR" if expected_salary else ""
        console.rule(f"[bold magenta]Track: {profile_key.upper()} (Resume: {resume_path}{salary_display})[/bold magenta]")

        for role in target_roles:
            if total_submitted_or_flagged >= max_apps_total:
                break

            for loc in target_locations:
                if total_submitted_or_flagged >= max_apps_total:
                    break

                console.print(f"\n[bold blue]▶ Searching: {role} in {loc} (Country: India/Global)[/bold blue]")

                jobs = await discovery.search_by_role(
                    role=role,
                    location=loc,
                    date_posted=date_posted,
                    country="in",
                    sources=target_sources
                )

                # Rate limiting pacing sleep between search API calls
                await asyncio.sleep(rate_limit_sleep)

                if not jobs:
                    console.print(f"[dim]  No unseen postings found for '{role}' in '{loc}'.[/dim]")
                    continue

                for job in jobs:
                    if total_submitted_or_flagged >= max_apps_total:
                        break

                    company = job["company"]
                    title = job["title"]
                    url = job["url"]
                    job_hash = job["job_hash"]
                    description = job["description"]
                    job_loc = job["location"]

                    console.print(f"\n[cyan]Evaluating Posting:[/cyan] [bold]{title}[/bold] @ {company} ({job_loc})")
                    console.print(f"  URL: [dim]{url}[/dim]")

                    # 5. Gemini Structured Fit Evaluation tailored to this specific profile track
                    eval_result = await evaluator.evaluate_job_fit(
                        job_description=description,
                        role_profile=profile_data,
                        global_screening=track_screening,
                        min_score_threshold=min_score,
                        job_title=title
                    )

                    fit_score = eval_result["fit_score"]
                    should_apply = eval_result["should_apply"]
                    reason = eval_result["reason"]
                    custom_answers = eval_result.get("custom_answers", {})

                    console.print(f"  Score: [bold]{fit_score}/100[/bold] | Apply Recommendation: [bold]{should_apply}[/bold]")
                    console.print(f"  Reason: [italic]{reason}[/italic]")

                    # 6. Submission or Skip
                    if should_apply and fit_score >= min_score:
                        console.print(f"  [green]▶ High match for track '{profile_key}'! Initiating Submitter...[/green]")

                        sub_res = await submitter.submit_application(
                            job_url=url,
                            personal_info=personal,
                            resume_path=resume_path,
                            answers=custom_answers,
                            dry_run=dry_run
                        )

                        outcome_status = sub_res["status"]
                        outcome_msg = sub_res["message"]
                        console.print(f"  Outcome: [bold]{outcome_status}[/bold] ({outcome_msg})")

                        # Update SQLite Record
                        await update_application_status(
                            job_hash=job_hash,
                            status=outcome_status,
                            error_message=outcome_msg,
                            fit_score=fit_score,
                            profile_used=profile_key,
                            screening_data=custom_answers,
                            db_path=db_path
                        )

                        # 7. Dispatch Webhook Notification
                        console.print("  [blue]Dispatching notification alert...[/blue]")
                        await notifier.notify_application_event(
                            title=title,
                            company=company,
                            url=url,
                            fit_score=fit_score,
                            status=outcome_status,
                            reason=reason,
                            message=outcome_msg,
                            profile_used=profile_key,
                            source=job.get("source", "")
                        )

                        total_submitted_or_flagged += 1
                    else:
                        console.print("  [dim]Score below threshold. Marking as SKIPPED.[/dim]")
                        await update_application_status(
                            job_hash=job_hash,
                            status="SKIPPED",
                            error_message="Below minimum score threshold",
                            fit_score=fit_score,
                            profile_used=profile_key,
                            db_path=db_path
                        )

    console.rule("[bold green]All Pipeline Tracks Completed[/bold green]")
    await display_applications_summary(db_path=db_path)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-Role & Location Job Discovery & Application Engine CLI")
    parser.add_argument(
        "--profile",
        type=str,
        default="config/profile.json",
        help="Path to candidate profile JSON file"
    )
    parser.add_argument(
        "--track",
        type=str,
        default=None,
        help="Filter to a specific role profile track (e.g. laravel_backend, dotnet_backend)"
    )
    parser.add_argument(
        "--roles",
        type=str,
        default=None,
        help="Comma-separated list of target roles (overrides profile.json)"
    )
    parser.add_argument(
        "--locations",
        type=str,
        default=None,
        help="Comma-separated list of target locations (e.g. 'Calicut, Kerala,Bangalore, Karnataka')"
    )
    parser.add_argument(
        "--sources",
        type=str,
        default=None,
        help="Comma-separated list of target job platforms (e.g. 'LinkedIn,Indeed,Naukri,Glassdoor')"
    )
    parser.add_argument(
        "--date-posted",
        type=str,
        default=None,
        help="Date filter for job discovery (e.g. all, today, 3days, week)"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=None,
        help="Minimum fit score (0-100) to trigger application"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Run in live application mode (default: dry-run mode)"
    )
    parser.add_argument(
        "--max-apps",
        type=int,
        default=5,
        help="Maximum jobs to submit/process in this run"
    )
    parser.add_argument(
        "--show-db",
        action="store_true",
        help="Display SQLite applications table and exit"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.show_db:
        db_path = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
        asyncio.run(display_applications_summary(db_path))
        return

    roles = [r.strip() for r in args.roles.split(",") if r.strip()] if args.roles else None
    locations = [loc.strip() for loc in args.locations.split(",") if loc.strip()] if args.locations else None
    sources = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    dry_run = not args.live

    asyncio.run(
        run_engine(
            profile_path=args.profile,
            dry_run=dry_run,
            track_filter=args.track,
            roles_override=roles,
            locations_override=locations,
            sources_override=sources,
            date_posted_override=args.date_posted,
            threshold_override=args.threshold,
            max_apps_total=args.max_apps
        )
    )


if __name__ == "__main__":
    main()
