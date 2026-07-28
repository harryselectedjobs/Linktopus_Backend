from fastapi import APIRouter
import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from linkedIn_services.linkedin_recruiter_automation.automation_service import run_linkedin_job_and_outreach_campaign
from linkedIn_services.linkedin_recruiter_automation.new_automation_service import search_linkedin_people, \
    run_outreach_pipeline
from models.linkedin_campaign import LinkedInCampaignRequest

router = APIRouter(
    prefix="/automation",
    tags=["linkedin automation"]
)


@router.post("/linkedin/campaign")
async def post_linkedin_campaign(request: LinkedInCampaignRequest):
    return await run_linkedin_job_and_outreach_campaign(
        payload=request.payload,
        seniority=request.seniority,
        inmailMessage=request.inmailMessage,
        noteMessage=request.noteMessage,
        candidateSearchLocation=request.candidateSearchLocation,
        max_candidates=request.max_candidates,
    )


DEFAULT_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"


# ── Router ────────────────────────────────────────────────────────────────

class OutreachPipelineRequest(BaseModel):
    project_name:str
    keyword: str
    inmail_message: str
    connection_message: str | None = None
    limit: int = 100


class OutreachPipelineResponse(BaseModel):
    project_id: str
    message: str


@router.post("/outreach/run", response_model=OutreachPipelineResponse)
async def trigger_outreach_pipeline(payload: OutreachPipelineRequest):
    project_id = str(uuid.uuid4())

    try:
        await run_outreach_pipeline(
            account_id=DEFAULT_ACCOUNT_ID,
            project_id=project_id,
            project_name=payload.project_name,
            keyword=payload.keyword,
            inmail_message=payload.inmail_message,
            connection_message=payload.connection_message,
            limit=payload.limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Outreach pipeline failed: {str(e)}")

    return OutreachPipelineResponse(
        project_id=project_id,
        message="Outreach pipeline completed successfully",
    )