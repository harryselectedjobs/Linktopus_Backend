from typing import Optional
from pydantic import BaseModel

from models.linkedin_job import LinkedInJobRequest
from models.linkedin_search import SeniorityFilter


class LinkedInCampaignRequest(BaseModel):
    payload: LinkedInJobRequest
    seniority: Optional[SeniorityFilter] = None
    inmailMessage: str
    noteMessage: Optional[str] = None
    candidateSearchLocation: Optional[str] = None
    max_candidates: Optional[int] = None