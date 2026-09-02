from fastapi import APIRouter
import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from linkedIn_services.linkedin_recruiter_automation.automation_service import run_linkedin_job_and_outreach_campaign
from linkedIn_services.linkedin_recruiter_automation.new_automation_service import run_outreach_pipeline
from linkedIn_services.linkedin_recruiter_automation.unipile_apis import list_recruiter_projects, \
    list_project_pipeline_candidates
from models.linkedin_campaign import LinkedInCampaignRequest
from repository.new_automation_pipeline import get_all_projects, get_project_details

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
    project_name: str
    roles: list[str]
    companies: list[str]
    locations: list[str]
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
            roles=payload.roles,
            companies=payload.companies,
            locations=payload.locations,
            inmail_message=payload.inmail_message,
            connection_message=payload.connection_message,
            limit=payload.limit,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Outreach pipeline failed: {str(e)}"
        )

    return OutreachPipelineResponse(
        project_id=project_id,
        message="Outreach pipeline completed successfully",
    )


# ── Project listing / details ───────────────────────────────────────────────

class ProjectSummary(BaseModel):
    project_id: str
    project_name: str = ""
    candidate_count: int
    first_created_at: str | None = None


class ProjectDetailsResponse(BaseModel):
    project_id: str
    candidate_count: int
    candidates: list[dict]


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects():
    try:
        return get_all_projects()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list projects: {str(e)}")


@router.get("/projects/{project_id}", response_model=ProjectDetailsResponse)
async def get_project(project_id: str):
    try:
        return get_project_details(project_id)
    except HTTPException:
        raise  # preserve the 404 raised inside get_project_details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch project: {str(e)}")



# ── Project listing / details UNIPILE  ───────────────────────────────────────────────

# ============================================================
# UNIPILE - LIST RECRUITER PROJECTS
# GET /automation/linkedin/recruiter/projects
# ============================================================

@router.get("/linkedin/recruiter/projects")
async def get_linkedin_recruiter_projects():
    try:
        return await list_recruiter_projects()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch LinkedIn Recruiter projects: {str(e)}",
        )


# ============================================================
# UNIPILE - LIST PROJECT PIPELINE CANDIDATES
# POST /automation/linkedin/recruiter/projects/{project_id}/pipeline
# ============================================================

@router.post(
    "/linkedin/recruiter/projects/{project_id}/pipeline"
)
async def get_linkedin_project_pipeline_candidates(
    project_id: str,
):
    try:
        return await list_project_pipeline_candidates(
            project_id
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to fetch project pipeline candidates: "
                f"{str(e)}"
            ),
        )

