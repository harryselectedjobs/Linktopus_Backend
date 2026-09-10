import asyncio
import httpx
import json
import re

from linkedIn_services.linkedin_recruiter_automation.unipile_apis import _invite_linkedin_user_raw, \
    _create_linkedin_chat_raw, _get_linkedin_user_profile_raw, _safe_json
from linkedIn_services.linkedin_recruiter_automation.filter_candidates import generate_title_variants,matches_any_title ,_get_candidate_titles
from models.linkedin_chat import LinkedInChatRequest
from models.linkedin_user_action import LinkedInInviteRequest
from repository.new_automation_pipeline import save_candidates, get_top_candidates, mark_outreach_sent
from repository.schedule_calendar_services import add_meeting_record

UNIPILE_BASE_URL = "https://api40.unipile.com:17060"
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="

UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="


def _loosen_keywords(keyword: str, logic: str) -> str:
    if logic == "OR":
        return re.sub(r"\s+AND\s+", " OR ", keyword, flags=re.IGNORECASE)
    return keyword


def _format_company_filters(companies: list[dict] | None) -> list[dict]:
    if not companies:
        return []
    return [
        {"id": str(company["id"]), "priority": company.get("priority", "CAN_HAVE")}
        for company in companies
        if company.get("id")
    ]


def _headers() -> dict:
    return {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }


async def _resolve_role_filters(client: httpx.AsyncClient, account_id: str, roles: list[str]) -> list[dict]:
    """
    Resolve job title strings -> LinkedIn/Unipile JOB_TITLE taxonomy IDs.

    Mirrors the Recruiter UI exactly: ONE title per role input (not a
    manually-expanded family of variants), priority=CAN_HAVE, and
    scope=CURRENT_OR_PAST (matching the "Current or Past" toggle shown
    in the UI screenshot — NOT "Current" only).

    is_selection is read from the nested `additional_data` field in the
    API response, NOT the top level — that was the earlier bug.
    """
    role_filters = []
    parameters_url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search/parameters"

    for role in roles or []:
        if not role or not role.strip():
            continue

        response = await client.get(
            parameters_url,
            params={
                "keywords": role,
                "service": "RECRUITER",
                "type": "JOB_TITLE",
                "account_id": account_id,
            },
            headers=_headers(),
        )
        response.raise_for_status()

        items = response.json().get("items", [])
        if not items:
            print(f"⚠️ no JOB_TITLE match found for '{role}' — skipping")
            continue

        print(f"🔎 JOB_TITLE matches for '{role}':")
        for it in items:
            is_sel = it.get("additional_data", {}).get("is_selection")
            print(f"    id={it.get('id')}  title={it.get('title')}  is_selection={is_sel}")

        # Use ONLY the first/top match — this is what the UI's single
        # text box resolves to when you type the title and it autocompletes.
        chosen = items[0]
        is_selection = chosen.get("additional_data", {}).get("is_selection", False)

        print(f"    -> using id={chosen['id']} (title={chosen.get('title')}, is_selection={is_selection})")

        role_filters.append({
            "id": chosen["id"],
            "is_selection": is_selection,
            "priority": "CAN_HAVE",         # matches UI: "Can have"
            "scope": "CURRENT_OR_PAST",     # matches UI: "Current or Past" — was "CURRENT", this was wrong
        })

    return role_filters

async def _resolve_location_filters(client: httpx.AsyncClient, account_id: str, locations: list[str]) -> list[dict]:
    """Resolve location names -> LinkedIn/Unipile location IDs."""
    location_filters = []
    parameters_url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search/parameters"

    for location in locations or []:
        response = await client.get(
            parameters_url,
            params={
                "keywords": location,
                "service": "RECRUITER",
                "type": "LOCATION",
                "account_id": account_id,
            },
            headers=_headers(),
        )
        response.raise_for_status()

        items = response.json().get("items", [])
        if not items:
            print(f"⚠️ no LOCATION match found for '{location}'")
            continue

        # DEBUG: show every candidate match, not just the one we pick
        print(f"🔎 LOCATION matches for '{location}':")
        for it in items:
            print(f"    id={it.get('id')}  title={it.get('title')}")

        chosen = items[0]
        print(f"    -> using id={chosen['id']} (title={chosen.get('title')})")

        location_filters.append(
            {"id": chosen["id"], "priority": "CAN_HAVE", "scope": "CURRENT"}
        )

    return location_filters

def _build_search_payload(role_filters: list[dict], companies: list[str], location_filters: list[dict]) -> dict:
    company_filters = [
        {"keywords": company, "priority": "CAN_HAVE", "scope": "CURRENT_OR_PAST"}
        for company in (companies or [])
        if company and company.strip()
    ]

    payload = {"api": "recruiter", "category": "people"}
    if location_filters:
        payload["location"] = location_filters
    if company_filters:
        payload["company"] = company_filters
    if role_filters:
        payload["role"] = role_filters  # already {"id": ..., "priority": ..., "scope": ...} dicts

    return payload


