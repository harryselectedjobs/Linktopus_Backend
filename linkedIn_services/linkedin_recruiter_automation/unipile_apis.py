import httpx
from fastapi import Response
from typing import Any, Optional
import json
from fastapi.responses import JSONResponse
import asyncio


from core.config import settings
from models.calendar_event import BookEventRequest

from models.linkedin_chat import LinkedInChatRequest
from models.linkedin_job import LinkedInJobRequest
from models.linkedin_search import LinkedInSearchRequest, RoleFilter, LocationFilter, SeniorityFilter
from models.linkedin_user_action import LinkedInUserActionRequest, LinkedInInviteRequest

BASE_URL = "https://api40.unipile.com:17060/api/v1"

CREATE_JOBS_PATH = "/linkedin/jobs"
SEARCH_PATH = "/linkedin/search"
LINKEDIN_USER_PATH = "/linkedin/user"
CHATS_PATH = "/chats"
USERS_PATH = "/users"
INVITE_PATH = "/users/invite"
UNIPILE_CHATS_PATH = "/chats"
CALENDARS_PATH = "/calendars"



def _build_response(resp: httpx.Response) -> Response:
    try:
        body = resp.json()
        return JSONResponse(content=body, status_code=resp.status_code)
    except ValueError:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "text/plain"),
        )


def _safe_json(resp: httpx.Response) -> Optional[Any]:
    try:
        return resp.json()
    except ValueError:
        return None


def _headers(json_content: bool = True) -> dict:
    headers = {"X-API-KEY": settings.UNIPILE_API_KEY}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def _calendar_headers() -> dict:
    return {
        "X-API-KEY": settings.UNIPILE_CALENDAR_API_KEY,
        "Content-Type": "application/json",
        "accept": "application/json",
    }


# ---------------------------------------------------------------------------
# Raw calls (return httpx.Response) — used by the orchestrator below so it
# can read job_id, items, provider_id etc. without unwrapping FastAPI Response.
# ---------------------------------------------------------------------------

async def _create_linkedin_job_raw(payload: LinkedInJobRequest) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{CREATE_JOBS_PATH}",
            headers=_headers(),
            content=payload.model_dump_json(exclude_none=True),
        )


async def _search_linkedin_recruiter_raw(
    account_id: str, payload: LinkedInSearchRequest, cursor: Optional[str] = None
) -> httpx.Response:
    params = {"account_id": account_id}
    if cursor:
        params["cursor"] = cursor

    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{SEARCH_PATH}",
            headers=_headers(),
            params=params,
            content=payload.model_dump_json(exclude_none=True),
        )


async def _linkedin_user_action_raw(user_id: str, payload: LinkedInUserActionRequest) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{LINKEDIN_USER_PATH}/{user_id}",
            headers=_headers(),
            content=payload.model_dump_json(exclude_none=True),
        )


async def _create_linkedin_chat_raw(payload: LinkedInChatRequest) -> httpx.Response:
    form_data = {
        "account_id": payload.account_id,
        "text": payload.text,
        "attendees_ids": payload.attendees_ids,
        "linkedin[api]": payload.linkedin_api,
        "linkedin[inmail]": str(payload.linkedin_inmail).lower(),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{CHATS_PATH}",
            headers=_headers(json_content=False),
            data=form_data,
        )


async def _get_linkedin_user_profile_raw(public_identifier: str, account_id: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(
            f"{BASE_URL}{USERS_PATH}/{public_identifier}",
            headers=_headers(json_content=False),
            params={"account_id": account_id},
        )


async def _invite_linkedin_user_raw(payload: LinkedInInviteRequest) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{INVITE_PATH}",
            headers=_headers(),
            content=payload.model_dump_json(exclude_none=True),
        )


async def _get_chat_messages_raw(chat_id: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(
            f"{BASE_URL}{UNIPILE_CHATS_PATH}/{chat_id}/messages",
            headers=_headers(json_content=False),
        )


async def get_chat_messages(chat_id: str) -> Response:
    """
    Fetches messages for a given chat via Unipile.
    """
    return _build_response(await _get_chat_messages_raw(chat_id))




def get_chat_summary(data: dict) -> dict:
    conversation = []

    for msg in reversed(data.get("items", [])):  # Oldest -> Newest
        conversation.append({
            "sender": "You" if msg.get("is_sender") else "Candidate",
            "timestamp": msg.get("timestamp"),
            "message": msg.get("text", "").strip(),
        })

    return {
        "chat_id": conversation[0]["timestamp"] if conversation else None,  # optional
        "total_messages": len(conversation),
        "conversation": conversation,
    }



async def _book_event_raw(calendar_id: str, account_id: str, payload: BookEventRequest) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{BASE_URL}{CALENDARS_PATH}/{calendar_id}/events",
            headers=_calendar_headers(),
            params={"account_id": account_id},
            content=payload.model_dump_json(exclude_none=True),
        )


async def book_event(payload: BookEventRequest) -> Response:
    calendar_id="AQMkADAwATM3ZmYAZS0xYzc0LWI5ZDctMDACLTAwCgBGAAADn5WgwTeI_kOEVuMg7XKvLAcAfDaEH8uxckCdl0Wmx6GJuAAAAgEGAAAAfDaEH8uxckCdl0Wmx6GJuAAAAhL6AAAA"
    account_id="g89rd7YaSy-oS9aviCL_zg"
    return _build_response(await _book_event_raw(calendar_id, account_id, payload))
