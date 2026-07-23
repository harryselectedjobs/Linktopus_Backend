from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional

# ---------------------------------------------------------------------------
# Router-facing wrappers — same public functions your router already imports.
# Behavior unchanged, just now backed by the _raw calls above.
# ---------------------------------------------------------------------------

from linkedIn_services.linkedin_recruiter_automation.unipile_apis import _build_response, _create_linkedin_job_raw, \
    _search_linkedin_recruiter_raw, _linkedin_user_action_raw, _create_linkedin_chat_raw, \
    _get_linkedin_user_profile_raw, _invite_linkedin_user_raw, _safe_json, book_event
from models.calendar_event import BookEventRequest, EventTime, Attendee
from models.linkedin_chat import LinkedInChatRequest
from models.linkedin_job import LinkedInJobRequest
from fastapi import Response

from models.linkedin_search import LinkedInSearchRequest, SeniorityFilter, RoleFilter, LocationFilter
from models.linkedin_user_action import LinkedInUserActionRequest, LinkedInInviteRequest
from repository.schedule_calendar_services import mark_meeting_as_booked


async def create_linkedin_job(payload: LinkedInJobRequest) -> Response:
    return _build_response(await _create_linkedin_job_raw(payload))


async def search_linkedin_recruiter(
    account_id: str, payload: LinkedInSearchRequest, cursor: Optional[str] = None
) -> Response:
    return _build_response(await _search_linkedin_recruiter_raw(account_id, payload, cursor))


async def linkedin_user_action(user_id: str, payload: LinkedInUserActionRequest) -> Response:
    return _build_response(await _linkedin_user_action_raw(user_id, payload))


async def create_linkedin_chat(payload: LinkedInChatRequest) -> Response:
    return _build_response(await _create_linkedin_chat_raw(payload))


async def get_linkedin_user_profile(public_identifier: str, account_id: str) -> Response:
    return _build_response(await _get_linkedin_user_profile_raw(public_identifier, account_id))


async def invite_linkedin_user(payload: LinkedInInviteRequest) -> Response:
    return _build_response(await _invite_linkedin_user_raw(payload))


# ---------------------------------------------------------------------------
# Orchestrator: create job -> search (paginated) -> pipeline -> InMail -> invite
# ---------------------------------------------------------------------------