async def _iter_search_pages(client: httpx.AsyncClient, account_id: str, payload: dict, page_size: int = 100):
    """
    Async generator yielding raw candidate dicts across ALL pages, following
    the `cursor` field. If the cursor is null, there are no more results.
    """
    search_url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search"
    cursor = None

    while True:
        params = {"account_id": account_id, "limit": page_size}
        if cursor:
            params["cursor"] = cursor

        response = await client.post(search_url, params=params, headers=_headers(), json=payload)
        response.raise_for_status()
        data = response.json()

        items = data.get("items", [])
        for item in items:
            yield item

        cursor = data.get("cursor")
        if not cursor or not items:
            break


async def search_matching_candidates(
    account_id: str,
    roles: list[str],
    companies: list[str],
    locations: list[str],
    target_count: int = 100,
    page_size: int = 100,
) -> list[dict]:
    """
    Searches LinkedIn (paginating via cursor as needed) and returns up to
    `target_count` candidates. Trusts Unipile/LinkedIn's own server-side
    role filter (via resolved JOB_TITLE id + is_selection) instead of
    re-filtering locally with a title regex — the server-side taxonomy
    match is more accurate than a literal-text/synonym check ever was.
    """
    matched: list[dict] = []
    raw_count = 0

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            location_filters = await _resolve_location_filters(client, account_id, locations)
            role_filters = await _resolve_role_filters(client, account_id, roles)
            company_filters = [
                {"keywords": company, "priority": "CAN_HAVE", "scope": "CURRENT_OR_PAST"}
                for company in (companies or [])
                if company and company.strip()
            ]

            payload = {"api": "recruiter", "category": "people"}
            if location_filters:
                payload["location"] = location_filters
            if company_filters:
                payload["company"] = company_filters
            if role_filters:
                payload["role"] = role_filters

            print("payload")
            print(payload)
            print("payload")

            async for candidate in _iter_search_pages(client, account_id, payload, page_size):
                raw_count += 1
                matched.append(candidate)  # trust server-side role match — no local title regex

                if len(matched) >= target_count:
                    break

            print(f"RAW candidates returned by Unipile: {raw_count}")
            print(f"MATCHED (trusting server-side role filter): {len(matched)}")

    except httpx.ReadTimeout:
        print("❌ Unipile search request timed out.")
        return matched

    except httpx.HTTPStatusError as e:
        print(f"❌ Unipile search HTTP error {e.response.status_code}: {e.response.text[:500]}")
        try:
            error_body = e.response.json()
            with open("unipile_error.json", "w", encoding="utf-8") as f:
                json.dump(error_body, f, indent=2)
        except Exception:
            pass
        return matched

    except httpx.RequestError as e:
        print(f"❌ Unipile search request error: {e}")
        return matched

    except Exception as e:
        print(f"❌ Unexpected error after {len(matched)} matched (raw so far: {raw_count}): {e}")
        raise

    return matched



# ── Unipile recruiter project helpers ───────────────────────────────────────

async def create_unipile_recruiter_project(
    account_id: str,
    project_name: str,
    visibility: str = "PRIVATE",
) -> dict | None:
    url = f"{UNIPILE_PROJECTS_BASE_URL}/{account_id}/linkedin/recruiter/projects"
    headers = {
        "X-API-KEY": UNIPILE_PROJECTS_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }
    payload = {"visibility": visibility, "name": project_name}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code in (200, 201):
                return response.json()
            print(f"❌ Unipile project creation failed: {response.status_code} {response.text[:300]}")
            return None
    except httpx.RequestError as e:
        print(f"❌ Unipile project creation request error: {e}")
        return None


