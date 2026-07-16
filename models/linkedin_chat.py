from typing import Optional
from pydantic import BaseModel


class LinkedInChatRequest(BaseModel):
    account_id: str = "Ryi4vdHxS8O4ChMtypDbLQ"
    text: str
    attendees_ids: str
    linkedin_api: str = "recruiter"
    linkedin_inmail: bool = True