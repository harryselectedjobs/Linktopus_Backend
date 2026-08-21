import os
import json
import re
import requests
from typing import Optional


# =============================================================================
# CONFIG
# =============================================================================

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# IMPORTANT:
# Do NOT hard-code real API keys in source code.
# Put them in environment variables.
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"

UNIPILE_BASE_URL = os.environ.get(
    "UNIPILE_BASE_URL",
    "https://api40.unipile.com:17060/api/v1"
)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

UNIPILE_PARAMS_URL = (
    f"{UNIPILE_BASE_URL}/linkedin/search/parameters"
)

UNIPILE_SEARCH_URL = (
    f"{UNIPILE_BASE_URL}/linkedin/search"
)

# Recruiter / Sales Navigator can return up to 100 results per request.
SEARCH_LIMIT = 100

# Maximum number of skills we allow GPT to generate.
MAX_SKILLS = 4

# Maximum number of role/title alternatives.
MAX_ROLE_TERMS = 5

# Maximum number of companies.
MAX_COMPANIES = 5

# Maximum number of locations.
MAX_LOCATIONS = 5


# =============================================================================
# OPENAI PROMPT
# =============================================================================
#
# This prompt deliberately teaches GPT the SEARCH STRATEGY rather than simply
# asking it to extract every possible filter from the JD.
#
# The code below ALSO enforces these rules, so we don't depend only on GPT.
# =============================================================================

EXTRACTION_SYSTEM_PROMPT = """
You are an expert LinkedIn Recruiter search strategist.

Your job is NOT to create the most restrictive search.

Your job is to create a SEARCHABLE candidate pool that is broad enough to
avoid returning zero candidates while still being relevant to the job.

You are generating parameters for the Unipile LinkedIn Recruiter API.

IMPORTANT SEARCH PHILOSOPHY
============================

LinkedIn Recruiter searches should behave similarly to a recruiter manually
using LinkedIn Recruiter.

Do NOT try to encode every requirement from the job description as a hard
filter.

A job description can contain many technologies, responsibilities,
qualifications and preferred companies. Most of those should NOT become
MUST_HAVE filters.

The search should prioritize:

1. Job title / role similarity
2. Relevant location
3. Optional relevant companies
4. A SMALL number of relevant skills

The goal is a useful candidate pool, not a mathematically perfect filter.

STRICT RULES
============

RULE 1 - NEVER USE SENIORITY
----------------------------

Do not return seniority.

The application will completely ignore seniority.

RULE 2 - ROLE/TITLE
-------------------

Extract the most useful job-title alternatives.

Use simple OR expressions.

GOOD:

"Python Developer" OR "Python Engineer" OR "Backend Developer"
OR "Software Engineer"

BAD:

"Python Developer" AND "FastAPI" AND "AWS" AND "SQL"

BAD:

"Python Developer" AND "Backend Developer"

The role keyword should describe JOB TITLES / ROLES.

Do not put companies into role keywords.

Do not put long technical requirements into role keywords.

Maximum 5 role alternatives.

RULE 3 - SKILLS
----------------

Only select the most important technical skills.

Maximum 4 skills.

Do NOT include every technology from the JD.

Do NOT create a huge AND expression.

Prefer simple OR.

Example:

Python OR FastAPI OR Django

Do not produce:

Python AND FastAPI AND Django AND AWS AND Docker AND PostgreSQL
AND Redis AND Kubernetes

Skills are optional supporting signals.

They must NEVER be treated as hard requirements by this system.

RULE 4 - COMPANY
-----------------

If the JD explicitly says candidates should have experience at certain
companies, extract those companies separately.

Never place company names inside role/title keywords.

Companies are optional signals.

The application will use CAN_HAVE for companies.

Do not invent companies.

Do not add dozens of similar companies.

Maximum 5 companies.

RULE 5 - LOCATION
-----------------

Extract explicit hiring locations.

Location is useful, but should not be unnecessarily restrictive.

Maximum 5 locations.

RULE 6 - EMPLOYMENT TYPE
------------------------

Only return employment_type when the JD explicitly specifies it.

Do not automatically assume FULL_TIME.

If employment type is not explicitly specified, return [].

RULE 7 - AND / OR
-----------------

Avoid complicated boolean expressions.

Use OR for equivalent titles.

Use OR for closely related skills.

Do not use AND unless it is absolutely necessary.

In general, a simple OR query is preferable to a strict AND query.

RULE 8 - DO NOT OVERFIT
-----------------------

Do not turn every sentence of the JD into a search filter.

For example, if a JD says:

"Experience with Python, FastAPI, AWS, Docker, PostgreSQL, Redis,
microservices, CI/CD and Kubernetes."

Do NOT create a search requiring all of these.

Instead:

role:
Python Developer OR Python Engineer OR Backend Developer

skills:
Python OR FastAPI OR AWS

The remaining technologies can be used later when evaluating candidates.

RULE 9 - COMPANY + TITLE
------------------------

If the JD says:

"Python Developer who has worked at Microsoft, Google or Amazon"

Return:

role_keywords:
Python Developer OR Python Engineer OR Backend Developer

preferred_companies:
Microsoft
Google
Amazon

NEVER:

"Microsoft Python Developer"

NEVER:

"Google Python Developer"

NEVER:

"Amazon Python Developer"

RULE 10 - SEARCHABILITY
----------------------

When uncertain between a broad searchable query and a strict query,
ALWAYS choose the broader searchable query.

The application contains additional code that progressively relaxes the
search if the initial search returns zero candidates.

OUTPUT
======

Return ONLY valid JSON.

Use exactly this schema:

{
  "role_keywords": "...",
  "skills_keywords": "...",
  "locations": [],
  "preferred_companies": [],
  "employment_type": []
}

Do not return seniority.

Do not return additional fields.

Keep role_keywords concise.

Keep skills_keywords concise.

Do not create complicated boolean expressions.
"""


