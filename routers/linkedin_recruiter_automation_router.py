from fastapi import APIRouter

from linkedIn_services.linkedin_recruiter_automation.automation_service import run_linkedin_job_and_outreach_campaign
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