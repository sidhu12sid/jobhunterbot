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


class JobFitEvaluator:
    """Evaluates job descriptions against a specific candidate role profile using Google Gemini API structured output."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
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
        # Extract skills, anchors, negative keywords, summary, and track-specific salary
        primary_skills = role_profile.get("primary_skills") or role_profile.get("skills") or []
        core_anchors = role_profile.get("core_anchors") or []
        negative_keywords = role_profile.get("negative_keywords") or []
        summary = role_profile.get("summary") or ""
        experience_years = role_profile.get("experience_years", 3)
        target_roles = role_profile.get("target_roles", [])
        expected_salary = role_profile.get("expected_salary") or role_profile.get("salary_expectation")
        track_name = role_profile.get("track_name", "Software Engineering")

        screening_answers = dict(global_screening or role_profile.get("standard_screening_answers") or {
            "sponsorship_required": "No",
            "authorized_to_work": "Yes",
            "notice_period_weeks": "4"
        })

        if expected_salary:
            screening_answers["salary_expectation"] = str(expected_salary)
        elif "salary_expectation" not in screening_answers or not screening_answers["salary_expectation"]:
            screening_answers["salary_expectation"] = "Negotiable"

        # Safe skill match helper
        def skill_matches(skill: str, text: str) -> bool:
            s = skill.strip().lower()
            pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(s)}(?:$|[^a-zA-Z0-9])"
            return bool(re.search(pattern, text))

        combined_text = f"{job_title} {job_description}".lower()

        # 1. HARD DISQUALIFICATION: Title indicates a conflicting primary technology stack
        if job_title and negative_keywords:
            title_lower = job_title.lower()
            contradicting_title = any(
                skill_matches(neg, title_lower) for neg in negative_keywords
            ) and not any(skill_matches(anchor, title_lower) for anchor in core_anchors)

            if contradicting_title:
                logger.info("Job disqualified: Title '%s' conflicts with track '%s'", job_title, track_name)
                return {
                    "fit_score": 15,
                    "should_apply": False,
                    "reason": f"Disqualified: Job title '{job_title}' indicates a conflicting technology stack for {track_name}.",
                    "custom_answers": {}
                }

        # 2. HARD DISQUALIFICATION: Complete absence of mandatory core technology anchors
        if core_anchors:
            has_core_anchor = any(skill_matches(anchor, combined_text) for anchor in core_anchors)
            if not has_core_anchor:
                logger.info(
                    "Job disqualified: Missing mandatory core anchors %s for track '%s'",
                    core_anchors, track_name
                )
                return {
                    "fit_score": 15,
                    "should_apply": False,
                    "reason": f"Disqualified: Job does not require mandatory track technologies ({', '.join(core_anchors)}).",
                    "custom_answers": {}
                }

        if not self.client:
            logger.info("Gemini API key not supplied or placeholder. Using offline role-profile matcher.")
            return self._heuristic_fallback(
                job_description=job_description,
                primary_skills=primary_skills,
                core_anchors=core_anchors,
                negative_keywords=negative_keywords,
                summary=summary,
                target_roles=target_roles,
                experience_years=experience_years,
                screening=screening_answers,
                min_threshold=min_score_threshold,
                job_title=job_title
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
            "EXPERIENCE MATCHING RULES:\n"
            f"- Candidate Experience Level: Exactly {experience_years} year(s) of professional experience in this track.\n"
            "- If the JD requires 0-3 years (Junior to Mid-level matching candidate experience), evaluate experience positively.\n"
            "- If the JD strictly requires significantly higher experience (e.g. 5+, 7+, or 10+ years, Senior/Lead/Architect roles), heavily reduce fit_score (< 55) and set should_apply to false, explicitly stating the experience mismatch.\n"
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
                experience_years=experience_years,
                screening=screening_answers,
                min_threshold=min_score_threshold,
                job_title=job_title
            )

    def _heuristic_fallback(
        self,
        job_description: str,
        primary_skills: list,
        summary: str,
        target_roles: list,
        experience_years: int,
        screening: Dict[str, str],
        min_threshold: int,
        core_anchors: Optional[list] = None,
        negative_keywords: Optional[list] = None,
        job_title: str = ""
    ) -> Dict[str, Any]:
        """Deterministic heuristic scoring based on role profile skills, core anchors, and required experience."""
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
            if required_min_years > experience_years + 2:
                # Severe mismatch (e.g. job wants 5+ or 8+ years, candidate has 1 or 3)
                exp_adjustment = -35
                is_exp_mismatch = True
                exp_note = f" [EXP GAP: Requires {required_min_years}+ yrs vs candidate's {experience_years} yr]"
            elif required_min_years <= experience_years:
                # Well-aligned experience
                exp_adjustment = 10
                exp_note = f" [EXP MATCH: Requires {required_min_years} yrs, candidate has {experience_years} yrs]"

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
