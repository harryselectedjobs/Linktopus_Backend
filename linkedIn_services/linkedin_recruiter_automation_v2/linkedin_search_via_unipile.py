import os
import json
import re
import requests
from typing import Optional, Dict, List, Tuple


# =============================================================================
# CONFIG
# =============================================================================

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# Put these in environment variables.
UNIPILE_API_KEY = os.environ["UNIPILE_API_KEY"]
UNIPILE_ACCOUNT_ID = os.environ["UNIPILE_ACCOUNT_ID"]

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


# =============================================================================
# SEARCH SETTINGS
# =============================================================================

# Recruiter supports up to 100 results per request.
SEARCH_LIMIT = 100

# Keep the search intentionally broad.
MAX_ROLE_TITLES = 5
MAX_SKILLS = 4
MAX_LOCATIONS = 10
MAX_COMPANIES = 10

# We don't want an enormous boolean expression.
MAX_ROLE_KEYWORDS_LENGTH = 500
MAX_SKILLS_KEYWORDS_LENGTH = 300


# =============================================================================
# OPENAI SYSTEM PROMPT
# =============================================================================

EXTRACTION_SYSTEM_PROMPT = r"""
You are an expert LinkedIn Recruiter search strategist.

Your job is to read a job description and extract SEARCH INTENT.

You are NOT responsible for constructing the final Unipile API payload.

Python code will construct the final payload.

Your most important objective is:

CREATE A BROAD, RELEVANT CANDIDATE POOL.

Do NOT over-filter.

Do NOT turn every requirement from the JD into a hard search condition.

===========================================================
IMPORTANT LINKEDIN / UNIPILE SEARCH STRATEGY
===========================================================

The application uses LinkedIn Recruiter through the Unipile API.

Unipile Recruiter people searches support:

- role
- skills
- location
- current company
- past company
- employment type
- other recruiter filters

However, this application deliberately avoids restrictive filtering.

The Python application will decide whether a filter is used.

You must ONLY extract the information.

===========================================================
RULE 1 — ROLE TITLES
===========================================================

Extract the best job-title alternatives.

Maximum 5.

Example:

JD:
"Node.js Developer"

Good:

[
  "Node.js Developer",
  "Node.js Engineer",
  "Backend Developer"
]

Do NOT return:

[
  "Node.js Developer AND JavaScript AND MongoDB AND AWS"
]

Do not put skills in role_titles.

Do not put company names in role_titles.

Do not put locations in role_titles.

Do not put years of experience in role_titles.

Role titles should be actual job titles.

===========================================================
RULE 2 — SKILLS
===========================================================

Extract only the most important technical skills.

Maximum 4.

Do NOT extract every technology mentioned in the JD.

For example, if the JD contains:

Python
FastAPI
Django
AWS
Docker
Kubernetes
Redis
PostgreSQL
Kafka
Git

You should normally return only the strongest 3-4 signals, such as:

[
  "Python",
  "FastAPI",
  "Django",
  "AWS"
]

Do not make skills mandatory.

Python code will always treat skills as CAN_HAVE.

===========================================================
RULE 3 — LOCATIONS
===========================================================

THIS IS VERY IMPORTANT.

If the JD explicitly contains a Location section, extract EVERY location.

Never omit an explicitly mentioned location.

Example:

Location:
Noida, Hyderabad, Bengaluru

Return:

[
  "Noida",
  "Hyderabad",
  "Bengaluru"
]

Do not choose only one.

Do not summarize them.

Do not invent additional locations.

===========================================================
RULE 4 — PREFERRED COMPANIES
===========================================================

THIS IS VERY IMPORTANT.

If the JD contains:

- Preferred Company Background
- Preferred Companies
- Target Companies
- Previous Company
- Company Background
- Experience at
- candidates from
- worked at
- similar organizations

extract the explicitly named companies.

Example:

Preferred Company Background:
CTS, TCS, Wipro, Infosys, HCLTech, Accenture

Return:

[
  "CTS",
  "TCS",
  "Wipro",
  "Infosys",
  "HCLTech",
  "Accenture"
]

Do NOT put these company names into role_titles.

Do NOT put them into skills.

Do not combine company + title.

BAD:

"CTS Node.js Developer"

BAD:

"TCS Backend Developer"

GOOD:

role_titles:
[
  "Node.js Developer",
  "Node.js Engineer",
  "Backend Developer"
]

preferred_companies:
[
  "CTS",
  "TCS",
  "Wipro"
]

===========================================================
RULE 5 — COMPARABLE / SIMILAR COMPANIES
===========================================================

If the JD explicitly says that certain companies are comparable,
extract the companies that are actually named.

Example:

"CTS, TCS, Wipro or comparable organizations such as Infosys,
HCLTech, Cognizant or Accenture"

Return all explicitly named companies.

Do not invent additional companies.

===========================================================
RULE 6 — EMPLOYMENT TYPE
===========================================================

Only return employment type if it is explicitly stated.

For example:

"Full-time position"

=> ["FULL_TIME"]

If the JD does NOT explicitly state employment type:

=> []

NEVER assume FULL_TIME.

===========================================================
RULE 7 — SENIORITY
===========================================================

Do NOT return seniority.

Do NOT return:

manager
director
senior
vp
cxo
entry
etc.

Years of experience may help understand the role but should NOT become
a LinkedIn seniority filter.

===========================================================
RULE 8 — BOOLEAN SEARCH
===========================================================

DO NOT construct complicated boolean expressions.

You should return arrays.

Python will construct simple OR expressions.

For example:

role_titles:
[
  "Node.js Developer",
  "Node.js Engineer",
  "Backend Developer"
]

Python will create:

"Node.js Developer" OR "Node.js Engineer" OR "Backend Developer"

Do NOT use AND.

===========================================================
RULE 9 — DO NOT OVERFIT
===========================================================

The job description may contain many requirements.

Do not turn all of them into search filters.

Search is for finding a candidate pool.

Candidate qualification can happen later.

===========================================================
RULE 10 — NO LOCATION OR COMPANY LOSS
===========================================================

If a location or preferred company is explicitly written in the JD,
it MUST be returned.

Do not omit it simply because it is a preference.

===========================================================
OUTPUT
===========================================================

Return ONLY valid JSON.

Use exactly this schema:

{
  "role_titles": [],
  "skills": [],
  "locations": [],
  "preferred_companies": [],
  "employment_type": []
}

No additional fields.

No markdown.

No explanation.
"""


