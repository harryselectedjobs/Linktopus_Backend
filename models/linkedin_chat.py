from typing import Optional
from pydantic import BaseModel


class LinkedInChatRequest(BaseModel):
    account_id: str = "Go4TXZgASryd_z5opPJ_Ow"
    text: str
    attendees_ids: str
    linkedin_api: str = "recruiter"
    linkedin_inmail: bool = True