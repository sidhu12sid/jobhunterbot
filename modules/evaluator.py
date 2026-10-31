import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Suppress internal Google GenAI SDK info logs like AFC
logging.getLogger("google_genai").setLevel(logging.WARNING)


class ScreeningQA(BaseModel):
    question: str = Field(..., description="The screening question detected")
    answer: str = Field(..., description="The inferred answer based on candidate qualifications")


class JobFitEvaluation(BaseModel):
    fit_score: int = Field(..., ge=0, le=100, description="Match score from 0 to 100")
    should_apply: bool = Field(..., description="Whether the candidate should apply")
    reason: str = Field(..., description="Concise rationale explaining the score and alignment")
    custom_answers: list[ScreeningQA] = Field(
        default_factory=list,
        description="Screening questions identified in the JD mapped to candidate answers"
    )


def fast_heuristic_prefilter(
    job_title: str,
    job_description: str,
    track_config: Dict[str, Any]
) -> tuple[bool, str]:
    """Fast-Reject Pre-Filter: Run synchronously before invoking the Gemini API.

    Drops non-tech domains, high seniority mismatches, missing core anchors,
    and conflicting tech stacks locally without burning LLM API quota.
    """
    title_lower = (job_title or "").strip().lower()
    desc_lower = (job_description or "").strip().lower()
    combined_lower = f"{title_lower} {desc_lower}"
    track_name = track_config.get("track_name", "Software Engineering")

    # 1. Negative title filters (drop immediately)
    disqualified_keywords = [
        "legal", "content specialist", "marketing", "seo", "growth lead",
        "affiliate", "sdr", "sales", "qa tester", "manual qa", "flutter",
        "odoo", "ux writer", "recruiter", "hr executive", "telecaller",
        "accountant", "graphic designer", "video editor", "business development",
        "inside sales", "counsel", "advocate", "copywriter", "receptionist"
    ]
    for bad_word in disqualified_keywords:
        pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(bad_word)}(?:$|[^a-zA-Z0-9])"
        if re.search(pattern, title_lower):
            return False, f"Disqualified: Title '{job_title}' contains non-matching domain keyword '{bad_word}'."

    # 2. Strict Seniority / Experience Guardrail (Decoupled per Track)
    is_laravel = "laravel" in track_name.lower() or any("php" == a.lower() or "laravel" == a.lower() for a in (track_config.get("core_anchors") or []))

    if is_laravel:
        # Laravel Track: Candidate has 1+ year experience. Accept 1-3 years. Reject 4+ or 5+ years.
        high_exp_matches = re.findall(
            r"(\b[4-9]|\b1[0-9])\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:exp|experience|relevant|hands-on)",
            desc_lower
        )
        if high_exp_matches:
            try:
                req_years = min(int(m) for m in high_exp_matches if int(m) <= 20)
                if req_years >= 4:
                    return False, f"Disqualified: Role requires {req_years}+ years experience; candidate has 1+ year in Laravel."
            except Exception:
                pass

        senior_lead_pattern = r"\b(lead|principal|staff engineer|lead architect|chief architect|director|vp|head of)\b"
        if re.search(senior_lead_pattern, title_lower):
            return False, f"Disqualified: Title '{job_title}' specifies senior/lead role beyond candidate's 1+ year Laravel experience."
    else:
        # .NET Track: Candidate has 4 years experience. Accept up to 4-5 years. Reject 6+ or 7+ years.
        high_exp_matches = re.findall(
            r"(\b[6-9]|\b1[0-9])\+?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:exp|experience|relevant|hands-on)",
            desc_lower
        )
        if high_exp_matches:
            try:
                req_years = min(int(m) for m in high_exp_matches if int(m) <= 20)
                if req_years >= 6:
                    return False, f"Disqualified: Role requires {req_years}+ years experience; candidate has 4 years in .NET/software development."
            except Exception:
                pass

        senior_lead_pattern = r"\b(principal|staff engineer|lead architect|chief architect|director of engineering|vp of engineering)\b"
        if re.search(senior_lead_pattern, title_lower):
            return False, f"Disqualified: Title '{job_title}' specifies senior executive/principal level beyond 4 years experience."

    # 3. Core Tech Anchor Guardrail
    core_anchors = track_config.get("core_anchors") or []
    if core_anchors:
        def skill_matches(skill: str, text: str) -> bool:
            s = skill.strip().lower()
            pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(s)}(?:$|[^a-zA-Z0-9])"
            return bool(re.search(pattern, text))

        has_anchor = any(skill_matches(anchor, combined_lower) for anchor in core_anchors)
        if not has_anchor:
            return False, f"Disqualified: Job does not require mandatory track technologies ({', '.join(core_anchors)})."

    # 4. Conflicting Tech Stack in Title
    negative_keywords = track_config.get("negative_keywords") or []
    if job_title and negative_keywords:
        contradicting = any(
            re.search(rf"(?:^|[^a-zA-Z0-9]){re.escape(neg.lower())}(?:$|[^a-zA-Z0-9])", title_lower)
            for neg in negative_keywords
        ) and not any(
            re.search(rf"(?:^|[^a-zA-Z0-9]){re.escape(anchor.lower())}(?:$|[^a-zA-Z0-9])", title_lower)
            for anchor in core_anchors
        )
        if contradicting:
            return False, f"Disqualified: Job title '{job_title}' indicates a conflicting technology stack for {track_name}."

    return True, "Proceed to evaluation"


