from typing import Optional, List
from pydantic import BaseModel


class RoleFilter(BaseModel):
    id: str
    is_selection: Optional[bool] = None


class SeniorityFilter(BaseModel):
    include: Optional[List[str]] = None
    exclude: Optional[List[str]] = None


class LocationFilter(BaseModel):
    id: str


class LinkedInSearchRequest(BaseModel):
    api: str = "recruiter"
    category: str = "people"
    role: Optional[List[RoleFilter]] = None
    seniority: Optional[SeniorityFilter] = None
    location: Optional[List[LocationFilter]] = None
    employment_type: Optional[List[str]] = None