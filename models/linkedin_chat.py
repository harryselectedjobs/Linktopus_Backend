from typing import Optional
from pydantic import BaseModel


class LinkedInChatRequest(BaseModel):
    account_id: str = "D8lUBYotRuGOlA7cOQ4egQ"
    text: str
    attendees_ids: str
    linkedin_api: str = "recruiter"
    linkedin_inmail: bool = True