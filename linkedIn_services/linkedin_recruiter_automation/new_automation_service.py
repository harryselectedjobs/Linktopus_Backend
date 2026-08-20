import asyncio
import httpx
import json
import re

from linkedIn_services.linkedin_recruiter_automation.unipile_apis import _invite_linkedin_user_raw, \
    _create_linkedin_chat_raw, _get_linkedin_user_profile_raw, _safe_json
from models.linkedin_chat import LinkedInChatRequest
from models.linkedin_user_action import LinkedInInviteRequest
from repository.new_automation_pipeline import save_candidates, get_top_candidates, mark_outreach_sent
from repository.schedule_calendar_services import add_meeting_record

UNIPILE_BASE_URL = "https://api40.unipile.com:17060"
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="

# recruiter *project* endpoints (create project, etc.) live on the
# main management host, not the per-account DSN host above — Unipile
# routes these differently, so this needs its own base URL / key.
UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="


def _loosen_keywords(keyword: str, logic: str) -> str:
    """
    LinkedIn's top-level `keywords` field is always a hard filter —
    there's no priority setting for it like role/skills have. If the
    incoming string is a long AND-chain of multi-word phrases,
    a candidate must match ALL phrases, which is extremely restrictive
    and can easily produce 0 results.

    logic="OR" rewrites " AND " -> " OR " so a candidate matching ANY
    one phrase qualifies instead of requiring all of them.
    logic="AND" leaves the string untouched (strict matching).
    """
    if logic == "OR":
        return re.sub(r"\s+AND\s+", " OR ", keyword, flags=re.IGNORECASE)
    return keyword


def _format_company_filters(companies: list[dict] | None) -> list[dict]:
    if not companies:
        return []

    return [
        {
            "id": str(company["id"]),
            "priority": company.get("priority", "CAN_HAVE")
        }
        for company in companies
        if company.get("id")
    ]


async def search_linkedin_people(
        account_id: str,
        keyword: str,
        limit: int = 1,
        location: list[dict] | None = None,
        seniority: dict | None = None,
        past_company: list[dict] | None = None,
        current_company: list[dict] | None = None,
        role_priority: str = "CAN_HAVE",
        skills_priority: str = "CAN_HAVE",
        keyword_logic: str = "OR",
        max_keyword_phrases: int = 2,
):
    """
    Search LinkedIn Recruiter people.

    NOTE on keyword handling — the incoming `keyword` string is split
    on " AND " into individual phrases, then distributed as follows
    (default max_keyword_phrases=2):

    - `role`: only the 2ND phrase. Unchanged from before.
    - `keywords`: the FIRST `max_keyword_phrases` phrases, joined with
      `keyword_logic`.
    - `skills`: the NEXT `max_keyword_phrases` phrases after that —
      a genuinely different slice from `keywords`. Falls back to the
      same value as `keywords` if there aren't enough phrases left.
    """

    url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search"

    params = {
        "limit": limit,
        "account_id": account_id,
    }

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

    keyword_parts = re.split(r"\s+AND\s+", keyword.strip(), flags=re.IGNORECASE)
    keyword_parts = [p.strip() for p in keyword_parts if p.strip()]

    connector = " OR " if keyword_logic == "OR" else " AND "

    keywords_slice = keyword_parts[:max_keyword_phrases]
    effective_keyword = (
        connector.join(keywords_slice) if keywords_slice else keyword.strip()
    )

    skills_slice = keyword_parts[max_keyword_phrases: max_keyword_phrases * 2]
    effective_skills = (
        connector.join(skills_slice) if skills_slice else effective_keyword
    )

    role_keyword = keyword_parts[1] if len(keyword_parts) >= 2 else effective_keyword

    payload = {
        "api": "recruiter",
        "category": "people",
        "keywords": effective_keyword,
        "locale": "english",
    }

    payload["role"] = [
        {
            "is_selection": True,
            "keywords": role_keyword,
            "priority": role_priority,
        }
    ]

    payload["skills"] = [
        {
            "keywords": effective_skills,
            "priority": skills_priority,
        }
    ]

    if location:
        formatted_location = [{"id": str(loc["id"])} for loc in location if loc.get("id")]
        if formatted_location:
            payload["location"] = formatted_location

    if seniority:
        cleaned_seniority = {}
        if seniority.get("include"):
            cleaned_seniority["include"] = seniority["include"]
        if seniority.get("exclude"):
            cleaned_seniority["exclude"] = seniority["exclude"]
        if cleaned_seniority:
            payload["seniority"] = cleaned_seniority

    formatted_past_company = _format_company_filters(past_company)
    if formatted_past_company:
        payload["past_company"] = formatted_past_company

    formatted_current_company = _format_company_filters(current_company)
    if formatted_current_company:
        payload["current_company"] = formatted_current_company

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, params=params, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    except httpx.ReadTimeout:
        print("❌ Unipile search request timed out after 120s.")
        return None

    except httpx.HTTPStatusError as e:
        print(f"❌ Unipile search HTTP error {e.response.status_code}: {e.response.text[:500]}")

        # Full error body still written to disk for deep debugging,
        # just not dumped into stdout/logs by default.
        try:
            error_body = e.response.json()
            with open("unipile_error.json", "w", encoding="utf-8") as f:
                json.dump(error_body, f, indent=2)
        except Exception:
            pass

        return None

    except httpx.RequestError as e:
        print(f"❌ Unipile search request error: {e}")
        return None


