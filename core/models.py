from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    id: str
    ats_type: str  # "greenhouse" | "lever"
    board_or_company: str
    title: str
    company: str
    location: str
    url: str
    apply_url: str
    content_raw: str
    content_hash: str
    workplace_type: Optional[str] = "unknown"
    updated_at: Optional[datetime] = None


class EvaluationResult(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Match score from 0 to 100")
    should_apply: bool = Field(..., description="Whether this candidate should apply")
    reasoning: str = Field(..., description="Summary explanation of the score")
    key_strengths: List[str] = Field(default_factory=list, description="Candidate qualifications matching the JD")
    missing_requirements: List[str] = Field(default_factory=list, description="Gaps between candidate profile and JD")
    screening_answers: Dict[str, str] = Field(
        default_factory=dict,
        description="Suggested answers to potential screening questions extracted from JD"
    )


class ApplicationRecord(BaseModel):
    id: Optional[int] = None
    job_hash: str
    title: str
    company: str
    ats_type: str
    job_url: str
    status: str = "discovered"  # discovered | evaluated | skipped | submitted | failed
    score: Optional[int] = None
    reasoning: Optional[str] = None
    applied_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
