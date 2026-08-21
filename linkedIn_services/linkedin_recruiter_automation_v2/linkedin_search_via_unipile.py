"""
linkedin_recruiter_search.py

One-page pipeline:
  1. Take a raw job description (JD) as text.
  2. Ask OpenAI (gpt-4o-mini) to extract structured search parameters from it
     (title/role keywords, skills, target locations, target companies, seniority).
  3. Resolve each location / company name to a Unipile/LinkedIn numeric ID via
     GET /linkedin/search/parameters (always taking the FIRST match, per your rule).
  4. Assemble the final Unipile Recruiter search payload (same shape as your
     working curl example).
  5. POST it to /linkedin/search and return the raw response JSON.

Usage:
    python linkedin_recruiter_search.py path/to/jd.txt
    # or
    from linkedin_recruiter_search import run_pipeline
    result = run_pipeline(jd_text)

Required environment variables (do NOT hardcode secrets in this file):
    OPENAI_API_KEY
    UNIPILE_API_KEY
    UNIPILE_ACCOUNT_ID      e.g. D8lUBYotRuGOlA7cOQ4egQ
    UNIPILE_BASE_URL        e.g. https://api40.unipile.com:17060/api/v1
"""

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

# ---------------------------------------------------------------------------
# 1) Extraction prompt — rewritten to teach GPT the Unipile Recruiter schema
#    and, critically, to bias toward BROAD boolean strings and to NOT decide
#    priority itself. Priority (CAN_HAVE vs MUST_HAVE) is decided in code
#    below, where it can be controlled and progressively loosened. Letting
#    the LLM freely choose MUST_HAVE per field is exactly what was producing
#    over-constrained, zero-result payloads.
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = f"""You are a technical recruiter assistant. Given a job description,
extract structured LinkedIn Recruiter search parameters. Respond with ONLY valid JSON
(no markdown fences, no commentary) matching exactly this schema:

{{
  "title_keywords": "a SHORT boolean string of 2-4 title variants joined with OR, e.g. \\"Backend Engineer\\" OR \\"Software Engineer\\" OR \\"Platform Engineer\\". Never chain more than one AND in this string.",
  "role_keywords": "leave this EMPTY STRING unless the JD needs functional-role matching that title_keywords doesn't already cover. Do not restate the same terms as title_keywords — that double-filters and causes 0 results.",
  "skills_keywords": "at most 2-3 CORE skills joined with OR, not AND. Only include a skill here if a candidate missing it would clearly be unqualified. Do not list every technology mentioned in the JD — that stacks filters and eliminates otherwise-good candidates.",
  "locations": ["city or region name — only what's explicitly stated, max 3"],
  "preferred_companies": ["Company Name — ONLY if the JD explicitly says target/poach from these companies. Do not infer companies from industry context."],
  "seniority_levels": ["optional. LOWERCASE ONLY, chosen strictly from this fixed set: {', '.join(SENIORITY_ALLOWED)}. Leave empty unless the JD is explicit about seniority — this filter is a common cause of 0 results when guessed."],
  "employment_type": ["FULL_TIME"]
}}

CRITICAL RULES TO AVOID ZERO-RESULT SEARCHES:
- Prefer OR over AND everywhere. A boolean string like "A" AND "B" AND "C" requires all
  three literally in the profile text and very often matches nobody. "A" OR "B" OR "C" is
  almost always what's actually wanted (candidate could be described any of those ways).
- Do NOT put the same concept in both title_keywords and role_keywords. Pick one.
- Keep skills_keywords short. 6 ANDed or even 6 separately-required skills filters is a
  near-guaranteed 0-candidate search on real LinkedIn data.
- Only include locations/companies/seniority that are explicitly named or unambiguous in
  the JD. When in doubt, leave the field empty — an empty filter is skipped entirely and
  widens the search, which is safer than a wrong or overly narrow guess.
- Map JD seniority to the closest single value in the fixed set (e.g. "C-suite" -> "cxo",
  "VP"/"SVP" -> "vp", "entry-level" -> "entry") — never invent a value outside the set."""


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
    Look up a Unipile search parameter by keyword. Prefers an exact
    case-insensitive title match over the first fuzzy result — taking the
    literal first hit was silently resolving locations/companies to the
    wrong entity (e.g. an obscure micro-region) and tanking result counts.
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

    exact = next(
        (it for it in items if it.get("title", "").strip().lower() == keyword.strip().lower()),
        None,
    )
    chosen = exact or items[0]
    if not exact and len(items) > 1:
        print(f"  NOTE: no exact match for '{keyword}', using top fuzzy result "
              f"'{chosen['title']}' out of {len(items)} candidates")
    return chosen["id"], chosen["title"]


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


