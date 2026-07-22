from typing import Optional
from pydantic import BaseModel


class LinkedInUserActionRequest(BaseModel):
    api: str = "recruiter"
    action: str ="addCandidateToPipeline"
    account_id: str
    hiring_project_id: Optional[str] = None


class LinkedInInviteRequest(BaseModel):
    provider_id: str
    account_id: str = "D8lUBYotRuGOlA7cOQ4egQ"
    message: Optional[str] = None