class JobFitEvaluator:
    """Evaluates job descriptions against a specific candidate role profile using Google Gemini API structured output."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        self.client: Optional[genai.Client] = None

        if self.api_key and self.api_key != "your_free_google_ai_studio_key":
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                logger.warning("Could not initialize Google GenAI client: %s", exc)

    async def evaluate_job_fit(
        self,
        job_description: str,
        role_profile: Dict[str, Any],
        global_screening: Optional[Dict[str, str]] = None,
        min_score_threshold: int = 70,
        job_title: str = ""
    ) -> Dict[str, Any]:
        """Evaluate fit between job description and a specific sub-profile from role_profiles.

        Measures match against the specific profile's primary_skills, core anchors, and summary.
        Returns a dictionary with:
        - fit_score (int: 0-100)
        - should_apply (bool)
        - reason (str)
        - custom_answers (dict)
        """
        # FAST-REJECT PRE-FILTER: Drop mismatches before burning any LLM calls
        is_valid, prefilter_reason = fast_heuristic_prefilter(
            job_title=job_title,
            job_description=job_description,
            track_config=role_profile
        )
        if not is_valid:
            logger.info("Fast-Reject Pre-Filter dropped posting: '%s' (%s)", job_title, prefilter_reason)
            return {
                "fit_score": 15,
                "should_apply": False,
                "reason": prefilter_reason,
                "custom_answers": {}
            }

        # Extract skills, anchors, negative keywords, summary, and track-specific salary
        primary_skills = role_profile.get("primary_skills") or role_profile.get("skills") or []
        core_anchors = role_profile.get("core_anchors") or []
        negative_keywords = role_profile.get("negative_keywords") or []
        summary = role_profile.get("summary") or ""
        track_name = role_profile.get("track_name", "Software Engineering")
        is_laravel = "laravel" in track_name.lower() or any("php" == a.lower() or "laravel" == a.lower() for a in core_anchors)
        experience_years = role_profile.get("experience_years") or (1 if is_laravel else 4)
        exp_tag = role_profile.get("subject_experience_tag") or ("1+ Year Experience" if is_laravel else "4 Years Experience")
        target_roles = role_profile.get("target_roles", [])
        expected_salary = role_profile.get("expected_salary") or role_profile.get("salary_expectation")

        screening_answers = dict(global_screening or role_profile.get("standard_screening_answers") or {
            "sponsorship_required": "No",
            "authorized_to_work": "Yes",
            "notice_period_weeks": "4"
        })

        if expected_salary:
            screening_answers["salary_expectation"] = str(expected_salary)
        elif "salary_expectation" not in screening_answers or not screening_answers["salary_expectation"]:
            screening_answers["salary_expectation"] = "Negotiable"

        if not self.client:
            logger.info("Gemini API key not supplied or placeholder. Using offline role-profile matcher.")
            return self._heuristic_fallback(
                job_description=job_description,
                primary_skills=primary_skills,
                core_anchors=core_anchors,
                negative_keywords=negative_keywords,
                summary=summary,
                target_roles=target_roles,
                screening=screening_answers,
                min_threshold=min_score_threshold,
                job_title=job_title,
                experience_years=experience_years,
                track_name=track_name
            )

        if is_laravel:
            track_exp_instructions = (
                "TRACK-SPECIFIC EXPERIENCE RULES (LARAVEL TRACK):\n"
                "- Candidate Experience Level: Exactly 1+ year of hands-on production engineering experience in PHP and Laravel.\n"
                "- Accept roles requiring 1+ year (e.g., 1-2 years, 1-3 years) as an ELIGIBLE MATCH.\n"
                "- Automatically reject roles requiring 4+ or 5+ years (heavily reduce fit_score < 55 and set should_apply to false, stating: 'EXP GAP: Requires X+ yrs vs candidate\'s 1+ yr').\n"
                "- DO NOT reference any total experience or .NET experience.\n"
            )
        else:
            track_exp_instructions = (
                "TRACK-SPECIFIC EXPERIENCE RULES (.NET TRACK):\n"
                "- Candidate Experience Level: Exactly 4 years of software development experience specializing in C#, .NET Core, and enterprise RESTful APIs.\n"
                "- Accept roles requiring up to 4 or 5 years (e.g., 2-4 years, 3-5 years) as an ELIGIBLE MATCH.\n"
                "- Only reject roles requiring 6+ or 7+ years (Senior/Lead/Architect), heavily reducing fit_score (< 55) and setting should_apply to false, stating the experience mismatch.\n"
                "- DO NOT reference any split or Laravel experience.\n"
            )

        system_instruction = (
            "You are an expert technical recruiter assessing candidate-job fit for a specific engineering role.\n"
            f"TARGET TRACK: {track_name}\n"
            f"MANDATORY CORE TECHNOLOGIES: {', '.join(core_anchors)}\n"
            f"CONFLICTING / NEGATIVE TECHNOLOGIES: {', '.join(negative_keywords)}\n"
            "STRICT STACK FILTERING RULES:\n"
            "- The job MUST be centered on the candidate's core technologies. "
            "Do NOT match jobs whose primary backend is in conflicting technologies (such as Java, Spring Boot, Python, Django, Node.js) "
            "or pure frontend roles just because secondary skills (like Git, REST APIs, SQL, Docker, or frontend frameworks) match. "
            "If the core stack does not match, set fit_score < 40 and should_apply to false.\n"
            f"{track_exp_instructions}"
            "- If the JD does not specify years of experience, evaluate primarily on primary_skills alignment.\n"
            "Output must strictly comply with the requested JSON schema. "
            "Detect any screening questions (e.g. work authorization, notice period, tech stack experience) "
            "and provide inferred answers based on candidate qualifications."
        )

        user_content = {
            "role_profile": {
                "track_name": track_name,
                "core_anchors": core_anchors,
                "primary_skills": primary_skills,
                "summary": summary,
                "experience_years": experience_years,
                "experience_tag": exp_tag,
                "target_roles": target_roles
            },
            "standard_screening_answers": screening_answers,
            "job_title": job_title,
            "job_description": job_description[:5000]
        }

        prompt = f"{system_instruction}\n\nCandidate Profile Track & Job Description:\n{json.dumps(user_content, indent=2)}"

        try:
            # Enforce 20s timeout so Google API retries don't freeze the pipeline
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JobFitEvaluation,
                        temperature=0.2
                    )
                ),
                timeout=20.0
            )

            raw_text = response.text or "{}"
            parsed = json.loads(raw_text)

            fit_score = int(parsed.get("fit_score", 50))
            should_apply = parsed.get("should_apply", fit_score >= min_score_threshold)
            reason = parsed.get("reason", "Evaluated via Gemini API")

            # Convert custom_answers list of {question, answer} to dict
            raw_qa = parsed.get("custom_answers", [])
            custom_answers: Dict[str, str] = {}
            if isinstance(raw_qa, list):
                for item in raw_qa:
                    if isinstance(item, dict) and "question" in item and "answer" in item:
                        custom_answers[item["question"]] = item["answer"]
            elif isinstance(raw_qa, dict):
                custom_answers = raw_qa

            return {
                "fit_score": fit_score,
                "should_apply": bool(should_apply and fit_score >= min_score_threshold),
                "reason": str(reason),
                "custom_answers": custom_answers
            }
        except Exception as exc:
            logger.error("Gemini API evaluation failed: %s. Reverting to role-profile heuristic.", exc)
            return self._heuristic_fallback(
                job_description=job_description,
                primary_skills=primary_skills,
                core_anchors=core_anchors,
                negative_keywords=negative_keywords,
                summary=summary,
                target_roles=target_roles,
                screening=screening_answers,
                min_threshold=min_score_threshold,
                job_title=job_title,
                experience_years=experience_years,
                track_name=track_name
            )

    def _heuristic_fallback(
        self,
        job_description: str,
        primary_skills: list,
        summary: str,
        target_roles: list,
        screening: Optional[Dict[str, str]] = None,
        min_threshold: int = 70,
        core_anchors: Optional[list] = None,
        negative_keywords: Optional[list] = None,
        job_title: str = "",
        experience_years: Optional[int] = None,
        track_name: str = "",
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Deterministic heuristic scoring based on role profile skills, core anchors, and decoupled track experience."""
        if screening is None:
            screening = {}

        is_laravel = "laravel" in (track_name or "").lower() or any("php" == a.lower() or "laravel" == a.lower() for a in (core_anchors or []))

        jd_lower = job_description.lower()
        title_lower = job_title.lower() if job_title else ""
        combined_text = f"{title_lower} {jd_lower}"

        # Check matched primary skills with safe punctuation handling (e.g. .NET, C#, C++, Vue.js)
        def skill_matches(skill: str, text: str) -> bool:
            s = skill.strip().lower()
            pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(s)}(?:$|[^a-zA-Z0-9])"
            return bool(re.search(pattern, text))

        # Check core anchor requirement
        has_core_anchor = False
        if core_anchors:
            has_core_anchor = any(skill_matches(anchor, combined_text) for anchor in core_anchors)
            if not has_core_anchor:
                return {
                    "fit_score": 15,
                    "should_apply": False,
                    "reason": f"Role-profile evaluation: Missing mandatory core technology anchors [{', '.join(core_anchors)}].",
                    "custom_answers": {}
                }

        matched_skills = [
            skill for skill in primary_skills
            if skill_matches(skill, jd_lower)
        ]

        # Check matched target role tokens
        matched_roles = [
            r for r in target_roles
            if any(token.lower() in jd_lower for token in re.split(r"[\s/().]+", r) if len(token) > 2)
        ]

        # Experience matching: detect required years in JD
        exp_matches = re.findall(
            r"(\d+)\s*(?:\+|-\s*\d+)?\s*(?:years?|yrs?)(?:\s+of)?\s+(?:exp|experience|relevant|hands-on)",
            jd_lower
        )
        required_min_years = None
        if exp_matches:
            try:
                matched_ints = [int(m) for m in exp_matches if int(m) <= 20]
                if matched_ints:
                    required_min_years = min(matched_ints)
            except Exception:
                pass

        exp_adjustment = 0
        exp_note = ""
        is_exp_mismatch = False

        if required_min_years is not None:
            if is_laravel:
                # Laravel Track: Candidate has 1+ year. Accept 1-3 years. Reject 4+ or 5+ years.
                if required_min_years >= 4:
                    exp_adjustment = -35
                    is_exp_mismatch = True
                    exp_note = f" [EXP GAP: Requires {required_min_years}+ yrs vs candidate's 1+ yr]"
                elif required_min_years <= 3:
                    exp_adjustment = 10
                    exp_note = f" [EXP MATCH: Requires {required_min_years} yrs; candidate has 1+ yr hands-on Laravel experience]"
            else:
                # .NET Track: Candidate has 4 years. Accept up to 4-5 years. Reject 6+ or 7+ years.
                if required_min_years >= 6:
                    exp_adjustment = -35
                    is_exp_mismatch = True
                    exp_note = f" [EXP GAP: Requires {required_min_years}+ yrs vs candidate's 4 yrs experience]"
                elif required_min_years <= 5:
                    exp_adjustment = 10
                    exp_note = f" [EXP MATCH: Requires {required_min_years} yrs; candidate has 4 yrs .NET software engineering experience]"

        # Scoring: Base (20) + Core anchor (30) + Primary skill boost (up to 25) + Role boost (15) + Exp adjustment
        anchor_boost = 30 if has_core_anchor or not core_anchors else 0
        skill_boost = min(len(matched_skills) * 5, 25)
        role_boost = 15 if matched_roles else 0
        fit_score = max(min(20 + anchor_boost + skill_boost + role_boost + exp_adjustment, 98), 10)

        should_apply = bool(fit_score >= min_threshold and not is_exp_mismatch)

        skills_summary = ", ".join(matched_skills[:5]) if matched_skills else "General software principles"
        reason = (
            f"Role-profile evaluation: Matched primary skills [{skills_summary}] "
            f"with role track targets [{', '.join(matched_roles[:2]) if matched_roles else 'Backend Engineering'}]."
            f"{exp_note}"
        )

        salary_val = screening.get("salary_expectation") or "Negotiable"
        formatted_salary = f"{salary_val} INR" if salary_val.isdigit() else str(salary_val)

        custom_answers = {
            "Are you authorized to work in India?": screening.get("authorized_to_work", "Yes"),
            "Do you require visa sponsorship?": screening.get("sponsorship_required", "No"),
            "Notice period": f"{screening.get('notice_period_weeks', '4')} weeks",
            "Expected CTC / Salary": formatted_salary
        }

        return {
            "fit_score": fit_score,
            "should_apply": should_apply,
            "reason": reason,
            "custom_answers": custom_answers
        }
