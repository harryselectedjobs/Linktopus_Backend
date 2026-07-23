import asyncio
import httpx

UNIPILE_BASE_URL = "https://api40.unipile.com:17060/api/v1/posts"
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="

async def post_to_selected_page(text: str):
    headers = {
        "accept": "application/json",
        "X-API-KEY": UNIPILE_API_KEY,
        # do NOT set content-type manually — httpx sets the multipart
        # boundary automatically when you use `data=` with `files=`
    }
    # Using (None, value) tuples forces httpx to send these as
    # multipart/form-data fields rather than a JSON body
    form_fields = {
        "account_id": (None, "D8lUBYotRuGOlA7cOQ4egQ"),
        "text": (None, text),
        "as_organization": (None, "18055530"),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(UNIPILE_BASE_URL, files=form_fields, headers=headers)
        print("Status:", resp.status_code)
        print("Body:", resp.text)
        resp.raise_for_status()
        return resp.json()