# ── Unipile recruiter project helpers ───────────────────────────────────────

async def create_unipile_recruiter_project(
    account_id: str,
    project_name: str,
    visibility: str = "PRIVATE",
) -> dict | None:
    """
    POST /v2/{account_id}/linkedin/recruiter/projects
    Returns the parsed JSON response, or None on failure.
    """

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
    """
    POST /api/v1/linkedin/user/{candidate_linkedin_id}
    action=addCandidateToPipeline

    `candidate_linkedin_id` must be the search result's `id` field —
    NOT `public_identifier` (the vanity URL slug).
    """

    url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/user/{candidate_linkedin_id}"

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

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
    keyword: str,
    inmail_message: str,
    connection_message: str = None,
    limit: int = 100,
    location: list[dict] | None = None,
    seniority: dict | None = None,
    past_company: list[dict] | None = None,
    current_company: list[dict] | None = None,
    projects_account_id: str = "acc_01m09sdddhfetrdm9tzcbqncv1",
):
    """
    1. Searches LinkedIn people (up to `limit`, optionally filtered by
       `location` / `seniority`)
    2. Saves all candidates to DynamoDB
    3. Creates a Unipile recruiter project named `project_name` and adds
       every saved candidate to that project's pipeline

    NOTE: InMail sending, connection invites, and meeting record
    creation are TEMPORARILY DISABLED. Re-enable by uncommenting STEP 3.

    Unipile setup note — two separate Unipile accounts are in play here:
    - `account_id`: the DSN/action account (search, addCandidateToPipeline)
      on UNIPILE_BASE_URL / UNIPILE_API_KEY.
    - `projects_account_id`: the account recruiter *projects* are created
      under, on UNIPILE_PROJECTS_BASE_URL / UNIPILE_PROJECTS_API_KEY.
    """

    # ── STEP 1: Search LinkedIn ──────────────────────────────────────────
    search_result = await search_linkedin_people(
        account_id=account_id,
        keyword=keyword,
        limit=limit,
        location=location,
        seniority=seniority,
        past_company=past_company,
        current_company=current_company,
    )

    if not search_result:
        print("❌ No search results returned — aborting pipeline.")
        return

    candidates = search_result.get("items", [])

    if not candidates:
        print("⚠️ Search returned no candidates.")
        return

    total_available = search_result.get("paging", {}).get("total_count")
    print(f"ℹ️ Search returned {len(candidates)} candidates (total available: {total_available})")

    # Duplicate-id check — only logs when it actually finds a problem,
    # since Unipile's `id` field is also the DynamoDB range key.
    ids = [c.get("id") for c in candidates]
    if len(set(ids)) != len(ids):
        dupes = {i for i in ids if ids.count(i) > 1}
        print(f"⚠️ {len(ids) - len(set(ids))} duplicate candidate id(s) in search response: {dupes}")

    # ── STEP 2: Save candidates ──────────────────────────────────────────
    save_candidates(project_id, candidates, project_name)

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

            # Always UNCONTACTED for now since InMail/connection sending
            # (STEP 3) is disabled. Once re-enabled, pass
            # stage="CONTACTED" from inside that block when inmail_success.
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