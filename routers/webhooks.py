from fastapi import APIRouter,Request
import json


router = APIRouter(
    prefix="/webhook",
    tags=["Webhooks"]
)

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