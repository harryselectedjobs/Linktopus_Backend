import asyncio
from pathlib import Path

import httpx

UNIPILE_BASE_URL = "https://api40.unipile.com:17060/api/v1/posts"
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="

# __file__ is .../Linktopus_Backend/linkedIn_services/share_post/share_post_on_page.py
# parents[2] steps up to .../Linktopus_Backend (the project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ATTACHMENT_PATH = PROJECT_ROOT / "static_files" / "selectedjobs.png"


PROMO_SUFFIX = "\n\n🌐 Discover more opportunities and insights at https://www.selected.jobs/"


async def post_to_selected_page(text: str, attachment_path: Path = ATTACHMENT_PATH):
    text = f"{text}{PROMO_SUFFIX}"

    headers = {
        "accept": "application/json",
        "X-API-KEY": UNIPILE_API_KEY,
        # do NOT set content-type manually — httpx sets the multipart
        # boundary automatically when you use `data=` with `files=`
    }

    # Plain text fields go in `data=` (not `files=`), since they're not
    # actual file uploads — this matches --form field=value in curl.
    form_data = {
        "account_id": "D8lUBYotRuGOlA7cOQ4egQ",
        "text": text,
        "as_organization": "18055530",
    }

    with open(attachment_path, "rb") as f:
        files = {
            # (filename, file_object, content_type) — matches
            # --form attachments='@selectedjobs.png' in curl
            "attachments": (attachment_path.name, f, "image/png"),
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                UNIPILE_BASE_URL,
                data=form_data,
                files=files,
                headers=headers,
            )
            print("Status:", resp.status_code)
            print("Body:", resp.text)
            resp.raise_for_status()
            return resp.json()