# =============================================================================
# OPENAI EXTRACTION
# =============================================================================

def extract_search_params(jd_text: str) -> dict:
    """
    Convert a JD into broad LinkedIn Recruiter search parameters.
    """

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": EXTRACTION_SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": jd_text
            }
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_object"
        }
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        OPENAI_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]

    result = json.loads(content)

    return result


# =============================================================================
# STRING HELPERS
# =============================================================================

def normalize_text(value: str) -> str:
    """
    Normalize text for comparison.
    """
    if not value:
        return ""

    value = str(value).lower().strip()

    value = re.sub(r"\s+", " ", value)

    return value


def clean_boolean_keywords(value: str) -> str:
    """
    Clean GPT generated boolean keywords.

    We intentionally keep boolean logic simple.

    We allow OR.

    We remove excessive AND usage because AND is one of the biggest
    reasons a search becomes unnecessarily restrictive.
    """

    if not value:
        return ""

    value = str(value).strip()

    # Remove newlines
    value = value.replace("\n", " ")

    # Normalize spaces
    value = re.sub(r"\s+", " ", value)

    # Convert lowercase boolean operators to uppercase
    value = re.sub(r"\s+or\s+", " OR ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+and\s+", " AND ", value, flags=re.IGNORECASE)

    # Remove excessive AND chains.
    #
    # Example:
    #
    # Python AND FastAPI AND AWS AND Docker
    #
    # becomes:
    #
    # Python OR FastAPI OR AWS OR Docker
    #
    # This is intentionally conservative.
    and_count = len(
        re.findall(r"\bAND\b", value, flags=re.IGNORECASE)
    )

    if and_count >= 2:
        parts = re.split(
            r"\s+AND\s+",
            value,
            flags=re.IGNORECASE
        )

        parts = [
            p.strip()
            for p in parts
            if p.strip()
        ]

        value = " OR ".join(parts)

    return value


def split_boolean_terms(value: str) -> list[str]:
    """
    Convert a boolean expression into individual terms.

    Used for company-name sanitization.
    """

    if not value:
        return []

    parts = re.split(
        r"\s+(?:OR|AND)\s+",
        value,
        flags=re.IGNORECASE
    )

    cleaned = []

    for part in parts:

        part = part.strip()

        part = part.strip('"')

        if part:
            cleaned.append(part)

    return cleaned


