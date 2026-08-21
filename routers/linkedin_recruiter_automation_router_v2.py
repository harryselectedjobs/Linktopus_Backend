from fastapi import APIRouter
import uuid
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from linkedIn_services.linkedin_recruiter_automation_v2.jd_generator import generate_job_description
from linkedIn_services.linkedin_recruiter_automation_v2.linkedin_search_via_unipile import run_pipeline_v2


router = APIRouter(
    prefix="/v2-automation",
    tags=["linkedin automation v2"]
)


# ---------------------------------------------------------------------------
# /generate-jd — layman requirement -> job description (jd_generator.py)
# ---------------------------------------------------------------------------
class GenerateJDRequest(BaseModel):
    user_input: str = Field(..., min_length=1, description="Hiring requirement in plain language")


class GenerateJDResponse(BaseModel):
    request_id: str
    jd_text: str


class GenerateJDIncompleteResponse(BaseModel):
    request_id: str
    missing_fields: list[str]
    message: str


@router.post("/generate-jd")
def generate_jd(payload: GenerateJDRequest):
    request_id = str(uuid.uuid4())
    try:
        result = generate_job_description(payload.user_input)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"JD generation failed: {exc}") from exc

    if "jd_text" in result:
        return GenerateJDResponse(
            request_id=request_id,
            jd_text=result["jd_text"],
        )

    # Incomplete requirement — return 200 with what's missing, not an error,
    # since this is an expected UI flow (prompt the user for more detail).
    return GenerateJDIncompleteResponse(
        request_id=request_id,
        missing_fields=result.get("missing_fields", []),
        message=result.get("message", "More information is needed to generate a job description."),
    )


# ---------------------------------------------------------------------------
# /search — job description text -> LinkedIn Recruiter search results (run_pipeline_v2)
# ---------------------------------------------------------------------------
class RunSearchRequest(BaseModel):
    jd_text: str = Field(..., min_length=1, description="Full job description text")


class RunSearchResponse(BaseModel):
    request_id: str
    result: dict


@router.post("/search")
def run_search_v2(payload: RunSearchRequest):
    request_id = str(uuid.uuid4())
    try:
        result = run_pipeline_v2(payload.jd_text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LinkedIn search pipeline failed: {exc}") from exc

    return RunSearchResponse(request_id=request_id, result=result)