# =============================================================================
# BASIC HELPERS
# =============================================================================

def normalize_text(value: str) -> str:
    if value is None:
        return ""

    value = str(value).strip().lower()

    value = re.sub(r"\s+", " ", value)

    return value


def unique_clean_list(
    values,
    maximum: int
) -> List[str]:

    if not isinstance(values, list):
        return []

    result = []
    seen = set()

    for value in values:

        if value is None:
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
# OPENAI EXTRACTION
# =============================================================================

def extract_search_intent(
    jd_text: str
) -> dict:

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

    content = (
        response
        .json()["choices"][0]["message"]["content"]
    )

    return json.loads(content)


# =============================================================================
# SANITIZE GPT OUTPUT
# =============================================================================

def sanitize_search_intent(
    extracted: dict
) -> dict:

    if not isinstance(extracted, dict):
        extracted = {}

    role_titles = unique_clean_list(
        extracted.get("role_titles", []),
        MAX_ROLE_TITLES
    )

    skills = unique_clean_list(
        extracted.get("skills", []),
        MAX_SKILLS
    )

    locations = unique_clean_list(
        extracted.get("locations", []),
        MAX_LOCATIONS
    )

    companies = unique_clean_list(
        extracted.get("preferred_companies", []),
        MAX_COMPANIES
    )

    employment_type = unique_clean_list(
        extracted.get("employment_type", []),
        5
    )

    return {
        "role_titles": role_titles,
        "skills": skills,
        "locations": locations,
        "preferred_companies": companies,
        "employment_type": employment_type
    }


