import asyncio
import httpx
import json

from linkedIn_services.linkedin_recruiter_automation.unipile_apis import _invite_linkedin_user_raw, \
    _create_linkedin_chat_raw, _get_linkedin_user_profile_raw, _safe_json
from models.linkedin_chat import LinkedInChatRequest
from models.linkedin_user_action import LinkedInInviteRequest
from repository.new_automation_pipeline import save_candidates, get_top_candidates, mark_outreach_sent
from repository.schedule_calendar_services import add_meeting_record

UNIPILE_BASE_URL = "https://api40.unipile.com:17060"
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="

# NEW: recruiter *project* endpoints (create project, etc.) live on the
# main management host, not the per-account DSN host above — Unipile
# routes these differently, so this needs its own base URL / key.
UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="

import json
import re


def _loosen_keywords(keyword: str, logic: str) -> str:
    """
    LinkedIn's top-level `keywords` field is always a hard filter —
    there's no priority setting for it like role/skills have. If the
    incoming string is a long AND-chain of multi-word phrases
    (e.g. "GTM Revenue Operations Leader AND Go To Market AND ..."),
    a candidate must match ALL phrases, which is extremely restrictive
    and can easily produce 0 results.

    logic="OR" rewrites " AND " -> " OR " so a candidate matching ANY
    one phrase qualifies instead of requiring all of them.
    logic="AND" leaves the string untouched (strict matching).
    """
    if logic == "OR":
        # Case-insensitive replace of the literal " AND " connector
        return re.sub(r"\s+AND\s+", " OR ", keyword, flags=re.IGNORECASE)
    return keyword



