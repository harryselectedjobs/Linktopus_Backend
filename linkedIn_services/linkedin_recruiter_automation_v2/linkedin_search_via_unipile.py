import os
import json
import requests

# ---------------------------------------------------------------------------
# Config (all from env vars — never commit real keys)
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"
UNIPILE_BASE_URL = os.environ.get("UNIPILE_BASE_URL", "https://api40.unipile.com:17060/api/v1")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UNIPILE_PARAMS_URL = f"{UNIPILE_BASE_URL}/linkedin/search/parameters"
UNIPILE_SEARCH_URL = f"{UNIPILE_BASE_URL}/linkedin/search"

SENIORITY_ALLOWED = [
    "owner", "partner", "cxo", "vp", "director", "manager",
    "senior", "entry", "training", "unpaid",
]

EXTRACTION_SYSTEM_PROMPT = f"""You are a technical recruiter assistant. Given a job description,
extract structured LinkedIn Recruiter search parameters. Respond with ONLY valid JSON
(no markdown fences, no commentary) matching exactly this schema:

{{
  "title_keywords": "boolean keyword string for the 'keywords' field, e.g. \\"Title A\\" OR \\"Title B\\"",
  "role_keywords": "boolean keyword string for functional/role matching",
  "skills_keywords": "boolean keyword string for the skills field",
  "locations": ["city or region name", "..."],
  "preferred_companies": ["Company Name", "..."],
  "seniority_levels": ["one or more values, LOWERCASE ONLY, chosen strictly from this fixed set: {', '.join(SENIORITY_ALLOWED)}"],
  "employment_type": ["FULL_TIME"]
}}

Only include locations/companies that are explicitly named or clearly implied in the JD.
Keep each keyword string boolean-search-ready (quoted phrases joined with OR/AND).
For seniority_levels: pick ONLY from {SENIORITY_ALLOWED} (lowercase, exact spelling, no synonyms
outside this list). Map the JD's stated seniority to the closest matching value(s) in the set —
e.g. a "C-suite / Chief X Officer" role maps to "cxo"; "VP"/"SVP" maps to "vp"; "entry-level" maps
to "entry"."""


def extract_search_params(jd_text: str) -> dict:
    """Call OpenAI to turn a raw JD into structured search parameters."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": jd_text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def resolve_id(keyword: str, param_type: str) -> tuple[str, str] | None:
    """
    Look up a Unipile search parameter by keyword and return (id, title) of the
    FIRST result, per your rule of always taking the top match.
    Returns None if nothing is found.
    """
    headers = {"X-API-KEY": UNIPILE_API_KEY, "accept": "application/json"}
    params = {
        "keywords": keyword,
        "type": param_type,
        "account_id": UNIPILE_ACCOUNT_ID,
    }
    resp = requests.get(UNIPILE_PARAMS_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return None
    first = items[0]
    return first["id"], first["title"]


def sanitize_seniority(levels: list) -> list:
    """Keep only values from the fixed allowed enum, forced to lowercase."""
    cleaned = []
    for lvl in levels or []:
        lvl_lc = str(lvl).strip().lower()
        if lvl_lc in SENIORITY_ALLOWED and lvl_lc not in cleaned:
            cleaned.append(lvl_lc)
        else:
            print(f"  WARNING: dropping invalid seniority value '{lvl}'")
    return cleaned


def build_payload(extracted: dict) -> dict:
    """Resolve locations/companies to IDs and assemble the final search payload."""
    location_objs = []
    for loc_name in extracted.get("locations", []):
        resolved = resolve_id(loc_name, "LOCATION")
        if resolved:
            loc_id, loc_title = resolved
            location_objs.append({"id": loc_id, "priority": "CAN_HAVE"})
            print(f"  location '{loc_name}' -> {loc_title} ({loc_id})")
        else:
            print(f"  WARNING: no location match for '{loc_name}', skipping")

    company_objs = []
    for company_name in extracted.get("preferred_companies", []):
        resolved = resolve_id(company_name, "COMPANY")
        if resolved:
            comp_id, comp_title = resolved
            company_objs.append({"id": comp_id, "priority": "CAN_HAVE"})
            print(f"  company '{company_name}' -> {comp_title} ({comp_id})")
        else:
            print(f"  WARNING: no company match for '{company_name}', skipping")

    payload = {
        "api": "recruiter",
        "category": "people",
        "keywords": extracted.get("title_keywords", ""),
        "role": [
            {
                "keywords": extracted.get("role_keywords", ""),
                "priority": "MUST_HAVE",
                "scope": "CURRENT_OR_PAST",
            }
        ] if extracted.get("role_keywords") else [],
        "skills": [
            {"keywords": extracted.get("skills_keywords", ""), "priority": "CAN_HAVE"}
        ] if extracted.get("skills_keywords") else [],
        "location": location_objs,
        "current_company": company_objs,
        "past_company": company_objs,
        "employment_type": extracted.get("employment_type", ["FULL_TIME"]),
    }

    # seniority_levels = sanitize_seniority(extracted.get("seniority_levels", []))
    # if seniority_levels:
    #     payload["seniority"] = {"include": seniority_levels}

    # Drop empty lists/strings so we don't send noisy filters
    return {k: v for k, v in payload.items() if v not in ("", [], None)}


def run_search(payload: dict) -> dict:
    """POST the assembled payload to the Unipile LinkedIn Recruiter search endpoint."""
    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }
    params = {"account_id": UNIPILE_ACCOUNT_ID}
    resp = requests.post(UNIPILE_SEARCH_URL, headers=headers, params=params, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def run_pipeline_v2(jd_text: str) -> dict:
    """Full pipeline: JD text -> OpenAI extraction -> ID resolution -> search -> results."""
    print("Extracting search parameters from JD via OpenAI...")
    extracted = extract_search_params(jd_text)
    print(json.dumps(extracted, indent=2))

    print("\nResolving location/company IDs via Unipile...")
    payload = build_payload(extracted)
    print("\nFinal payload:")
    print(json.dumps(payload, indent=2))

    print("\nRunning LinkedIn Recruiter search...")
    result = run_search(payload)
    return result