# =============================================================================
# BUILD SIMPLE OR EXPRESSION
# =============================================================================

def build_or_expression(
    values: List[str],
    maximum: int,
    max_length: int
) -> str:

    values = unique_clean_list(
        values,
        maximum
    )

    parts = []

    for value in values:

        value = value.strip()

        if not value:
            continue

        # Escape embedded quotes.
        value = value.replace('"', '\\"')

        # Quote multi-word phrases.
        if " " in value:

            part = f'"{value}"'

        else:

            part = value

        parts.append(part)

    expression = " OR ".join(parts)

    if len(expression) > max_length:

        expression = expression[:max_length]

        # Avoid ending halfway through a phrase/operator.
        expression = expression.rsplit(" OR ", 1)[0]

    return expression


# =============================================================================
# PARAMETER RESOLUTION
# =============================================================================

def resolve_parameter(
    keyword: str,
    parameter_type: str
) -> Optional[Dict]:

    """
    Resolve a human-readable LinkedIn parameter to its Unipile ID.

    Unipile requires IDs for many search parameters, so this endpoint
    is used before constructing the Recruiter search payload.
    """

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json"
    }

    params = {
        "keywords": keyword,
        "type": parameter_type,
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

    data = response.json()

    items = data.get("items", [])

    if not items:
        print(
            f"[PARAMETER] No match for "
            f"{parameter_type}: {keyword}"
        )

        return None

    normalized_keyword = normalize_text(keyword)

    # -------------------------------------------------------------------------
    # 1. Exact match
    # -------------------------------------------------------------------------

    for item in items:

        title = item.get("title", "")

        if normalize_text(title) == normalized_keyword:

            return {
                "id": str(item["id"]),
                "title": title
            }

    # -------------------------------------------------------------------------
    # 2. Strong partial match
    # -------------------------------------------------------------------------

    candidates = []

    for item in items:

        title = item.get("title", "")

        normalized_title = normalize_text(title)

        if (
            normalized_keyword in normalized_title
            or
            normalized_title in normalized_keyword
        ):

            candidates.append(item)

    if candidates:

        best = candidates[0]

        return {
            "id": str(best["id"]),
            "title": best.get("title", "")
        }

    # -------------------------------------------------------------------------
    # 3. Fallback
    # -------------------------------------------------------------------------

    first = items[0]

    return {
        "id": str(first["id"]),
        "title": first.get("title", "")
    }


# =============================================================================
# RESOLVE LOCATIONS
# =============================================================================

def resolve_locations(
    locations: List[str]
) -> List[Dict]:

    resolved = []

    for location in locations:

        result = resolve_parameter(
            location,
            "LOCATION"
        )

        if not result:
            continue

        resolved.append(
            {
                "id": result["id"],
                "priority": "CAN_HAVE"
            }
        )

        print(
            f"[LOCATION] {location} "
            f"-> {result['title']} "
            f"({result['id']})"
        )

    return resolved


# =============================================================================
# RESOLVE COMPANIES
# =============================================================================

def resolve_companies(
    companies: List[str]
) -> List[Dict]:

    resolved = []

    for company in companies:

        result = resolve_parameter(
            company,
            "COMPANY"
        )

        if not result:
            continue

        resolved.append(
            {
                "id": result["id"],
                "priority": "CAN_HAVE"
            }
        )

        print(
            f"[COMPANY] {company} "
            f"-> {result['title']} "
            f"({result['id']})"
        )

    return resolved


# =============================================================================
# BUILD BASE PAYLOAD
# =============================================================================

def build_base_payload(
    intent: dict
) -> dict:

    role_expression = build_or_expression(
        intent.get("role_titles", []),
        MAX_ROLE_TITLES,
        MAX_ROLE_KEYWORDS_LENGTH
    )

    skills_expression = build_or_expression(
        intent.get("skills", []),
        MAX_SKILLS,
        MAX_SKILLS_KEYWORDS_LENGTH
    )

    location_objects = resolve_locations(
        intent.get("locations", [])
    )

    company_objects = resolve_companies(
        intent.get("preferred_companies", [])
    )

    payload = {
        "api": "recruiter",
        "category": "people"
    }

    # =========================================================================
    # ROLE
    #
    # IMPORTANT:
    #
    # CAN_HAVE
    #
    # We deliberately do NOT use MUST_HAVE.
    # =========================================================================

    if role_expression:

        payload["role"] = [
            {
                "keywords": role_expression,
                "priority": "CAN_HAVE",
                "scope": "CURRENT_OR_PAST"
            }
        ]

    # =========================================================================
    # SKILLS
    # =========================================================================

    if skills_expression:

        payload["skills"] = [
            {
                "keywords": skills_expression,
                "priority": "CAN_HAVE"
            }
        ]

    # =========================================================================
    # LOCATION
    # =========================================================================

    if location_objects:

        payload["location"] = location_objects

    # =========================================================================
    # COMPANY
    #
    # Same company IDs can be used for current and past company.
    # Both are CAN_HAVE.
    # =========================================================================

    if company_objects:

        payload["current_company"] = company_objects

        payload["past_company"] = company_objects

    # =========================================================================
    # EMPLOYMENT TYPE
    #
    # ONLY if explicitly present in the JD.
    # =========================================================================

    employment_type = intent.get(
        "employment_type",
        []
    )

    if employment_type:

        payload["employment_type"] = employment_type

    return payload


# =============================================================================
# PAYLOAD COPY
# =============================================================================

def clone_payload(
    payload: dict
) -> dict:

    return json.loads(
        json.dumps(payload)
    )


# =============================================================================
# FALLBACK PAYLOADS
# =============================================================================

def create_fallback_payloads(
    base_payload: dict
) -> List[Tuple[str, dict]]:

    payloads = []

    # =========================================================================
    # LEVEL 1
    #
    # Role + Skills + Location + Companies
    # =========================================================================

    payloads.append(
        (
            "role + skills + location + companies",
            clone_payload(base_payload)
        )
    )

    # =========================================================================
    # LEVEL 2
    #
    # Remove skills.
    #
    # Role + Location + Companies
    # =========================================================================

    payload_2 = clone_payload(
        base_payload
    )

    payload_2.pop(
        "skills",
        None
    )

    payloads.append(
        (
            "role + location + companies",
            payload_2
        )
    )

    # =========================================================================
    # LEVEL 3
    #
    # Remove preferred company.
    #
    # Role + Location
    # =========================================================================

    payload_3 = clone_payload(
        payload_2
    )

    payload_3.pop(
        "current_company",
        None
    )

    payload_3.pop(
        "past_company",
        None
    )

    payloads.append(
        (
            "role + location",
            payload_3
        )
    )

    # =========================================================================
    # LEVEL 4
    #
    # Remove location.
    #
    # Role only.
    # =========================================================================

    payload_4 = clone_payload(
        payload_3
    )

    payload_4.pop(
        "location",
        None
    )

    payloads.append(
        (
            "role only",
            payload_4
        )
    )

    # =========================================================================
    # LEVEL 5
    #
    # Simplify role.
    # =========================================================================

    payload_5 = clone_payload(
        payload_4
    )

    if "role" in payload_5:

        role = payload_5["role"]

        if role:

            keywords = role[0].get(
                "keywords",
                ""
            )

            terms = re.split(
                r"\s+OR\s+",
                keywords,
                flags=re.IGNORECASE
            )

            terms = [
                term.strip()
                for term in terms
                if term.strip()
            ]

            # Keep the first 2 broad titles.
            terms = terms[:2]

            if terms:

                role[0]["keywords"] = (
                    " OR ".join(terms)
                )

    payloads.append(
        (
            "simplified role",
            payload_5
        )
    )

    return payloads


# =============================================================================
# RESULT COUNT
# =============================================================================

def get_result_count(
    result: dict
) -> int:

    if not isinstance(result, dict):
        return 0

    items = result.get(
        "items"
    )

    if isinstance(items, list):

        return len(items)

    paging = result.get(
        "paging"
    )

    if isinstance(paging, dict):

        total_count = paging.get(
            "total_count"
        )

        if isinstance(
            total_count,
            int
        ):

            return total_count

    return 0


# =============================================================================
# EXECUTE UNIPILE SEARCH
# =============================================================================

def execute_unipile_search(
    payload: dict,
    limit: int = SEARCH_LIMIT
) -> dict:

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
# SEARCH WITH AUTOMATIC RELAXATION
# =============================================================================

def search_with_fallbacks(
    base_payload: dict
) -> dict:

    fallback_payloads = (
        create_fallback_payloads(
            base_payload
        )
    )

    last_result = None
    last_payload = None
    last_strategy = None

    for strategy, payload in fallback_payloads:

        print("\n")
        print("=" * 90)
        print(
            f"SEARCH STRATEGY: {strategy}"
        )
        print("=" * 90)

        print(
            json.dumps(
                payload,
                indent=2
            )
        )

        try:

            result = execute_unipile_search(
                payload,
                limit=SEARCH_LIMIT
            )

        except requests.HTTPError as exc:

            print(
                f"[SEARCH ERROR] "
                f"{strategy}: {exc}"
            )

            # Continue to next fallback.
            continue

        count = get_result_count(
            result
        )

        print(
            f"[RESULTS] {count}"
        )

        last_result = result
        last_payload = payload
        last_strategy = strategy

        # ---------------------------------------------------------------------
        # SUCCESS
        # ---------------------------------------------------------------------

        if count > 0:

            print(
                f"[SUCCESS] Found {count} "
                f"candidates using: {strategy}"
            )

            return {
                "success": True,
                "candidate_count": count,
                "search_strategy": strategy,
                "payload_used": payload,
                "result": result
            }

        print(
            "[EMPTY] 0 candidates. "
            "Relaxing search..."
        )

    # =========================================================================
    # EVERYTHING RETURNED ZERO
    # =========================================================================

    return {
        "success": False,
        "candidate_count": 0,
        "search_strategy": last_strategy,
        "payload_used": last_payload,
        "result": last_result
    }


# =============================================================================
# COMPLETE PIPELINE
# =============================================================================

def run_pipeline_v2(
    jd_text: str
) -> dict:

    print("\n")
    print("=" * 90)
    print("STEP 1 — GPT SEARCH INTENT EXTRACTION")
    print("=" * 90)

    raw_intent = extract_search_intent(
        jd_text
    )

    print(
        json.dumps(
            raw_intent,
            indent=2
        )
    )

    print("\n")
    print("=" * 90)
    print("STEP 2 — SANITIZING GPT OUTPUT")
    print("=" * 90)

    intent = sanitize_search_intent(
        raw_intent
    )

    print(
        json.dumps(
            intent,
            indent=2
        )
    )

    print("\n")
    print("=" * 90)
    print("STEP 3 — RESOLVING LINKEDIN PARAMETER IDS")
    print("=" * 90)

    payload = build_base_payload(
        intent
    )

    print("\n")
    print("=" * 90)
    print("BASE PAYLOAD")
    print("=" * 90)

    print(
        json.dumps(
            payload,
            indent=2
        )
    )

    print("\n")
    print("=" * 90)
    print("STEP 4 — RECRUITER SEARCH")
    print("=" * 90)

    search_result = search_with_fallbacks(
        payload
    )

    print("\n")
    print("=" * 90)
    print("FINAL SEARCH INFORMATION")
    print("=" * 90)

    print(
        json.dumps(
            {
                "success": search_result.get(
                    "success"
                ),
                "candidate_count": search_result.get(
                    "candidate_count"
                ),
                "search_strategy": search_result.get(
                    "search_strategy"
                ),
                "payload_used": search_result.get(
                    "payload_used"
                )
            },
            indent=2
        )
    )

    return {
        "intent": intent,
        **search_result
    }