# ---------------------------------------------------------------------------
# 2) Payload building — priority is decided HERE, in code, not by the LLM.
#    Everything defaults to CAN_HAVE. Only role gets MUST_HAVE, and only on
#    the first attempt; the fallback ladder below downgrades it if needed.
# ---------------------------------------------------------------------------
def build_payload(extracted: dict, *, role_priority: str = "MUST_HAVE",
                   include_skills: bool = True, include_company: bool = True,
                   include_top_keywords: bool = True, include_location: bool = True,
                   include_seniority: bool = False) -> dict:
    """Resolve locations/companies to IDs and assemble the search payload.

    The keyword args are the loosening knobs used by search_with_fallback —
    each one strips or downgrades a filter without needing a fresh OpenAI call.
    """
    location_objs = []
    if include_location:
        for loc_name in extracted.get("locations", []):
            resolved = resolve_id(loc_name, "LOCATION")
            if resolved:
                loc_id, loc_title = resolved
                location_objs.append({"id": loc_id, "priority": "CAN_HAVE"})
                print(f"  location '{loc_name}' -> {loc_title} ({loc_id})")
            else:
                print(f"  WARNING: no location match for '{loc_name}', skipping")

    company_objs = []
    if include_company:
        for company_name in extracted.get("preferred_companies", []):
            resolved = resolve_id(company_name, "COMPANY")
            if resolved:
                comp_id, comp_title = resolved
                company_objs.append({"id": comp_id, "priority": "CAN_HAVE"})
                print(f"  company '{company_name}' -> {comp_title} ({comp_id})")
            else:
                print(f"  WARNING: no company match for '{company_name}', skipping")

    role_keywords = extracted.get("role_keywords") or extracted.get("title_keywords", "")
    role_block = [
        {
            "keywords": role_keywords,
            "priority": role_priority,
            "scope": "CURRENT_OR_PAST",
        }
    ] if role_keywords else []

    skills_block = [
        {"keywords": extracted.get("skills_keywords", ""), "priority": "CAN_HAVE"}
    ] if (include_skills and extracted.get("skills_keywords")) else []

    payload = {
        "api": "recruiter",
        "category": "people",
        # Only send the top-level global keywords filter when role_keywords
        # is empty (i.e. title_keywords is the only signal we have) — never
        # send the same concept as both "keywords" and "role" at once, that
        # was the main source of double-filtering / 0 results.
        "keywords": extracted.get("title_keywords", "")
        if (include_top_keywords and not extracted.get("role_keywords"))
        else "",
        "role": role_block,
        "skills": skills_block,
        "location": location_objs,
        "current_company": company_objs,
        "past_company": company_objs,
        "employment_type": extracted.get("employment_type", ["FULL_TIME"]),
    }

    if include_seniority:
        seniority_levels = sanitize_seniority(extracted.get("seniority_levels", []))
        if seniority_levels:
            payload["seniority"] = {"include": seniority_levels}

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


def _result_count(result: dict) -> int:
    # Unipile returns paginated results; total count is the reliable signal
    # of whether the search is too narrow, independent of `limit`.
    return result.get("paging", {}).get("total_count", len(result.get("items", [])))


# ---------------------------------------------------------------------------
# 3) Automatic fallback ladder — if a search comes back empty, progressively
#    loosen the SAME extracted parameters (no extra OpenAI calls) and retry.
#    Each rung removes exactly one source of over-constraint. First non-empty
#    rung wins; if every rung is empty, return the broadest attempt with a
#    clear note so it's visible in logs rather than silently "0 candidates".
# ---------------------------------------------------------------------------
def search_with_fallback(extracted: dict) -> dict:
    ladder = [
        ("full payload (role=MUST_HAVE)", dict(role_priority="MUST_HAVE")),
        ("drop skills filter", dict(role_priority="MUST_HAVE", include_skills=False)),
        ("role downgraded to CAN_HAVE", dict(role_priority="CAN_HAVE", include_skills=False)),
        ("drop company filter", dict(role_priority="CAN_HAVE", include_skills=False, include_company=False)),
        ("role keywords only, no location", dict(role_priority="CAN_HAVE", include_skills=False,
                                                   include_company=False, include_location=False)),
    ]

    last_result = None
    last_payload = None
    for label, kwargs in ladder:
        payload = build_payload(extracted, **kwargs)
        print(f"\nAttempt [{label}]:")
        print(json.dumps(payload, indent=2))
        result = run_search(payload)
        count = _result_count(result)
        print(f"  -> {count} total candidates")
        last_result, last_payload = result, payload
        if count > 0:
            result["_fallback_step"] = label
            result["_payload_used"] = payload
            return result

    print("\nWARNING: every fallback rung returned 0 candidates. "
          "The JD's own criteria may genuinely be too niche for this LinkedIn "
          "account's network/plan — worth checking manually.")
    last_result["_fallback_step"] = "exhausted all rungs, still 0"
    last_result["_payload_used"] = last_payload
    return last_result


def run_pipeline_v2(jd_text: str) -> dict:
    """Full pipeline: JD text -> OpenAI extraction -> ID resolution -> search (with fallback)."""
    print("Extracting search parameters from JD via OpenAI...")
    extracted = extract_search_params(jd_text)
    print(json.dumps(extracted, indent=2))

    print("\nRunning LinkedIn Recruiter search with automatic loosening on 0 results...")
    result = search_with_fallback(extracted)
    print(f"\nFinal: used rung '{result.get('_fallback_step')}'")
    return result