# =============================================================================
# COMPANY SANITIZATION
# =============================================================================

def remove_companies_from_keywords(
    keywords: str,
    companies: list
) -> str:
    """
    Defensive code-level protection.

    If GPT accidentally puts company names inside role_keywords or
    skills_keywords, remove those company names.

    This does NOT depend on GPT obeying the prompt.
    """

    if not keywords:
        return ""

    if not companies:
        return keywords

    result = keywords

    for company in companies:

        if not company:
            continue

        company = str(company).strip()

        if not company:
            continue

        # Case-insensitive removal
        pattern = re.compile(
            re.escape(company),
            re.IGNORECASE
        )

        result = pattern.sub("", result)

    # Clean broken boolean expressions after removal
    result = re.sub(
        r"\s+(OR|AND)\s+(OR|AND)\s+",
        " OR ",
        result,
        flags=re.IGNORECASE
    )

    result = re.sub(
        r"^(OR|AND)\s+",
        "",
        result,
        flags=re.IGNORECASE
    )

    result = re.sub(
        r"\s+(OR|AND)$",
        "",
        result,
        flags=re.IGNORECASE
    )

    result = re.sub(r"\s+", " ", result).strip()

    return result


# =============================================================================
# LIMIT TERMS
# =============================================================================

def limit_boolean_terms(
    keywords: str,
    maximum: int
) -> str:
    """
    Limit the number of OR terms.

    This prevents GPT from creating giant search expressions.
    """

    if not keywords:
        return ""

    terms = split_boolean_terms(keywords)

    if not terms:
        return ""

    terms = terms[:maximum]

    return " OR ".join(
        f'"{term}"' if " " in term else term
        for term in terms
    )


# =============================================================================
# LIST SANITIZATION
# =============================================================================

def clean_string_list(
    values,
    maximum: int
) -> list[str]:

    if not isinstance(values, list):
        return []

    result = []

    seen = set()

    for value in values:

        if not value:
            continue

        value = str(value).strip()

        if not value:
            continue

        normalized = normalize_text(value)

        if normalized in seen:
            continue

        seen.add(normalized)

        result.append(value)

        if len(result) >= maximum:
            break

    return result


# =============================================================================
# SANITIZE GPT OUTPUT
# =============================================================================

def sanitize_extracted_params(
    extracted: dict
) -> dict:
    """
    Apply deterministic rules after GPT extraction.
    """

    if not isinstance(extracted, dict):
        extracted = {}

    companies = clean_string_list(
        extracted.get("preferred_companies", []),
        MAX_COMPANIES
    )

    locations = clean_string_list(
        extracted.get("locations", []),
        MAX_LOCATIONS
    )

    role_keywords = clean_boolean_keywords(
        extracted.get("role_keywords", "")
    )

    skills_keywords = clean_boolean_keywords(
        extracted.get("skills_keywords", "")
    )

    # ---------------------------------------------------------
    # Remove companies from role and skills
    # ---------------------------------------------------------

    role_keywords = remove_companies_from_keywords(
        role_keywords,
        companies
    )

    skills_keywords = remove_companies_from_keywords(
        skills_keywords,
        companies
    )

    # ---------------------------------------------------------
    # Limit number of title alternatives
    # ---------------------------------------------------------

    role_keywords = limit_boolean_terms(
        role_keywords,
        MAX_ROLE_TERMS
    )

    # ---------------------------------------------------------
    # Limit number of skill alternatives
    # ---------------------------------------------------------

    skills_keywords = limit_boolean_terms(
        skills_keywords,
        MAX_SKILLS
    )

    # ---------------------------------------------------------
    # Employment type
    #
    # Only keep it if GPT explicitly returned it.
    # ---------------------------------------------------------

    employment_type = clean_string_list(
        extracted.get("employment_type", []),
        5
    )

    return {
        "role_keywords": role_keywords,
        "skills_keywords": skills_keywords,
        "locations": locations,
        "preferred_companies": companies,
        "employment_type": employment_type
    }


# =============================================================================
# UNIPILE SEARCH PARAMETER RESOLUTION
# =============================================================================

def resolve_id(
    keyword: str,
    param_type: str
) -> Optional[tuple[str, str]]:
    """
    Resolve a LinkedIn Recruiter search parameter to its ID.

    We request up to 100 possible matches instead of blindly relying on
    an arbitrary first result.

    The best textual match is selected.
    """

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json"
    }

    params = {
        "keywords": keyword,
        "type": param_type,
        "service": "RECRUITER",
        "account_id": UNIPILE_ACCOUNT_ID,
        "limit": 100
    }

    response = requests.get(
        UNIPILE_PARAMS_URL,
        headers=headers,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    items = response.json().get("items", [])

    if not items:
        return None

    target = normalize_text(keyword)

    # ---------------------------------------------------------
    # Exact title/name match first
    # ---------------------------------------------------------

    for item in items:

        title = item.get("title", "")

        if normalize_text(title) == target:

            return (
                str(item["id"]),
                title
            )

    # ---------------------------------------------------------
    # Partial match second
    # ---------------------------------------------------------

    for item in items:

        title = item.get("title", "")

        title_normalized = normalize_text(title)

        if target in title_normalized or title_normalized in target:

            return (
                str(item["id"]),
                title
            )

    # ---------------------------------------------------------
    # Fallback to first result
    # ---------------------------------------------------------

    first = items[0]

    return (
        str(first["id"]),
        first.get("title", "")
    )


# =============================================================================
# RESOLVE LOCATIONS
# =============================================================================

def resolve_locations(
    locations: list
) -> list:

    location_objects = []

    for location_name in locations:

        resolved = resolve_id(
            location_name,
            "LOCATION"
        )

        if not resolved:

            print(
                f"WARNING: Could not resolve location: "
                f"{location_name}"
            )

            continue

        location_id, location_title = resolved

        location_objects.append(
            {
                "id": location_id,
                "priority": "CAN_HAVE"
            }
        )

        print(
            f"Location: {location_name} "
            f"-> {location_title} ({location_id})"
        )

    return location_objects


# =============================================================================
# RESOLVE COMPANIES
# =============================================================================

def resolve_companies(
    companies: list
) -> list:

    company_objects = []

    for company_name in companies:

        resolved = resolve_id(
            company_name,
            "COMPANY"
        )

        if not resolved:

            print(
                f"WARNING: Could not resolve company: "
                f"{company_name}"
            )

            continue

        company_id, company_title = resolved

        company_objects.append(
            {
                "id": company_id,
                "priority": "CAN_HAVE"
            }
        )

        print(
            f"Company: {company_name} "
            f"-> {company_title} ({company_id})"
        )

    return company_objects


# =============================================================================
# BUILD BASE PAYLOAD
# =============================================================================

def build_payload(
    extracted: dict
) -> dict:

    extracted = sanitize_extracted_params(
        extracted
    )

    print("\nSanitized extraction:")
    print(
        json.dumps(
            extracted,
            indent=2
        )
    )

    # ---------------------------------------------------------
    # Resolve locations
    # ---------------------------------------------------------

    location_objects = resolve_locations(
        extracted.get("locations", [])
    )

    # ---------------------------------------------------------
    # Resolve companies
    # ---------------------------------------------------------

    company_objects = resolve_companies(
        extracted.get("preferred_companies", [])
    )

    # ---------------------------------------------------------
    # Role
    #
    # IMPORTANT:
    #
    # CAN_HAVE instead of MUST_HAVE.
    #
    # This is intentional.
    # ---------------------------------------------------------

    role_keywords = extracted.get(
        "role_keywords",
        ""
    )

    role_objects = []

    if role_keywords:

        role_objects.append(
            {
                "keywords": role_keywords,
                "priority": "CAN_HAVE",
                "scope": "CURRENT_OR_PAST"
            }
        )

    # ---------------------------------------------------------
    # Skills
    #
    # Also CAN_HAVE.
    # ---------------------------------------------------------

    skills_keywords = extracted.get(
        "skills_keywords",
        ""
    )

    skills_objects = []

    if skills_keywords:

        skills_objects.append(
            {
                "keywords": skills_keywords,
                "priority": "CAN_HAVE"
            }
        )

    # ---------------------------------------------------------
    # Base payload
    #
    # Notice:
    #
    # NO top-level "keywords"
    # NO seniority
    # NO automatic employment_type
    # ---------------------------------------------------------

    payload = {
        "api": "recruiter",
        "category": "people"
    }

    if role_objects:

        payload["role"] = role_objects

    if skills_objects:

        payload["skills"] = skills_objects

    if location_objects:

        payload["location"] = location_objects

    if company_objects:

        payload["current_company"] = company_objects

        payload["past_company"] = company_objects

    # Only include employment type when explicitly provided.
    employment_type = extracted.get(
        "employment_type",
        []
    )

    if employment_type:

        payload["employment_type"] = employment_type

    return payload


# =============================================================================
# PAYLOAD RELAXATION
# =============================================================================

def create_relaxed_payloads(
    base_payload: dict
) -> list[tuple[str, dict]]:
    """
    Create progressively broader searches.

    This is the important safety mechanism.

    Instead of accepting:

        0 candidates

    we progressively remove optional restrictions.

    Search sequence:

        1. Role + location + company + skills
        2. Role + location + company
        3. Role + location
        4. Role only
        5. Broad role alternatives

    Every step remains relevant to the job.
    """

    payloads = []

    # -------------------------------------------------------------------------
    # SEARCH 1
    # Original broad search
    # -------------------------------------------------------------------------

    payloads.append(
        (
            "BASE",
            json.loads(
                json.dumps(base_payload)
            )
        )
    )

    # -------------------------------------------------------------------------
    # SEARCH 2
    # Remove skills
    # -------------------------------------------------------------------------

    p2 = json.loads(
        json.dumps(base_payload)
    )

    p2.pop(
        "skills",
        None
    )

    payloads.append(
        (
            "WITHOUT_SKILLS",
            p2
        )
    )

    # -------------------------------------------------------------------------
    # SEARCH 3
    # Remove companies
    # -------------------------------------------------------------------------

    p3 = json.loads(
        json.dumps(p2)
    )

    p3.pop(
        "current_company",
        None
    )

    p3.pop(
        "past_company",
        None
    )

    payloads.append(
        (
            "WITHOUT_SKILLS_AND_COMPANIES",
            p3
        )
    )

    # -------------------------------------------------------------------------
    # SEARCH 4
    # Remove location
    # -------------------------------------------------------------------------

    p4 = json.loads(
        json.dumps(p3)
    )

    p4.pop(
        "location",
        None
    )

    payloads.append(
        (
            "ROLE_ONLY",
            p4
        )
    )

    # -------------------------------------------------------------------------
    # SEARCH 5
    #
    # Keep only role but simplify the role expression.
    # -------------------------------------------------------------------------

    p5 = json.loads(
        json.dumps(p4)
    )

    if "role" in p5:

        role = p5["role"]

        if role:

            original_keywords = role[0].get(
                "keywords",
                ""
            )

            terms = split_boolean_terms(
                original_keywords
            )

            # Keep only the first 3 broadest title alternatives.
            terms = terms[:3]

            if terms:

                role[0]["keywords"] = (
                    " OR ".join(
                        terms
                    )
                )

    payloads.append(
        (
            "SIMPLIFIED_ROLE",
            p5
        )
    )

    return payloads


# =============================================================================
# RUN ONE UNIPILE SEARCH
# =============================================================================

def run_search(
    payload: dict,
    limit: int = SEARCH_LIMIT
) -> dict:
    """
    Execute one Recruiter search.
    """

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json"
    }

    params = {
        "account_id": UNIPILE_ACCOUNT_ID,
        "limit": limit
    }

    response = requests.post(
        UNIPILE_SEARCH_URL,
        headers=headers,
        params=params,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    return response.json()


# =============================================================================
# EXTRACT RESULT COUNT
# =============================================================================

def get_result_count(
    result: dict
) -> int:
    """
    Safely determine the number of returned candidates.
    """

    # Common possibilities
    for key in [
        "items",
        "results",
        "data"
    ]:

        value = result.get(key)

        if isinstance(value, list):

            return len(value)

    # Some Unipile responses expose paging information.
    paging = result.get(
        "paging",
        {}
    )

    if isinstance(paging, dict):

        total_count = paging.get(
            "total_count"
        )

        if isinstance(total_count, int):

            return total_count

    return 0


# =============================================================================
# MAIN SEARCH WITH AUTOMATIC RELAXATION
# =============================================================================

def search_with_fallbacks(
    base_payload: dict
) -> dict:
    """
    Run progressively relaxed searches until useful results are found.

    This prevents an overly strict payload from permanently producing
    zero candidates.
    """

    payload_variations = create_relaxed_payloads(
        base_payload
    )

    last_result = {}

    for search_name, payload in payload_variations:

        print("\n" + "=" * 80)
        print(
            f"RUNNING SEARCH: {search_name}"
        )
        print("=" * 80)

        print(
            json.dumps(
                payload,
                indent=2
            )
        )

        try:

            result = run_search(
                payload,
                limit=SEARCH_LIMIT
            )

        except requests.HTTPError as exc:

            print(
                f"Search failed for {search_name}: "
                f"{exc}"
            )

            continue

        last_result = result

        count = get_result_count(
            result
        )

        print(
            f"Returned candidates: {count}"
        )

        # -------------------------------------------------------------
        # SUCCESS
        # -------------------------------------------------------------

        if count > 0:

            print(
                f"\nSUCCESS: {count} candidates "
                f"found using {search_name}"
            )

            return {
                "search_strategy": search_name,
                "payload": payload,
                "result": result
            }

        print(
            f"No candidates found using "
            f"{search_name}. Relaxing search..."
        )

    # -----------------------------------------------------------------
    # All searches returned zero.
    #
    # We return the final result rather than inventing candidates.
    # -----------------------------------------------------------------

    print(
        "\nWARNING: All fallback searches "
        "returned zero candidates."
    )

    return {
        "search_strategy": "ALL_SEARCHES_ZERO",
        "payload": payload_variations[-1][1],
        "result": last_result
    }


# =============================================================================
# COMPLETE PIPELINE
# =============================================================================

def run_pipeline_v2(
    jd_text: str
) -> dict:

    # -------------------------------------------------------------------------
    # STEP 1
    # -------------------------------------------------------------------------

    print(
        "\n"
        + "=" * 80
    )

    print(
        "STEP 1: Extracting broad search parameters"
    )

    print(
        "=" * 80
    )

    extracted = extract_search_params(
        jd_text
    )

    print(
        json.dumps(
            extracted,
            indent=2
        )
    )

    # -------------------------------------------------------------------------
    # STEP 2
    # -------------------------------------------------------------------------

    print(
        "\n"
        + "=" * 80
    )

    print(
        "STEP 2: Building safe Recruiter payload"
    )

    print(
        "=" * 80
    )

    payload = build_payload(
        extracted
    )

    print(
        "\nFINAL BASE PAYLOAD:"
    )

    print(
        json.dumps(
            payload,
            indent=2
        )
    )

    # -------------------------------------------------------------------------
    # STEP 3
    # -------------------------------------------------------------------------

    print(
        "\n"
        + "=" * 80
    )

    print(
        "STEP 3: Running Recruiter search with "
        "automatic relaxation"
    )

    print(
        "=" * 80
    )

    result = search_with_fallbacks(
        payload
    )

    return result


# =============================================================================
# OPTIONAL TEST
# =============================================================================

if __name__ == "__main__":

    jd = """
    We are looking for a Python Backend Developer with 4-6 years
    of experience.

    The candidate should have experience with Python, FastAPI,
    Django, PostgreSQL, AWS and Docker.

    Experience working at companies such as Microsoft, Google,
    Amazon or similar technology companies is preferred.

    Location: New York, San Francisco or Seattle.

    The candidate should have strong backend development experience
    and experience building scalable APIs and microservices.
    """

    result = run_pipeline_v2(
        jd
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "FINAL RESULT"
    )

    print(
        "=" * 80
    )

    print(
        json.dumps(
            result,
            indent=2
        )
    )