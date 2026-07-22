from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class JobTitle(BaseModel):
    id: str
    text: str


class Company(BaseModel):
    id: str
    text: str


class ApplyMethod(BaseModel):
    type: Literal["linkedin", "external"] = "linkedin"
    resume_required: bool = True
    notification_email: Optional[str] = None
    # external apply url, only used when type == "external"
    url: Optional[str] = None


class RecruiterProject(BaseModel):
    name: str


class RecruiterInfo(BaseModel):
    project: RecruiterProject
    functions: List[str]
    industries: List[str]
    seniority: Literal[
        "INTERNSHIP",
        "ENTRY_LEVEL",
        "ASSOCIATE",
        "MID_SENIOR_LEVEL",
        "DIRECTOR",
        "EXECUTIVE",
    ]
    apply_method: ApplyMethod


class LinkedInJobRequest(BaseModel):
    job_title: JobTitle
    company: Company
    workplace: Literal["ON_SITE", "REMOTE", "HYBRID"]
    recruiter: RecruiterInfo
    account_id: str
    location: str
    employment_status: Literal[
        "FULL_TIME",
        "PART_TIME",
        "CONTRACT",
        "TEMPORARY",
        "OTHER",
        "VOLUNTEER",
        "INTERNSHIP",
    ]
    description: str = Field(..., min_length=1)