async def run_linkedin_job_and_outreach_campaign(
    payload: LinkedInJobRequest,
    seniority: Optional[SeniorityFilter],
    inmailMessage: str,
    noteMessage: Optional[str] = None,
    candidateSearchLocation: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> Response:
    """
    STEP 1: create the job posting.
    STEP 2: search LinkedIn Recruiter for matches (role/employment_type pulled
            from `payload`, seniority and candidate search location passed in
            separately), paginating via `cursor` until every result is collected.
    STEP 3: add each candidate to the job's hiring pipeline.
    STEP 4: send each candidate an InMail, then log a meeting scheduling
            record for them (title = recruiter project name, email pulled
            from their LinkedIn profile).
    STEP 5: look up each candidate's provider_id and send a connection invite.

    A failure on one candidate's steps doesn't stop the rest of the batch —
    it's recorded in that candidate's result entry instead.
    """
    account_id = payload.account_id

    # ---------- STEP 1 ----------
    job_resp = await _create_linkedin_job_raw(payload)
    if job_resp.status_code >= 400:
        return _build_response(job_resp)

    job_body = job_resp.json()
    project_id = job_body.get("project_id")

    # ---------- STEP 2 ----------
    search_payload = LinkedInSearchRequest(
        role=[RoleFilter(id=payload.job_title.id, is_selection=True)],
        seniority=seniority,
        location=[LocationFilter(id=candidateSearchLocation)] if candidateSearchLocation else None,
        employment_type=[payload.employment_status],
    )

    candidates: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    total_count: Optional[int] = None

    while True:
        search_resp = await _search_linkedin_recruiter_raw(account_id, search_payload, cursor)
        if search_resp.status_code >= 400:
            return _build_response(search_resp)

        search_body = search_resp.json()
        candidates.extend(search_body.get("items", []))

        paging = search_body.get("paging") or {}
        total_count = paging.get("total_count", len(candidates))
        cursor = search_body.get("cursor")

        if max_candidates and len(candidates) >= max_candidates:
            candidates = candidates[:max_candidates]
            break

        if not cursor or len(candidates) >= total_count:
            break

    # ---------- STEPS 3-5, per candidate ----------
    results: List[Dict[str, Any]] = []

    for candidate in candidates:
        user_id = candidate.get("id")
        public_identifier = candidate.get("public_identifier")
        candidate_result: Dict[str, Any] = {
            "id": user_id,
            "public_identifier": public_identifier,
            "name": candidate.get("name"),
        }

        # STEP 3: add to pipeline
        try:
            pipeline_resp = await _linkedin_user_action_raw(
                user_id,
                LinkedInUserActionRequest(account_id=account_id, hiring_project_id=project_id),
            )
            candidate_result["add_to_pipeline"] = {
                "status_code": pipeline_resp.status_code,
                "body": _safe_json(pipeline_resp),
            }
        except Exception as exc:
            candidate_result["add_to_pipeline"] = {"error": str(exc)}

        # STEP 4: send InMail
        inmail_sent = False
        try:
            chat_resp = await _create_linkedin_chat_raw(
                LinkedInChatRequest(account_id=account_id, text=inmailMessage, attendees_ids=user_id)
            )
            candidate_result["inmail"] = {
                "status_code": chat_resp.status_code,
                "body": _safe_json(chat_resp),
            }
            inmail_sent = chat_resp.status_code < 400
        except Exception as exc:
            candidate_result["inmail"] = {"error": str(exc)}

        # Fetch profile once — used for meeting record (email) and invite (provider_id)
        profile_data: Optional[dict] = None
        try:
            profile_resp = await _get_linkedin_user_profile_raw(public_identifier, account_id)
            profile_data = _safe_json(profile_resp)
        except Exception as exc:
            candidate_result["profile_lookup_error"] = str(exc)

        # After InMail is sent, log a meeting scheduling record for this candidate
        if inmail_sent:
            try:
                candidate_email = None
                if profile_data:
                    emails = (profile_data.get("contact_info") or {}).get("emails") or []
                    candidate_email = emails[0] if emails else None

                if candidate_email:
                    meeting_payload = {
                        "title": payload.recruiter.project.name,
                        "attendees": [{"email": candidate_email}],
                    }
                    meeting_record = await asyncio.to_thread(add_meeting_record, meeting_payload)
                    candidate_result["meeting_record"] = meeting_record
                else:
                    candidate_result["meeting_record"] = {"error": "no email found on candidate profile"}
            except Exception as exc:
                candidate_result["meeting_record"] = {"error": str(exc)}

        # STEP 5: invite using provider_id from the profile fetched above
        try:
            provider_id = profile_data.get("provider_id") if profile_data else None

            if provider_id:
                invite_resp = await _invite_linkedin_user_raw(
                    LinkedInInviteRequest(
                        provider_id=provider_id,
                        account_id=account_id,
                        message=noteMessage,
                    )
                )
                candidate_result["invite"] = {
                    "status_code": invite_resp.status_code,
                    "body": _safe_json(invite_resp),
                }
            else:
                candidate_result["invite"] = {"error": "provider_id not found in profile response"}
        except Exception as exc:
            candidate_result["invite"] = {"error": str(exc)}

        results.append(candidate_result)

    return JSONResponse(
        content={
            "job": job_body,
            "total_candidates_found": total_count,
            "candidates_processed": len(results),
            "results": results,
        },
        status_code=200,
    )


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


async def book_one_hour_meeting(
    date: str,
    start_time: str,
    email: str,
    title: str,
):
    london_tz = ZoneInfo("Europe/London")

    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M").replace(tzinfo=london_tz)
    end_dt = start_dt + timedelta(hours=1)

    payload = BookEventRequest(
        start=EventTime(date_time=start_dt.isoformat(), time_zone="Europe/London"),
        end=EventTime(date_time=end_dt.isoformat(), time_zone="Europe/London"),
        title=title,
        attendees=[Attendee(email=email)],
    )

    result = await book_event(payload)

    # only mark it booked if the calendar event actually succeeded
    if getattr(result, "status_code", 200) < 400:
        mark_meeting_as_booked(email)

    return result