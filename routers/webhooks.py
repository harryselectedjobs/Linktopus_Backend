from fastapi import APIRouter,Request
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from open_ai.helper_bot import extract_job_title_and_skills

router = APIRouter(
    prefix="/webhook",
    tags=["Webhooks"]
)

class JobDescriptionRequest(BaseModel):
    job_description: str

@router.post("/linkedin-message")
async def unipile_webhook(request: Request):
    try:
        payload = await request.json()

        print("\n" + "=" * 60)
        print("📩 New Webhook Received")
        print(json.dumps(payload, indent=4))
        print("=" * 60 + "\n")

        return {"status": "success"}

    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error", "message": str(e)}



@router.post("/extract-job-title-and-skills")
async def extract_job_title_and_skills_api(request: JobDescriptionRequest):
    """
    Extracts the job title and top 3 technical skills from a job description.
    """
    try:
        result = extract_job_title_and_skills(request.job_description)
        return {
            "success": True,
            "data": result
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )