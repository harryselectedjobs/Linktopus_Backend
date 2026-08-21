import os
import json
import re
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"
UNIPILE_BASE_URL = os.environ.get("UNIPILE_BASE_URL", "https://api40.unipile.com:17060/api/v1")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

UNIPILE_PARAMS_URL = (
    f"{UNIPILE_BASE_URL}/linkedin/search/parameters"
)

UNIPILE_SEARCH_URL = (
    f"{UNIPILE_BASE_URL}/linkedin/search"
)

# Recruiter / Sales Navigator search maximum
SEARCH_LIMIT = 100
PARAMETER_LIMIT = 100


# ---------------------------------------------------------------------------
# Supported Unipile seniority values
# ---------------------------------------------------------------------------

SENIORITY_ALLOWED = [
    "owner",
    "partner",
    "cxo",
    "vp",
    "director",
    "manager",
    "senior",
    "entry",
    "training",
    "unpaid",
]


# ---------------------------------------------------------------------------
# IMPORTANT:
# Unipile API knowledge is explicitly included in the prompt.
#
# The purpose is NOT to let GPT invent payload structures.
# GPT only extracts candidate-search concepts.
# Python code remains responsible for constructing the actual API payload.
# ---------------------------------------------------------------------------

UNIPILE_API_REFERENCE = """
UNIPILE LINKEDIN SEARCH API REFERENCE

Endpoint:
POST /linkedin/search

The search is performed with:
- api
- category
- keywords
- role
- skills
- location
- current_company
- past_company
- employment_type
- seniority

For this application we use:

{
    "api": "recruiter",
    "category": "people"
}

ROLE FILTER:

"role" is an array of objects:

{
    "keywords": "Developer OR Software Engineer OR Backend Engineer",
    "priority": "MUST_HAVE",
    "scope": "CURRENT_OR_PAST"
}

Important:
- role.keywords is a BOOLEAN keyword expression.
- OR should be preferred when looking for equivalent job titles.
- Do NOT create unnecessarily strict AND expressions.
- MUST_HAVE is restrictive.
- CAN_HAVE is less restrictive.
- CURRENT_OR_PAST means the role can appear in the candidate's current or previous experience.
- Do not combine a company name with a job title inside role.keywords.

GOOD:
"Developer" OR "Software Developer" OR "Software Engineer"

BAD:
"Zoho Developer"

If the JD says:
"Developer who worked at Zoho"

the concepts must remain separate:

role:
"Developer" OR "Software Developer" OR "Software Engineer"

company:
Zoho

COMPANY FILTERS:

current_company and past_company are arrays of objects:

{
    "id": "COMPANY_ID",
    "priority": "CAN_HAVE"
}

Company IDs must come from:
GET /linkedin/search/parameters

with:
type=COMPANY

LOCATION FILTER:

location is an array:

{
    "id": "LOCATION_ID",
    "priority": "CAN_HAVE"
}

Location IDs must come from:
GET /linkedin/search/parameters

with:
type=LOCATION

SKILLS:

skills is an array:

{
    "keywords": "Python OR FastAPI",
    "priority": "CAN_HAVE"
}

Do not put every JD technology into skills.
Only use a small number of core skills.

EMPLOYMENT TYPE:

For a normal permanent employee role:

"employment_type": ["FULL_TIME"]

SEARCH LIMIT:

POST /linkedin/search supports a maximum limit of 100
for Recruiter/Sales Navigator searches.

This application always requests:

limit=100

The limit controls how many results are returned in the response.
It does NOT make LinkedIn find more candidates.
The total number of matching candidates is represented by paging.total_count.

IMPORTANT SEARCH PRINCIPLE:

The goal is to find qualified candidates, NOT to reproduce every word
from the JD as a filter.

Do NOT turn every JD requirement into a MUST_HAVE filter.

A realistic LinkedIn profile may:
- use a different title,
- mention only some skills,
- omit skills,
- have incomplete profile data,
- use an abbreviated job title,
- have company history without matching keywords in the headline.

Therefore broad equivalent titles joined with OR are preferred.

DO NOT INVENT:
- parameter IDs
- company IDs
- location IDs
- unsupported seniority values
- unsupported search fields
- unsupported API syntax

IDs are resolved by Python using the Unipile parameter endpoint.
"""


# ---------------------------------------------------------------------------
# GPT EXTRACTION PROMPT
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = f"""
You are a technical recruiter assistant that converts a job description
into structured LinkedIn Recruiter search concepts.

You are NOT responsible for constructing the final Unipile API payload.
Python code will construct the payload.

You MUST follow the Unipile API reference below.

{UNIPILE_API_REFERENCE}

Return ONLY valid JSON.

The JSON MUST contain exactly these fields:

{{
    "title_keywords": "",
    "role_keywords": "",
    "skills_keywords": "",
    "locations": [],
    "preferred_companies": [],
    "seniority_levels": [],
    "employment_type": ["FULL_TIME"]
}}

============================================================
TITLE EXTRACTION
============================================================

"title_keywords" must contain a SHORT Boolean expression containing
2-4 equivalent job-title variants.

Use OR between equivalent titles.

GOOD:

"Developer" OR "Software Developer" OR "Software Engineer"

GOOD:

"Backend Engineer" OR "Backend Developer" OR "Software Engineer"

BAD:

"Zoho Developer"

BAD:

"Python AND SQL AND Backend Developer"

BAD:

"Python Developer" AND "FastAPI Developer" AND "Backend Engineer"

The title should describe the JOB/ROLE.

The title must NOT contain a company name.

============================================================
COMPANY EXTRACTION
============================================================

"preferred_companies" contains companies ONLY when the JD explicitly
says candidates should have worked at, currently work at, previously worked
at, or should be sourced from those companies.

Example JD:

"Looking for a Developer who has worked at Zoho."

Correct:

"title_keywords":
"Developer" OR "Software Developer" OR "Software Engineer"

"preferred_companies":
["Zoho"]

NEVER create:

"title_keywords":
"Zoho Developer"

Company names belong ONLY in preferred_companies.

Do NOT infer companies merely because they are famous in the industry.

============================================================
ROLE KEYWORDS
============================================================

"role_keywords" should normally be EMPTY.

Use it only when the functional role cannot adequately be represented
by title_keywords.

Do NOT duplicate title_keywords into role_keywords.

If title_keywords already represents the role, leave:

"role_keywords": ""

This is extremely important because duplicating the same concept into
multiple filters can unnecessarily narrow LinkedIn results.

============================================================
SKILLS
============================================================

"skills_keywords" should contain at most 2-3 CORE skills.

Use OR instead of AND.

Example:

"Python" OR "FastAPI" OR "Django"

Do NOT include every technology mentioned in the JD.

Do NOT use a long chain of skills.

Do NOT include company names.

Do NOT put job titles here.

============================================================
LOCATIONS
============================================================

Extract only locations explicitly stated in the JD.

Maximum 3 locations.

Do not invent nearby cities.

Do not infer an entire country if only a city is mentioned.

============================================================
SENIORITY
============================================================

Only extract seniority if the JD explicitly specifies it.

Allowed values:

owner
partner
cxo
vp
director
manager
senior
entry
training
unpaid

Always lowercase.

If seniority is not explicit:

[]

Do not guess seniority from years of experience alone.

============================================================
EMPLOYMENT TYPE
============================================================

Normally return:

["FULL_TIME"]

============================================================
ZERO-RESULT PREVENTION
============================================================

The most important objective is to avoid an unnecessarily restrictive
search.

LinkedIn profiles are inconsistent.

Therefore:

1. Prefer OR over AND.
2. Do not put company names inside titles.
3. Do not duplicate title_keywords and role_keywords.
4. Keep skills short.
5. Do not invent locations.
6. Do not invent companies.
7. Do not guess seniority.
8. Do not turn every JD requirement into a filter.
9. Never create literal phrases such as "Zoho Developer" when Zoho is
   separately represented as a company.
10. Use broad equivalent titles.

Remember:

The search should discover a useful candidate pool first.
Candidate-level qualification can be evaluated after retrieval.

Return ONLY JSON.

"""


# ---------------------------------------------------------------------------
# 1. Extract search parameters from JD
# ---------------------------------------------------------------------------

def extract_search_params(jd_text: str) -> dict:
    """
    Convert a JD into structured LinkedIn search concepts using OpenAI.
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
        "Content-Type": "application/json",
    }

    resp = requests.post(
        OPENAI_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]

    extracted = json.loads(content)

    # Defensive defaults
    extracted.setdefault("title_keywords", "")
    extracted.setdefault("role_keywords", "")
    extracted.setdefault("skills_keywords", "")
    extracted.setdefault("locations", [])
    extracted.setdefault("preferred_companies", [])
    extracted.setdefault("seniority_levels", [])
    extracted.setdefault("employment_type", ["FULL_TIME"])

    return extracted


# ---------------------------------------------------------------------------
# 2. Sanitize company names from title / role keywords
# ---------------------------------------------------------------------------

def sanitize_keyword_fields(extracted: dict) -> dict:
    """
    HARD SAFETY LAYER.

    If GPT produces:

        "Zoho Developer"

    while preferred_companies contains:

        ["Zoho"]

    convert it to:

        "Developer"

    This prevents a company name from becoming part of a literal
    title phrase.

    This function intentionally does not trust GPT completely.
    """

    companies = extracted.get("preferred_companies", []) or []

    if not companies:
        return extracted

    for field in ("title_keywords", "role_keywords"):

        value = extracted.get(field, "") or ""

        if not value:
            continue

        original = value

        for company in companies:

            company = str(company).strip()

            if not company:
                continue

            value = re.sub(
                re.escape(company),
                "",
                value,
                flags=re.IGNORECASE
            )

        # Clean whitespace
        value = re.sub(r"\s+", " ", value).strip()

        # Remove dangling Boolean operators
        value = re.sub(
            r"^(AND|OR)\b\s*",
            "",
            value,
            flags=re.IGNORECASE
        )

        value = re.sub(
            r"\s*\b(AND|OR)$",
            "",
            value,
            flags=re.IGNORECASE
        )

        value = value.strip()

        # Clean quotes left after company removal
        value = value.replace('""', '"')

        if value != original.strip():

            print(
                f"  NOTE: stripped company name(s) from {field}: "
                f"'{original}' -> '{value}'"
            )

        extracted[field] = value

    return extracted


# ---------------------------------------------------------------------------
# 3. Additional normalization
# ---------------------------------------------------------------------------

def normalize_extracted_params(extracted: dict) -> dict:
    """
    Normalize GPT output before payload construction.
    """

    # Ensure lists
    if not isinstance(extracted.get("locations"), list):
        extracted["locations"] = []

    if not isinstance(extracted.get("preferred_companies"), list):
        extracted["preferred_companies"] = []

    if not isinstance(extracted.get("seniority_levels"), list):
        extracted["seniority_levels"] = []

    if not isinstance(extracted.get("employment_type"), list):
        extracted["employment_type"] = ["FULL_TIME"]

    # Remove duplicate companies
    extracted["preferred_companies"] = list(
        dict.fromkeys(
            str(x).strip()
            for x in extracted["preferred_companies"]
            if str(x).strip()
        )
    )

    # Remove duplicate locations
    extracted["locations"] = list(
        dict.fromkeys(
            str(x).strip()
            for x in extracted["locations"]
            if str(x).strip()
        )
    )

    # Only first 3 locations
    extracted["locations"] = extracted["locations"][:3]

    # Sanitize company leakage
    extracted = sanitize_keyword_fields(extracted)

    # If role_keywords is empty, title_keywords will be used.
    # If both exist, prefer role_keywords because it is already the
    # functional role filter.
    return extracted


# ---------------------------------------------------------------------------
# 4. Resolve Unipile parameter IDs
# ---------------------------------------------------------------------------

def resolve_id(keyword: str, param_type: str) -> tuple[str, str] | None:
    """
    Resolve a company/location name to a Unipile ID.

    Uses limit=100 so the parameter endpoint has a larger candidate
    set from which to find an exact match.
    """

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
    }

    params = {
        "keywords": keyword,
        "type": param_type,
        "account_id": UNIPILE_ACCOUNT_ID,
        "limit": PARAMETER_LIMIT,
    }

    resp = requests.get(
        UNIPILE_PARAMS_URL,
        headers=headers,
        params=params,
        timeout=30
    )

    resp.raise_for_status()

    items = resp.json().get("items", [])

    if not items:
        return None

    keyword_clean = keyword.strip().lower()

    # First try exact title
    exact = next(
        (
            item
            for item in items
            if item.get("title", "").strip().lower() == keyword_clean
        ),
        None
    )

    if exact:
        return exact["id"], exact["title"]

    # Then try exact-ish normalized title
    normalized_keyword = re.sub(
        r"[^a-z0-9]+",
        " ",
        keyword_clean
    ).strip()

    normalized_match = next(
        (
            item
            for item in items
            if re.sub(
                r"[^a-z0-9]+",
                " ",
                item.get("title", "").lower()
            ).strip() == normalized_keyword
        ),
        None
    )

    if normalized_match:
        return (
            normalized_match["id"],
            normalized_match["title"]
        )

    # Last resort: first fuzzy result
    chosen = items[0]

    if len(items) > 1:
        print(
            f"  NOTE: no exact match for '{keyword}', "
            f"using top fuzzy result "
            f"'{chosen.get('title')}' out of {len(items)} candidates"
        )

    return chosen["id"], chosen["title"]


# ---------------------------------------------------------------------------
# 5. Sanitize seniority
# ---------------------------------------------------------------------------

def sanitize_seniority(levels: list) -> list:
    """
    Keep only values supported by the application.
    """

    cleaned = []

    for level in levels or []:

        level_lower = str(level).strip().lower()

        if (
            level_lower in SENIORITY_ALLOWED
            and level_lower not in cleaned
        ):
            cleaned.append(level_lower)

        else:
            print(
                f"  WARNING: dropping invalid seniority "
                f"value '{level}'"
            )

    return cleaned


# ---------------------------------------------------------------------------
# 6. Build Unipile search payload
# ---------------------------------------------------------------------------

def build_payload(
    extracted: dict,
    *,
    role_priority: str = "MUST_HAVE",
    include_skills: bool = True,
    include_company: bool = True,
    include_top_keywords: bool = True,
    include_location: bool = True,
    include_seniority: bool = False
) -> dict:
    """
    Build the actual Unipile Recruiter search payload.

    GPT does NOT construct this payload.
    Python constructs it from sanitized concepts.
    """

    # ---------------------------------------------------------
    # Locations
    # ---------------------------------------------------------

    location_objs = []

    if include_location:

        for location_name in extracted.get("locations", []):

            resolved = resolve_id(
                location_name,
                "LOCATION"
            )

            if resolved:

                location_id, location_title = resolved

                location_objs.append(
                    {
                        "id": location_id,
                        "priority": "CAN_HAVE"
                    }
                )

                print(
                    f"  location '{location_name}' "
                    f"-> {location_title} ({location_id})"
                )

            else:

                print(
                    f"  WARNING: no location match "
                    f"for '{location_name}', skipping"
                )

    # ---------------------------------------------------------
    # Companies
    # ---------------------------------------------------------

    company_objs = []

    if include_company:

        for company_name in extracted.get(
            "preferred_companies",
            []
        ):

            resolved = resolve_id(
                company_name,
                "COMPANY"
            )

            if resolved:

                company_id, company_title = resolved

                company_objs.append(
                    {
                        "id": company_id,
                        "priority": "CAN_HAVE"
                    }
                )

                print(
                    f"  company '{company_name}' "
                    f"-> {company_title} ({company_id})"
                )

            else:

                print(
                    f"  WARNING: no company match "
                    f"for '{company_name}', skipping"
                )

    # ---------------------------------------------------------
    # Role
    # ---------------------------------------------------------

    role_keywords = (
        extracted.get("role_keywords")
        or extracted.get("title_keywords")
        or ""
    )

    role_keywords = role_keywords.strip()

    role_block = []

    if role_keywords:

        role_block = [
            {
                "keywords": role_keywords,
                "priority": role_priority,
                "scope": "CURRENT_OR_PAST"
            }
        ]

    # ---------------------------------------------------------
    # Skills
    # ---------------------------------------------------------

    skills_block = []

    skills_keywords = (
        extracted.get("skills_keywords")
        or ""
    ).strip()

    if include_skills and skills_keywords:

        skills_block = [
            {
                "keywords": skills_keywords,
                "priority": "CAN_HAVE"
            }
        ]

    # ---------------------------------------------------------
    # Top-level keywords
    #
    # DO NOT duplicate title keywords if role is already populated.
    # ---------------------------------------------------------

    top_keywords = ""

    if include_top_keywords and not role_block:

        top_keywords = (
            extracted.get("title_keywords")
            or ""
        ).strip()

    # ---------------------------------------------------------
    # Base payload
    # ---------------------------------------------------------

    payload = {
        "api": "recruiter",
        "category": "people",
        "keywords": top_keywords,
        "role": role_block,
        "skills": skills_block,
        "location": location_objs,
        "current_company": company_objs,
        "past_company": company_objs,
        "employment_type": extracted.get(
            "employment_type",
            ["FULL_TIME"]
        ),
    }

    # ---------------------------------------------------------
    # Seniority
    # ---------------------------------------------------------

    if include_seniority:

        seniority_levels = sanitize_seniority(
            extracted.get(
                "seniority_levels",
                []
            )
        )

        if seniority_levels:
            print("")

            # payload["seniority"] = {
            #     "include": seniority_levels
            # }

    # ---------------------------------------------------------
    # Remove empty values
    # ---------------------------------------------------------

    payload = {
        key: value
        for key, value in payload.items()
        if value not in ("", [], None)
    }

    return payload


# ---------------------------------------------------------------------------
# 7. Run Unipile search
# ---------------------------------------------------------------------------

def run_search(
    payload: dict,
    limit: int = SEARCH_LIMIT
) -> dict:
    """
    Execute LinkedIn Recruiter search.

    Recruiter maximum = 100.
    """

    # Never allow caller to exceed documented maximum.
    limit = min(int(limit), SEARCH_LIMIT)

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

    params = {
        "account_id": UNIPILE_ACCOUNT_ID,
        "limit": limit,
    }

    resp = requests.post(
        UNIPILE_SEARCH_URL,
        headers=headers,
        params=params,
        json=payload,
        timeout=60
    )

    resp.raise_for_status()

    return resp.json()


# ---------------------------------------------------------------------------
# 8. Extract result count
# ---------------------------------------------------------------------------

def _result_count(result: dict) -> int:
    """
    paging.total_count is the reliable candidate count.
    """

    return result.get(
        "paging",
        {}
    ).get(
        "total_count",
        len(result.get("items", []))
    )


# ---------------------------------------------------------------------------
# 9. Automatic fallback ladder
# ---------------------------------------------------------------------------

def search_with_fallback(extracted: dict) -> dict:
    """
    Progressively loosen the search when total_count == 0.

    No additional OpenAI calls are made.
    """

    ladder = [

        # -----------------------------------------------------
        # Attempt 1
        # -----------------------------------------------------

        (
            "full payload (role=MUST_HAVE)",
            {
                "role_priority": "MUST_HAVE"
            }
        ),

        # -----------------------------------------------------
        # Attempt 2
        # Remove skills
        # -----------------------------------------------------

        (
            "drop skills filter",
            {
                "role_priority": "MUST_HAVE",
                "include_skills": False
            }
        ),

        # -----------------------------------------------------
        # Attempt 3
        # Role becomes CAN_HAVE
        # -----------------------------------------------------

        (
            "role downgraded to CAN_HAVE",
            {
                "role_priority": "CAN_HAVE",
                "include_skills": False
            }
        ),

        # -----------------------------------------------------
        # Attempt 4
        # Remove company
        # -----------------------------------------------------

        (
            "drop company filter",
            {
                "role_priority": "CAN_HAVE",
                "include_skills": False,
                "include_company": False
            }
        ),

        # -----------------------------------------------------
        # Attempt 5
        # Remove location
        # -----------------------------------------------------

        (
            "role keywords only, no company/location",
            {
                "role_priority": "CAN_HAVE",
                "include_skills": False,
                "include_company": False,
                "include_location": False
            }
        ),
    ]

    last_result = None
    last_payload = None

    for label, kwargs in ladder:

        payload = build_payload(
            extracted,
            **kwargs
        )

        print(
            f"\nAttempt [{label}]:"
        )

        print(
            json.dumps(
                payload,
                indent=2
            )
        )

        result = run_search(
            payload,
            limit=SEARCH_LIMIT
        )

        count = _result_count(result)

        print(
            f"  -> {count} total candidates"
        )

        last_result = result
        last_payload = payload

        if count > 0:

            result["_fallback_step"] = label
            result["_payload_used"] = payload

            return result

    # ---------------------------------------------------------
    # All attempts returned zero
    # ---------------------------------------------------------

    print(
        "\nWARNING: every fallback rung returned 0 candidates."
    )

    print(
        "The JD may genuinely be too niche for this "
        "LinkedIn Recruiter account/search context."
    )

    last_result["_fallback_step"] = (
        "exhausted all rungs, still 0"
    )

    last_result["_payload_used"] = last_payload

    return last_result


# ---------------------------------------------------------------------------
# 10. COMPLETE PIPELINE
# ---------------------------------------------------------------------------

def run_pipeline_v2(jd_text: str) -> dict:
    """
    Complete pipeline:

        JD
          ↓
        GPT extraction
          ↓
        normalization
          ↓
        company/title safety check
          ↓
        Unipile ID resolution
          ↓
        Recruiter search
          ↓
        automatic fallback
          ↓
        max 100 results returned
    """

    print(
        "=================================================="
    )

    print(
        "Extracting LinkedIn search parameters from JD..."
    )

    print(
        "=================================================="
    )

    # ---------------------------------------------------------
    # Step 1: GPT extraction
    # ---------------------------------------------------------

    extracted = extract_search_params(
        jd_text
    )

    # ---------------------------------------------------------
    # Step 2: Normalize + sanitize
    # ---------------------------------------------------------

    extracted = normalize_extracted_params(
        extracted
    )

    print(
        "\nExtracted parameters:"
    )

    print(
        json.dumps(
            extracted,
            indent=2
        )
    )

    # ---------------------------------------------------------
    # Step 3: Search
    # ---------------------------------------------------------

    print(
        "\n=================================================="
    )

    print(
        "Running LinkedIn Recruiter search..."
    )

    print(
        "Search limit = 100"
    )

    print(
        "=================================================="
    )

    result = search_with_fallback(
        extracted
    )

    # ---------------------------------------------------------
    # Step 4: Final information
    # ---------------------------------------------------------

    print(
        "\n=================================================="
    )

    print(
        "FINAL SEARCH RESULT"
    )

    print(
        "=================================================="
    )

    print(
        f"Fallback step: "
        f"{result.get('_fallback_step')}"
    )

    print(
        f"Total candidates: "
        f"{_result_count(result)}"
    )

    print(
        f"Returned items: "
        f"{len(result.get('items', []))}"
    )

    print(
        "\nPayload used:"
    )

    print(
        json.dumps(
            result.get(
                "_payload_used",
                {}
            ),
            indent=2
        )
    )

    return result