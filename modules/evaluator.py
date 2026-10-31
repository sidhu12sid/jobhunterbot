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
        min_score_threshold: int = 70
    ) -> Dict[str, Any]:
        """Evaluate fit between job description and a specific sub-profile from role_profiles.

        Measures match against the specific profile's primary_skills and summary.
        Returns a dictionary with:
        - fit_score (int: 0-100)
        - should_apply (bool)
        - reason (str)
        - custom_answers (dict)
        """
        # Extract skills, summary, and track-specific salary
        primary_skills = role_profile.get("primary_skills") or role_profile.get("skills") or []
        summary = role_profile.get("summary") or ""
        experience_years = role_profile.get("experience_years", 3)
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
                summary=summary,
                target_roles=target_roles,
                experience_years=experience_years,
                screening=screening_answers,
                min_threshold=min_score_threshold
            )

        system_instruction = (
            "You are an expert technical recruiter assessing candidate-job fit for a specific engineering role. "
            "Compare the candidate's target role profile (specifically primary_skills, summary, and experience_years) against the job description strictly and objectively. "
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
                "primary_skills": primary_skills,
                "summary": summary,
                "experience_years": experience_years,
                "target_roles": target_roles
            },
            "standard_screening_answers": screening_answers,
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
                summary=summary,
                target_roles=target_roles,
                experience_years=experience_years,
                screening=screening_answers,
                min_threshold=min_score_threshold
            )

    def _heuristic_fallback(
        self,
        job_description: str,
        primary_skills: list,
        summary: str,
        target_roles: list,
        experience_years: int,
        screening: Dict[str, str],
        min_threshold: int
    ) -> Dict[str, Any]:
        """Deterministic heuristic scoring based on role profile skills and required experience."""
        jd_lower = job_description.lower()

        # Check matched primary skills with safe punctuation handling (e.g. .NET, C#, C++, Vue.js)
        def skill_matches(skill: str, text: str) -> bool:
            s = skill.strip().lower()
            pattern = rf"(?:^|[^a-zA-Z0-9]){re.escape(s)}(?:$|[^a-zA-Z0-9])"
            return bool(re.search(pattern, text))

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

        # Base 50 + skill points + role points + experience adjustment
        skill_boost = min(len(matched_skills) * 7, 35)
        role_boost = 15 if matched_roles else 0
        fit_score = max(min(50 + skill_boost + role_boost + exp_adjustment, 98), 15)

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