async def add_candidate_to_unipile_pipeline(
    account_id: str,
    hiring_project_id: str,
    candidate_linkedin_id: str,
    stage: str = "UNCONTACTED",
) -> bool:
    url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/user/{candidate_linkedin_id}"
    headers = _headers()
    payload = {
        "api": "recruiter",
        "action": "addCandidateToPipeline",
        "account_id": account_id,
        "hiring_project_id": hiring_project_id,
        "stage": stage,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code in (200, 201):
                return True
            print(f"❌ Add-to-pipeline failed for {candidate_linkedin_id}: "
                  f"{response.status_code} {response.text[:300]}")
            return False
    except httpx.RequestError as e:
        print(f"❌ Add-to-pipeline request error for {candidate_linkedin_id}: {e}")
        return False


# ── Outreach pipeline ────────────────────────────────────────────────────────

async def run_outreach_pipeline(
    account_id: str,
    project_id: str,
    project_name: str,
    roles: list[str],
    companies: list[str],
    locations: list[str],
    inmail_message: str,
    connection_message: str = None,
    limit: int = 100,
    projects_account_id: str = "acc_01m23cadhmexwtq4qvd6x3tj53",
):
    """
    `limit` is the number of TITLE-MATCHING candidates to add to the pipeline.
    Search pages are pulled (via cursor) until that many matches are found
    or the search is exhausted, then all matched candidates are added.
    """

    # ── STEP 1: Search + filter LinkedIn ─────────────────────────────────
    candidates = await search_matching_candidates(
        account_id=account_id,
        roles=roles,
        companies=companies,
        locations=locations,
        target_count=limit,
    )

    if not candidates:
        print("⚠️ No matching candidates found — aborting pipeline.")
        return

    print(f"ℹ️ Found {len(candidates)} title-matching candidates (target was {limit})")

    # Duplicate-id check
    ids = [c.get("id") for c in candidates]
    if len(set(ids)) != len(ids):
        dupes = {i for i in ids if ids.count(i) > 1}
        print(f"⚠️ {len(ids) - len(set(ids))} duplicate candidate id(s) in search response: {dupes}")

    # ── STEP 2: Save candidates ──────────────────────────────────────────
    # save_candidates(project_id, candidates, project_name)

    # ── STEP 2b: Create Unipile recruiter project + sync pipeline ───────
    unipile_project_id = None

    project_result = await create_unipile_recruiter_project(
        account_id=projects_account_id,
        project_name=project_name,
    )

    if project_result:
        unipile_project_id = project_result.get("project_id")
        print(f"✅ Unipile recruiter project '{project_name}' created (project_id={unipile_project_id})")

    if unipile_project_id:
        added_count = 0
        skipped_count = 0

        for candidate in candidates:
            candidate_linkedin_id = candidate.get("id")

            if not candidate_linkedin_id:
                skipped_count += 1
                continue

            success = await add_candidate_to_unipile_pipeline(
                account_id=account_id,
                hiring_project_id=unipile_project_id,
                candidate_linkedin_id=candidate_linkedin_id,
                stage="UNCONTACTED",
            )

            if success:
                added_count += 1

        print(f"ℹ️ {added_count}/{len(candidates)} candidates added to Unipile pipeline "
              f"(project_id={unipile_project_id}, skipped_no_id={skipped_count})")

    else:
        print("⚠️ Skipping pipeline sync — no Unipile project_id available.")

    print(f"✅ Outreach pipeline complete for project {project_id} — "
          f"{len(candidates)} candidates processed. Outreach (InMail/connection) is disabled.")


    # =====================================================================
    # STEP 3: Process candidates — DISABLED FOR NOW
    # Uncomment this whole block to resume sending InMail + connection
    # invites + meeting records.
    # =====================================================================
    #
    # # Change limit=50 if you actually want top 50
    # top_candidates = get_top_candidates(project_id, limit=1)
    #
    # for candidate in top_candidates:
    #
    #     candidate_id = candidate["candidate_id"]
    #     full_name = candidate.get("full_name")
    #
    #     # Depending on how save_candidates() stores the data,
    #     # this should be the LinkedIn public_identifier.
    #     public_identifier = candidate.get("public_identifier")
    #
    #     print(f"\n🚀 Processing candidate: {full_name}")
    #     print(f"   candidate_id: {candidate_id}")
    #     print(f"   public_identifier: {public_identifier}")
    #
    #     # Use first name for a natural-sounding greeting
    #     first_name = (full_name or "there").split()[0]
    #
    #     # =====================================================
    #     # STEP 3A: Send InMail (personalized)
    #     # =====================================================
    #     inmail_success = False
    #
    #     personalized_inmail = inmail_message.replace(
    #         "[Candidate's Name]", first_name
    #     )
    #
    #     full_inmail_text = (
    #         f"{personalized_inmail}\n\n"
    #         f"Schedule a meeting with us to learn more: https://linktopus.selected.jobs/calendar-booking"
    #     )
    #
    #     try:
    #         chat_payload = LinkedInChatRequest(
    #             account_id=account_id,
    #             text=full_inmail_text,
    #             attendees_ids=candidate_id,
    #             linkedin_api="recruiter",
    #             linkedin_inmail=True,
    #         )
    #
    #         chat_response = await _create_linkedin_chat_raw(chat_payload)
    #
    #         inmail_success = chat_response.status_code in (200, 201)
    #
    #         if not inmail_success:
    #             print(
    #                 f"❌ InMail failed for {full_name}: "
    #                 f"{chat_response.status_code} {chat_response.text}"
    #             )
    #         else:
    #             print(f"✅ InMail sent to {full_name}")
    #
    #     except Exception as exc:
    #         print(f"❌ InMail exception for {full_name}: {exc}")
    #
    #     # =====================================================
    #     # STEP 3B: Fetch LinkedIn profile
    #     # =====================================================
    #     profile_data = None
    #
    #     try:
    #         if public_identifier:
    #             profile_resp = await _get_linkedin_user_profile_raw(
    #                 public_identifier,
    #                 account_id,
    #             )
    #
    #             profile_data = _safe_json(profile_resp)
    #
    #             print(f"✅ Profile fetched for {full_name}")
    #
    #         else:
    #             print(
    #                 f"⚠️ No public_identifier found for {full_name}"
    #             )
    #
    #     except Exception as exc:
    #         print(
    #             f"❌ Profile lookup failed for {full_name}: {exc}"
    #         )
    #
    #     # =====================================================
    #     # STEP 3C: Extract provider_id
    #     # =====================================================
    #     provider_id = (
    #         profile_data.get("provider_id")
    #         if profile_data
    #         else None
    #     )
    #
    #     # =====================================================
    #     # STEP 3D: Extract email
    #     # =====================================================
    #     candidate_email = None
    #
    #     if profile_data:
    #         emails = (
    #             (profile_data.get("contact_info") or {})
    #             .get("emails")
    #             or []
    #         )
    #
    #         candidate_email = emails[0] if emails else None
    #
    #     print(f"   provider_id: {provider_id}")
    #     print(f"   email: {candidate_email}")
    #
    #     # =====================================================
    #     # STEP 3E: Create meeting scheduling record
    #     # Only after successful InMail
    #     # =====================================================
    #     meeting_record = None
    #
    #     if inmail_success:
    #
    #         try:
    #             if candidate_email:
    #
    #                 meeting_payload = {
    #                     "title": project_name,
    #                     "attendees": [
    #                         {
    #                             "email": candidate_email
    #                         }
    #                     ],
    #                 }
    #
    #                 meeting_record = await asyncio.to_thread(
    #                     add_meeting_record,
    #                     meeting_payload,
    #                 )
    #
    #                 print(
    #                     f"✅ Meeting scheduling record created "
    #                     f"for {full_name}"
    #                 )
    #
    #             else:
    #                 meeting_record = {
    #                     "error": "no email found on candidate profile"
    #                 }
    #
    #                 print(
    #                     f"⚠️ No email found for {full_name}"
    #                 )
    #
    #         except Exception as exc:
    #
    #             meeting_record = {
    #                 "error": str(exc)
    #             }
    #
    #             print(
    #                 f"❌ Meeting record failed for "
    #                 f"{full_name}: {exc}"
    #             )
    #
    #     # =====================================================
    #     # STEP 3F: Send connection invite using provider_id
    #     # (personalized)
    #     # =====================================================
    #     connection_success = False
    #
    #     personalized_connection = None
    #     if connection_message:
    #         personalized_connection = connection_message.replace(
    #             "[Candidate's Name]", first_name
    #         )
    #
    #     try:
    #
    #         if provider_id:
    #
    #             invite_payload = LinkedInInviteRequest(
    #                 account_id=account_id,
    #                 provider_id=provider_id,
    #                 message=personalized_connection,
    #             )
    #
    #             invite_response = await _invite_linkedin_user_raw(
    #                 invite_payload
    #             )
    #
    #             connection_success = (
    #                 invite_response.status_code in (200, 201)
    #             )
    #
    #             if connection_success:
    #                 print(
    #                     f"✅ Connection invite sent to {full_name}"
    #                 )
    #             else:
    #                 print(
    #                     f"❌ Invite failed for {full_name}: "
    #                     f"{invite_response.status_code} "
    #                     f"{invite_response.text}"
    #                 )
    #
    #         else:
    #             print(
    #                 f"❌ Cannot send invite to {full_name}: "
    #                 f"provider_id not found"
    #             )
    #
    #     except Exception as exc:
    #         print(
    #             f"❌ Invite exception for {full_name}: {exc}"
    #         )
    #
    #     # =====================================================
    #     # STEP 3G: Mark outreach
    #     # =====================================================
    #     mark_outreach_sent(
    #         project_id=project_id,
    #         candidate_id=candidate_id,
    #         inmail=inmail_success,
    #         connection=connection_success,
    #         attempted=True,
    #     )
    #
    #     print(
    #         f"→ {full_name}: "
    #         f"inmail={inmail_success}, "
    #         f"connection={connection_success}, "
    #         f"email={candidate_email}, "
    #         f"provider_id={provider_id}"
    #     )
    #
    # print(
    #     f"✅ Outreach pipeline complete for project {project_id} "
    #     f"— {len(candidates)} saved, "
    #     f"{len(top_candidates)} reached out to"
    # )