def _format_company_filters(
    companies: list[dict] | None
) -> list[dict]:

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

    Supports:
    - keywords
    - location
    - seniority
    - past company
    - current company

    NOTE on keyword handling — the incoming `keyword` string is split
    on " AND " into individual phrases, then distributed as follows
    (using the example "Python SQL Backend Developer AND Python AND
    SQL AND REST APIs AND Backend Development AND Database
    Optimization", with default max_keyword_phrases=2):

    - `role`: only the 2ND phrase (e.g. "Python"). Unchanged from
      before.
    - `keywords`: the FIRST `max_keyword_phrases` phrases, joined with
      `keyword_logic` (e.g. "Python SQL Backend Developer OR Python").
    - `skills`: the NEXT `max_keyword_phrases` phrases after that
      (e.g. "SQL OR REST APIs") — a genuinely different slice from
      `keywords`, rather than duplicating the same value. If there
      aren't enough phrases left for a distinct skills slice, it
      falls back to the same value as `keywords`.
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

    # ---------------------------------------------------------
    # Split the raw keyword string into its AND-chained phrases.
    # ---------------------------------------------------------
    keyword_parts = re.split(r"\s+AND\s+", keyword.strip(), flags=re.IGNORECASE)
    keyword_parts = [p.strip() for p in keyword_parts if p.strip()]

    connector = " OR " if keyword_logic == "OR" else " AND "

    # ---------------------------------------------------------
    # keywords: first `max_keyword_phrases` phrases
    # ---------------------------------------------------------
    keywords_slice = keyword_parts[:max_keyword_phrases]
    effective_keyword = (
        connector.join(keywords_slice) if keywords_slice else keyword.strip()
    )

    # ---------------------------------------------------------
    # skills: the NEXT `max_keyword_phrases` phrases (a different
    # slice from keywords). Falls back to effective_keyword only if
    # there aren't enough remaining phrases to be distinct.
    # ---------------------------------------------------------
    skills_slice = keyword_parts[max_keyword_phrases: max_keyword_phrases * 2]
    effective_skills = (
        connector.join(skills_slice) if skills_slice else effective_keyword
    )

    # ---------------------------------------------------------
    # role: unchanged — uses only the 2nd AND-chained phrase,
    # falling back to effective_keyword if fewer than 2 phrases.
    # ---------------------------------------------------------
    role_keyword = keyword_parts[1] if len(keyword_parts) >= 2 else effective_keyword

    payload = {
        "api": "recruiter",
        "category": "people",

        "keywords": effective_keyword,

        "locale": "english",
    }

    # ---------------------------------------------------------
    # ROLE — uses only the 2nd keyword phrase (unchanged)
    # ---------------------------------------------------------

    payload["role"] = [
        {
            "is_selection": True,
            "keywords": role_keyword,
            "priority": role_priority,
        }
    ]

    # ---------------------------------------------------------
    # SKILLS — uses a different slice than keywords
    # ---------------------------------------------------------

    payload["skills"] = [
        {
            "keywords": effective_skills,
            "priority": skills_priority,
        }
    ]

    # ---------------------------------------------------------
    # LOCATION
    # ---------------------------------------------------------

    if location:

        formatted_location = [
            {
                "id": str(loc["id"])
            }
            for loc in location
            if loc.get("id")
        ]

        if formatted_location:
            payload["location"] = formatted_location

    # ---------------------------------------------------------
    # SENIORITY
    # ---------------------------------------------------------

    if seniority:

        cleaned_seniority = {}

        if seniority.get("include"):
            cleaned_seniority["include"] = (
                seniority["include"]
            )

        if seniority.get("exclude"):
            cleaned_seniority["exclude"] = (
                seniority["exclude"]
            )

        if cleaned_seniority:
            payload["seniority"] = cleaned_seniority

    # ---------------------------------------------------------
    # PAST COMPANY
    # ---------------------------------------------------------

    formatted_past_company = _format_company_filters(
        past_company
    )

    if formatted_past_company:
        payload["past_company"] = formatted_past_company

    # ---------------------------------------------------------
    # CURRENT COMPANY
    # ---------------------------------------------------------

    formatted_current_company = _format_company_filters(
        current_company
    )

    if formatted_current_company:
        payload["current_company"] = formatted_current_company

    # ---------------------------------------------------------
    # REQUEST
    # ---------------------------------------------------------

    try:

        async with httpx.AsyncClient(
                timeout=120.0
        ) as client:

            print("Sending LinkedIn Recruiter request...")

            print("URL:", url)
            print("Params:", params)
            print(
                "Payload:",
                json.dumps(
                    payload,
                    indent=2
                )
            )

            response = await client.post(
                url,
                params=params,
                headers=headers,
                json=payload,
            )

            print(
                "Status:",
                response.status_code
            )

            print(
                "Response:",
                response.text
            )

            response.raise_for_status()

            return response.json()

    except httpx.ReadTimeout:

        print(
            "❌ Unipile request timed out "
            "after 120 seconds."
        )

        return None

    except httpx.HTTPStatusError as e:

        print(
            "❌ HTTP Error:",
            e.response.status_code
        )

        try:

            error_body = e.response.json()

            with open(
                    "unipile_error.json",
                    "w",
                    encoding="utf-8"
            ) as f:

                json.dump(
                    error_body,
                    f,
                    indent=2
                )

            print(
                "❌ Full error written "
                "to unipile_error.json"
            )

            detail = error_body.get(
                "detail",
                ""
            )

            idx = detail.find(
                '"title":"Recruiter - People"'
            )

            if idx != -1:

                print(
                    "---- Recruiter - People schema ----"
                )

                print(
                    detail[
                    max(0, idx - 5):
                    idx + 4000
                    ]
                )

            else:

                print(
                    "⚠️ Could not locate "
                    "'Recruiter - People' section."
                )

                print(detail[:2000])

        except Exception as parse_exc:

            print(
                "⚠️ Could not parse error body:",
                parse_exc
            )

            print(
                "Raw response:",
                e.response.text
            )

        return None

    except httpx.RequestError as e:

        print(
            "❌ Request Error:",
            str(e)
        )

        return None


# ── Unipile recruiter project helpers (NEW) ─────────────────────────────────

async def create_unipile_recruiter_project(
    account_id: str,
    project_name: str,
    visibility: str = "PRIVATE",
) -> dict | None:
    """
    POST /v2/{account_id}/linkedin/recruiter/projects

    Creates a Unipile recruiter project named `project_name` and returns
    the parsed JSON response (e.g. {"object": "ProjectCreated",
    "project_id": "1539201617"}), or None on failure.
    """

    url = f"{UNIPILE_PROJECTS_BASE_URL}/{account_id}/linkedin/recruiter/projects"

    headers = {
        "X-API-KEY": UNIPILE_PROJECTS_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

    payload = {
        "visibility": visibility,
        "name": project_name,
    }

    try:

        async with httpx.AsyncClient(timeout=30.0) as client:

            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

            if response.status_code in (200, 201):
                return response.json()

            print(
                f"❌ Unipile project creation failed: "
                f"{response.status_code} {response.text}"
            )

            return None

    except httpx.RequestError as e:
        print(f"❌ Unipile project creation request error: {e}")
        return None


async def add_candidate_to_unipile_pipeline(
    account_id: str,
    hiring_project_id: str,
    public_identifier: str,
    stage: str = "UNCONTACTED",
) -> bool:
    """
    POST /api/v1/linkedin/user/{public_identifier}
    action=addCandidateToPipeline

    Adds a candidate to the given Unipile recruiter project's pipeline
    at `stage` ("UNCONTACTED" or "CONTACTED"). Returns True on success.
    """

    url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/user/{public_identifier}"

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

            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )

            if response.status_code in (200, 201):
                return True

            print(
                f"❌ Add-to-pipeline failed for {public_identifier}: "
                f"{response.status_code} {response.text}"
            )

            return False

    except httpx.RequestError as e:
        print(f"❌ Add-to-pipeline request error for {public_identifier}: {e}")
        return False


# ── Outreach pipeline ───────────────────────────────────────────────────────

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
    creation are TEMPORARILY DISABLED. This currently only searches,
    saves candidates, and syncs them into the Unipile pipeline — no
    outreach is sent. Re-enable by uncommenting STEP 3 below.

    Unipile setup note — two separate Unipile accounts are in play here:
    - `account_id`: the DSN/action account (search, addCandidateToPipeline)
      — same one already used for search_linkedin_people, on
      UNIPILE_BASE_URL / UNIPILE_API_KEY.
    - `projects_account_id`: the account recruiter *projects* are created
      under, on UNIPILE_PROJECTS_BASE_URL / UNIPILE_PROJECTS_API_KEY.
      Defaulted to the value from your curl example — pass it explicitly
      if that's not actually a fixed/shared value in your setup.
    """

    # ---------------------------------------------------------
    # STEP 1: Search LinkedIn
    # ---------------------------------------------------------
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

    print(
        f"ℹ️ Total matching candidates available on LinkedIn: "
        f"{search_result.get('paging', {}).get('total_count')}"
    )

    # ---------------------------------------------------------
    # STEP 2: Save candidates
    # ---------------------------------------------------------
    save_candidates(project_id, candidates, project_name)

    # ---------------------------------------------------------
    # NEW: Create Unipile recruiter project + sync candidates into
    # its pipeline (project name = `project_name`)
    # ---------------------------------------------------------
    unipile_project_id = None

    project_result = await create_unipile_recruiter_project(
        account_id=projects_account_id,
        project_name=project_name,
    )

    if project_result:
        unipile_project_id = project_result.get("project_id")
        print(
            f"✅ Unipile recruiter project created: "
            f"'{project_name}' (project_id={unipile_project_id})"
        )

    if unipile_project_id:

        added_count = 0

        for candidate in candidates:

            full_name = candidate.get("full_name")
            public_identifier = candidate.get("public_identifier")

            if not public_identifier:
                print(
                    f"⚠️ Skipping pipeline add for {full_name} — "
                    f"no public_identifier found"
                )
                continue

            # Always UNCONTACTED for now since InMail/connection sending
            # (STEP 3) is disabled. Once STEP 3 is re-enabled, pass
            # stage="CONTACTED" from inside that block for candidates
            # whose inmail_success is True.
            success = await add_candidate_to_unipile_pipeline(
                account_id=account_id,
                hiring_project_id=unipile_project_id,
                public_identifier=public_identifier,
                stage="UNCONTACTED",
            )

            if success:
                added_count += 1
                print(f"✅ Added {full_name} to Unipile pipeline")

        print(
            f"ℹ️ {added_count}/{len(candidates)} candidates added to "
            f"Unipile pipeline (project_id={unipile_project_id})"
        )

    else:
        print(
            "⚠️ Skipping pipeline sync — no Unipile project_id available."
        )

    print(
        f"✅ Outreach pipeline complete for project {project_id} "
        f"— {len(candidates)} saved. Outreach (InMail/connection) is "
        f"currently disabled — no messages were sent."
    )

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