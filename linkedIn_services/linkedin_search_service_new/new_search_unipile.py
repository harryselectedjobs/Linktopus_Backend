from __future__ import annotations

import os
import re
import json
import time
import math
import threading
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
import requests

# =============================================================================
# CONFIG -- replace with your real keys / account ids
# =============================================================================
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_PATH)

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"
UNIPILE_BASE_URL = "https://api40.unipile.com:17060"

UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="
UNIPILE_PROJECTS_ACCOUNT_ID = "acc_01m09sdddhfetrdm9tzcbqncv1"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UNIPILE_SEARCH_PARAMS_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search/parameters"
UNIPILE_SEARCH_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search"


# =============================================================================
# SECTION 1 -- extraction (titles + locations + companies) for BUILDING the
# Unipile search payload. This is a lighter-weight extraction than the one
# used later for candidate evaluation: it's aimed at feeding LinkedIn's own
# search API (so it wants LinkedIn-style title phrasing and resolvable
# location/company names), not at judging candidates.
# =============================================================================

_EXTRACTION_AND_COMPANY_PROMPT = """You are an information extractor and sourcing assistant. You will be given a
job description. Return ONLY a single JSON object, no prose, no markdown fences,
matching this schema exactly:

{{
  "job_titles": [string, ...],
  "locations": [string, ...],
  "companies": [string, ...]
}}

Field-by-field rules:

"job_titles": 2-5 real job titles for this role, phrased exactly the way people
actually write them as their title on their LinkedIn profile/headline (LinkedIn
title conventions) -- not a generic paraphrase of the JD's own wording. This
feeds LinkedIn's own people-search API. Never invent a title that isn't a real,
commonly-held one.

"locations": real, well-known, searchable place names (cities, metro areas,
states, countries, or broad regions) that together capture EVERY way the JD
conveys where the candidate must be located. The JD may express this directly
(a named city/country) or indirectly (a US timezone like "East Coast" / "PST" /
"Central time zone", a global region like "APAC" or "EMEA", "must be within
commuting distance of X", etc.) -- there is no fixed list of accepted phrasings,
interpret whatever the JD actually says. When the JD names a broad region
("Nordics", "East Coast", "APAC"), expand it into several real, well-known,
representative places within it (e.g. major metro areas) rather than returning
the region name itself, since a location search needs concrete places. Only
produce this when the JD actually states a location constraint of some kind --
return an empty list if it states none. Never invent a location the JD gives no
basis for.

"companies": {min_n}-{max_n} REAL, currently operating company names (never
invented), chosen using this priority -- apply exactly one branch:
  (a) If the JD literally names the hiring company, list that company FIRST,
      followed by other real companies working in the same field/industry.
  (b) Else, if the JD describes its own product/industry (e.g. an "About Us"
      section) without naming itself, list real companies working in that
      same field.
  (c) Else, infer the hiring company's likely field from the JD's overall
      content -- responsibilities, domain vocabulary, stakeholders, tools
      mentioned -- and list real companies in that inferred field. If the JD
      truly gives no usable signal for this, return an empty list rather than
      inventing companies.
Prefer fewer, unmistakably-correct, same-field names over padding the list
with weak or irrelevant guesses just to hit {max_n}.

General rule: "job_titles" and "locations" must trace directly back to wording
or clearly-implied intent actually present in the JD. If unsure, leave it out /
use an empty list.
"""


def _call_openai_json_simple(system_prompt: str, user_content: str, model: str = "gpt-4o-mini") -> dict:
    """Calls the LLM once, with temperature 0 and strict JSON output.
    Raises on anything that isn't valid, parseable JSON -- callers must
    not fall back to guessing when this fails."""
    print(f"    [OpenAI] Calling {model} (prompt length: {len(user_content)} chars)...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    resp = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    # print("Resp",resp.json())
    usage = resp.json()["usage"]

    # 2. Print individual token metrics
    print(f"Prompt (Input) Tokens: {usage['prompt_tokens']}")
    print(f"Completion (Output) Tokens: {usage['completion_tokens']}")
    print(f"Total Tokens Used: {usage['total_tokens']}")

    print(f"    [OpenAI] Response received ({len(content)} chars). Parsing JSON...")
    parsed = json.loads(content)  # raises if the model didn't return clean JSON
    print(f"    [OpenAI] JSON parsed successfully.")
    return parsed


def extract_signals(job_description: str, company_min_n: int = 25, company_max_n: int = 50) -> dict:
    """The ONE OpenAI call for building the Unipile search payload. Returns
    job_titles, locations, companies -- all still just text, no ids yet."""
    print("\n[STEP 1] Extracting job titles, locations, and companies from JD via LLM...")
    prompt = _EXTRACTION_AND_COMPANY_PROMPT.format(min_n=company_min_n, max_n=company_max_n)
    data = _call_openai_json_simple(prompt, job_description)

    data.setdefault("job_titles", [])
    data.setdefault("locations", [])
    data.setdefault("companies", [])

    def _clean(values: list, cap: Optional[int] = None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for v in values:
            v = (v or "").strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
        return out[:cap] if cap else out

    data["job_titles"] = _clean(data["job_titles"])
    data["locations"] = _clean(data["locations"])
    data["companies"] = _clean(data["companies"], cap=company_max_n)

    print(f"[STEP 1] Done. job_titles={data['job_titles']}")
    print(f"[STEP 1] Done. locations={data['locations']}")
    print(f"[STEP 1] Done. companies ({len(data['companies'])} total)={data['companies']}")

    return data


# =============================================================================
# SECTION 2 -- resolve names to real Unipile ids (the ONLY source of ids)
# =============================================================================

def _unipile_search_parameters(keywords: str, param_type: str, limit: int = 10) -> list[dict]:
    """Hits Unipile's /search/parameters endpoint. Returns [] on no match --
    callers must treat that as 'drop this name', never as license to
    fabricate an id."""
    headers = {"X-API-KEY": UNIPILE_API_KEY, "accept": "application/json"}
    params = {
        "limit": limit,
        "keywords": keywords,
        "service": "RECRUITER",
        "type": param_type,
        "account_id": UNIPILE_ACCOUNT_ID,
    }
    resp = requests.get(UNIPILE_SEARCH_PARAMS_URL, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    print(f"    [Unipile params] type={param_type} keywords='{keywords}' -> {len(items)} match(es)")
    return items


def resolve_location_ids(location_names: list[str], per_location_limit: int = 5) -> list[str]:
    """Real LOCATION ids per location phrase, de-duplicated in order."""
    print(f"\n[STEP 2a] Resolving {len(location_names)} location name(s) to Unipile LOCATION ids...")
    ids: list[str] = []
    seen: set[str] = set()
    for loc in location_names:
        items = _unipile_search_parameters(loc, "LOCATION", limit=per_location_limit)
        for item in items[:per_location_limit]:
            _id = item.get("id")
            if _id and _id not in seen:
                seen.add(_id)
                ids.append(_id)
    print(f"[STEP 2a] Done. Resolved {len(ids)} unique location id(s).")
    return ids


def resolve_company_ids(company_names: list[str]) -> list[str]:
    """First real COMPANY match per name, in the same order as the input list."""
    print(f"\n[STEP 2b] Resolving {len(company_names)} company name(s) to Unipile COMPANY ids...")
    ids: list[str] = []
    seen: set[str] = set()
    for name in company_names:
        items = _unipile_search_parameters(name, "COMPANY", limit=1)
        if not items:
            print(f"    [Unipile params] no match for company '{name}', dropping it")
            continue
        _id = items[0].get("id")
        if _id and _id not in seen:
            seen.add(_id)
            ids.append(_id)
    print(f"[STEP 2b] Done. Resolved {len(ids)} unique company id(s) out of {len(company_names)} name(s).")
    return ids


# =============================================================================
# SECTION 3 -- filename helper + save-to-disk
# =============================================================================

def _slugify(text: str) -> str:
    """'Senior Vice President / Chief Customer Officer' ->
    'senior_vice_president_chief_customer_officer'"""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "role"


def save_payload_to_file(payload: dict, role_name: str, output_dir: str = ".") -> str:
    """Writes `payload` as pretty-printed JSON to
    '{output_dir}/{slugified role_name}_payload.json' and returns the path."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{_slugify(role_name)}_payload.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"    [save] Payload written to {path}")
    return path


def save_search_results_to_file(results: dict, role_name: str, output_dir: str = "./search_results") -> str:
    """Writes `results` as pretty-printed JSON to
    '{output_dir}/{slugified role_name}_candidates.json' and returns the path."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{_slugify(role_name)}_candidates.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in results.items() if not k.startswith("_")}, f, indent=2)
    print(f"    [save] Search results written to {path}")
    return path


# =============================================================================
# SECTION 4 -- assemble the Unipile search payload (role, location, company)
# =============================================================================

def build_payload(
        job_description: str,
        company_min_n: int = 25,
        company_max_n: int = 50,
        save_to_dir: str = "./payloads",
) -> tuple:
    """Always saves the final payload to
    '{save_to_dir}/{role_name}_payload.json' (role_name = the first
    title returned in `job_titles`, or 'role' if none were extracted).
    Pass save_to_dir=None only if you want to skip writing a file."""
    print("\n========== BUILD_PAYLOAD START ==========")
    signals = extract_signals(job_description, company_min_n, company_max_n)

    payload: dict = {"api": "recruiter", "category": "people"}

    if signals["job_titles"]:
        payload["role"] = [
            {
                "is_selection": True,
                "keywords": " OR ".join(signals["job_titles"]),
                "priority": "MUST_HAVE",
                "scope": "CURRENT",
            }
        ]
        print(f"[STEP 2] Role filter set: {payload['role'][0]['keywords']}")
    else:
        print("[STEP 2] No job titles extracted -- skipping role filter.")

    location_ids = resolve_location_ids(signals["locations"], per_location_limit=5)
    if location_ids:
        payload["location"] = [
            {"id": _id, "priority": "CAN_HAVE", "scope": "CURRENT"} for _id in location_ids
        ]

    company_ids = resolve_company_ids(signals["companies"])
    if company_ids:
        payload["current_company"] = [
            {"id": _id, "priority": "CAN_HAVE"} for _id in company_ids
        ]

    if save_to_dir is not None:
        role_name = signals["job_titles"][0] if signals["job_titles"] else "role"
        try:
            payload["_saved_to"] = save_payload_to_file(payload, role_name, save_to_dir)
        except OSError as e:
            payload["_saved_to"] = None
            print(f"WARNING: could not save payload to disk: {e}")
        payload["_role_name"] = role_name  # used by search_candidate() to name its own output file

    print("[STEP 2] Final Unipile search payload assembled:")
    print(json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}, indent=2))
    print("========== BUILD_PAYLOAD END ==========\n")

    return payload, signals["companies"], signals["locations"]


# =============================================================================
# SECTION 5 -- run the actual LinkedIn search via Unipile
# =============================================================================

def search_candidate(
        payload: dict,
        target_count: int = 500,
        save_to_dir: str = "./search_results",
) -> dict:
    """POSTs `payload` to Unipile's /api/v1/linkedin/search, following
    `cursor` across pages (100 per request) until it has collected
    `target_count` candidates OR runs out of real results."""
    print(f"\n[STEP 3] Searching LinkedIn via Unipile (target_count={target_count})...")
    role_name = payload.get("_role_name", "role")
    body = {k: v for k, v in payload.items() if not k.startswith("_")}

    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

    all_items: list = []
    total_count: Optional[int] = None
    cursor: Optional[str] = None
    page_limit = 100
    page_num = 0

    while len(all_items) < target_count:
        page_num += 1
        params: dict = {
            "limit": min(page_limit, target_count - len(all_items)),
            "account_id": UNIPILE_ACCOUNT_ID,
        }
        if cursor:
            params["cursor"] = cursor

        print(f"    [page {page_num}] Requesting up to {params['limit']} results "
              f"(cursor={'yes' if cursor else 'none'})...")
        resp = requests.post(UNIPILE_SEARCH_URL, headers=headers, params=params, json=body, timeout=60)
        resp.raise_for_status()
        page = resp.json()

        items = page.get("items", [])
        all_items.extend(items)
        total_count = page.get("paging", {}).get("total_count", total_count)
        cursor = page.get("cursor")

        print(f"    [page {page_num}] Got {len(items)} item(s). Running total: {len(all_items)}. "
              f"Reported total_count: {total_count}")

        if not items or not cursor:
            print(f"    [page {page_num}] No more items or no cursor returned -- stopping pagination.")
            break
        if total_count is not None and len(all_items) >= total_count:
            print(f"    [page {page_num}] Collected every reported match -- stopping pagination.")
            break

    results = {
        "items": all_items[:target_count],
        "total_count": total_count,
        "collected_count": len(all_items[:target_count]),
    }
    print(f"[STEP 3] Search complete. Collected {results['collected_count']} candidate(s) "
          f"(reported total available: {total_count}).")

    if save_to_dir is not None:
        results["_saved_to"] = save_search_results_to_file(results, role_name, save_to_dir)

    return results


# =============================================================================
# SECTION 6 -- STRICT CANDIDATE SELECTION
#
# This is the JD-agnostic evaluator: it works for any job description, in
# any field. Title (Rule 1) and location (Rule 4) are enforced
# deterministically in code -- never trusted from the LLM's own output.
# Company fit (Rule 2) and domain fit (Rule 3) genuinely need semantic
# reading, so they're LLM-judged, with a deterministic keyword-based
# fallback that resolves any "UNVERIFIED" domain call into a final PASS/FAIL
# so nothing is ever left for a human to review -- output is always binary.
# =============================================================================

def _call_openai_json(
    system_prompt: str,
    user_content: str,
    model: str = "gpt-4o",
    max_retries: int = 4,
    base_backoff_seconds: float = 15.0,
) -> dict:
    """One call to the LLM, temperature 0, strict JSON output. Retries on
    HTTP 429 with exponential backoff. Raises on any other error, or if the
    model doesn't return clean JSON -- callers must not silently guess.

    Uses gpt-4o by default, not gpt-4o-mini: mini was measurably too lenient
    on nuanced rule application (hybrid titles, seniority mismatches slipping
    through) in testing.
    """
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }

    attempt = 0
    while True:
        attempt += 1
        resp = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=120)

        if resp.status_code == 429:
            if attempt >= max_retries:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else base_backoff_seconds * (2 ** (attempt - 1))
            print(f"    [rate limited] waiting {wait_seconds:.0f}s (attempt {attempt}/{max_retries})...")
            time.sleep(wait_seconds)
            continue


        resp.raise_for_status()
        usage = resp.json()["usage"]

        # 2. Print individual token metrics
        print(f"Prompt (Input) Tokens: {usage['prompt_tokens']}")
        print(f"Completion (Output) Tokens: {usage['completion_tokens']}")
        print(f"Total Tokens Used: {usage['total_tokens']}")
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)  # raises if not clean JSON


_JD_EXTRACTION_PROMPT = """You are an information extractor for a recruiting pipeline. You will be
given a JOB DESCRIPTION for ANY role in ANY industry or function. Return
ONLY a single JSON object, no prose, no markdown fences, matching this
schema exactly:

{
  "target_titles": [string, ...],
  "location_requirement": string or null,
  "location_keywords": [string, ...],
  "industry_context": string,
  "domain_keywords": [string, ...]
}

Field-by-field rules:

"target_titles": the exact job title(s) this JD is hiring for, phrased
EXACTLY as the JD phrases them (or, if the JD gives multiple acceptable
title variants for the same single role, e.g. "Senior Vice President /
Chief Customer Officer", list each one separately). Do not paraphrase,
generalize, or invent alternate phrasings. Do not include seniority
levels below what the JD asks for, and do not include hybrid titles that
combine this function with another (that is for the recruiter to judge
case-by-case, not for you to pre-decide here).

"location_requirement": a one-sentence plain-English restatement of
whatever location constraint the JD states (city, region, country, time
zone, "remote - no constraint", etc.), or null if the JD states no
location constraint at all.

"location_keywords": a list of short strings that would each, if found as
a substring of a candidate's location field, satisfy the JD's location
requirement. Expand any broad region or time-zone phrase (e.g. "East
Coast", "APAC", "Central time zone") into the well-known concrete
place-names it implies (major metro areas, states, countries) --
location fields on candidate profiles name specific places, not regions,
so this list must too. Return an empty list if location_requirement is
null.

"industry_context": 1-3 sentences describing the field, industry, product
type, customer base, or business model this JD operates in, based on
whatever the JD says about itself, its product, its customers, or its
domain vocabulary. This is used later to judge whether a candidate's
employer and experience are in a comparable field -- write it so it's
useful for that comparison, not as a generic industry label.

"domain_keywords": 5-12 short phrases (2-4 words each) that would each
plausibly appear in a JOB TITLE (not a full sentence) of someone who
genuinely does the function this JD requires. Think of this as: "what
words would show up in this person's title history at various companies
if they truly have this background?" Include the JD's own core function
terms plus realistic synonyms different companies use for the same
function (e.g. for a JD about customer success leadership: "customer
success", "customer support", "client success", "customer experience",
"post-sales", "account management", "renewals", "retention"; for a JD
about backend engineering: "backend engineer", "software engineer",
"platform engineer", "distributed systems", "infrastructure engineer").
This list is used to check a candidate's OWN job title history (not
prose descriptions) for corroborating evidence when description text is
sparse, so favor short, title-realistic phrases over full sentences or
overly narrow jargon.

General rule: everything here must trace directly back to wording or
clearly-implied intent actually present in the JD. If the JD doesn't
state something, leave it out / use null / use an empty list. Never
invent a title, location, or industry claim the JD gives no basis for.
"""


def extract_jd_requirements(job_description: str) -> dict:
    """The one LLM call that makes the evaluator JD-agnostic. Works the
    same whether the JD is for a CCO, a backend engineer, a regional sales
    director, or anything else."""
    print("[extract_jd_requirements] Extracting titles/location/industry from JD...")
    data = _call_openai_json(_JD_EXTRACTION_PROMPT, job_description)

    data.setdefault("target_titles", [])
    data.setdefault("location_requirement", None)
    data.setdefault("location_keywords", [])
    data.setdefault("industry_context", "")
    data.setdefault("domain_keywords", [])

    print(f"  target_titles: {data['target_titles']}")
    print(f"  location_requirement: {data['location_requirement']}")
    print(f"  location_keywords: {data['location_keywords']}")
    print(f"  industry_context: {data['industry_context']}")
    print(f"  domain_keywords: {data['domain_keywords']}")
    return data


def title_matches(candidate_title: str, target_titles: list[str]) -> bool:
    """Generic substring-based title check. A candidate's current title
    passes Rule 1 only if at least one target title appears as a
    CONTIGUOUS substring of it (case/whitespace-insensitive). This works
    the same for any JD: catches hybrid titles ("Chief Strategy and
    Customer Officer" for a target of "Chief Customer Officer") and
    lower-seniority titles ("Vice President, X" for a target of "Senior
    Vice President") without any role-specific logic."""
    if not target_titles:
        return True
    normalized_candidate = re.sub(r"\s+", " ", candidate_title or "").strip().lower()
    for target in target_titles:
        normalized_target = re.sub(r"\s+", " ", target or "").strip().lower()
        if normalized_target and normalized_target in normalized_candidate:
            return True
    return False


def location_matches(candidate_location: str, location_keywords: list[str]) -> bool:
    """Same generic substring approach for location. If the JD stated no
    location constraint, location_keywords is empty and everything passes."""
    if not location_keywords:
        return True
    normalized_candidate = re.sub(r"\s+", " ", candidate_location or "").strip().lower()
    for kw in location_keywords:
        normalized_kw = re.sub(r"\s+", " ", kw or "").strip().lower()
        if normalized_kw and normalized_kw in normalized_candidate:
            return True
    return False


# Generic keywords that mark a work_experience entry as a SIDE role rather
# than someone's primary job -- board seats, advisory positions, angel/VC
# investing, mentorship. These often have no "end" date, so naively picking
# "whichever open-ended entry has the latest start date" can easily surface
# a board seat instead of someone's actual day job.
_SIDE_ROLE_KEYWORDS = [
    "board member", "board observer", "advisory board", "executive advisor",
    "advisor", "mentor", "general partner", "limited partner",
    "venture partner", "investor", "angel investor",
]


def _is_side_role(role_text: str) -> bool:
    text = (role_text or "").lower()
    return any(kw in text for kw in _SIDE_ROLE_KEYWORDS)


def get_current_role(work_experience: list) -> Optional[dict]:
    """Deterministically picks the candidate's real CURRENT primary job from
    their work_experience array, in code -- this is ground truth, never
    something we ask the LLM to identify and then trust blindly.

    Logic:
    1. An entry is a candidate for "current" if it has no "end" date.
    2. Among those, prefer ones that do NOT look like a side role.
    3. Among what's left, pick the one with the most recent "start" date.
    4. If EVERY open-ended entry looks like a side role, fall back to the
       most recent open-ended entry anyway rather than returning nothing.

    Returns None only if there are no work_experience entries at all.
    """
    if not work_experience:
        return None

    def start_key(w: dict) -> tuple:
        s = w.get("start") or {}
        return (s.get("year", 0), s.get("month", 0))

    open_roles = [w for w in work_experience if not w.get("end")]
    if not open_roles:
        return max(work_experience, key=start_key)

    primary_candidates = [w for w in open_roles if not _is_side_role(w.get("role", ""))]
    pool = primary_candidates if primary_candidates else open_roles
    return max(pool, key=start_key)


def domain_keyword_evidence_found(
    resolved_current_role,
    recent_prior_roles: list,
    domain_keywords: list,
) -> Optional[str]:
    """Deterministic, code-level check of whether any of the candidate's own
    job TITLES (current or recent prior) contain one of the JD's
    domain_keywords as a substring. Used to resolve an "UNVERIFIED" domain
    call into a final PASS/FAIL without relying on the LLM's inconsistent
    handling of thin/empty descriptions. Returns the matched keyword (for
    the audit trail) if found, else None."""
    if not domain_keywords:
        return None

    titles_to_check = []
    if resolved_current_role:
        titles_to_check.append(resolved_current_role.get("role", ""))
    titles_to_check.extend(w.get("role", "") for w in recent_prior_roles)

    for title in titles_to_check:
        normalized_title = re.sub(r"\s+", " ", title or "").strip().lower()
        for kw in domain_keywords:
            normalized_kw = re.sub(r"\s+", " ", kw or "").strip().lower()
            if normalized_kw and normalized_kw in normalized_title:
                return kw
    return None


_STRICT_SELECTION_SYSTEM_PROMPT = """You are an extremely strict, harsh technical recruiter screen. You will be
given: (1) structured JD REQUIREMENTS already extracted from a job
description (target titles, location requirement, industry context), and
(2) a list of CANDIDATES (each with a headline, location, and
work_experience). This same screen is reused across many different job
descriptions in many different fields -- do not assume anything about what
industry or function this particular JD is for beyond what
JD REQUIREMENTS tells you.

This is a rejection-biased screen: when in doubt, REJECT. Do not be
generous. Do not give credit for "close enough."

Each candidate includes a `resolved_current_role` field -- this has ALREADY
been determined for you, in code, as their real current primary job (it
accounts for end-dates and filters out board seats / advisory / investor
side-roles that might otherwise look "current"). Use `resolved_current_role`
as ground truth for current title and current company. Do NOT re-derive
"current" yourself from the raw work_experience array, and do NOT use the
candidate's "headline" field as evidence of current title, company, or
responsibilities -- headlines are self-written, often stale, aspirational,
or describe a side role instead of the real current job.

You may still use `recent_prior_roles` (up to a handful of the
candidate's most recent prior jobs, already filtered to exclude board/
advisory/investor side-roles) to look for a consistent pattern of the
required domain when `resolved_current_role`'s own description is thin.
See Rule 3 below.

Rule 1 (title) and Rule 4 (location) are checked deterministically in code
using `resolved_current_role` and the candidate's `location` field -- you
do not need to evaluate them. Just copy `resolved_current_role`'s `role`
and `company` values, and the candidate's `location` value, faithfully
into your output so they can be displayed. Focus your actual judgment on
Rules 2 and 3 below.

============================================================
RULE 2 -- COMPANY / INDUSTRY FIT
============================================================
Using JD REQUIREMENTS' industry_context, judge whether the candidate's
CURRENT employer is a real, currently operating company or organization
that plausibly competes or operates in the same field/business model
described. REJECT if the current employer is in a clearly different
industry, customer base, or business model than industry_context
describes, even if other rules pass. Do not give credit for superficial
similarity (e.g. "both involve customers," "both involve technology") --
the actual field, product type, and customer base must be comparable.

============================================================
RULE 3 -- DOMAIN FIT (strictest rule; apply maximum rigor)
============================================================
Read the CURRENT role's "description" field (and industry field) -- not
the title, not the headline -- to determine what the candidate actually
does day to day. If the current role's own description is thin, empty, or
generic, look at the 1-2 most recent PRIOR roles' descriptions to see
whether there's a consistent, demonstrated pattern of the same function --
a title that just changed is not itself proof the underlying work changed
too.

Compare the demonstrated responsibilities against industry_context and
whatever functional focus the target_titles imply. Mark this rule:
- "PASS" only if you can point to specific text in a description field
  that directly demonstrates the required function.
- "FAIL" if the demonstrated work is clearly a different function
  (regardless of title match) -- e.g. the title says the target role but
  the actual described responsibilities are sales quota attainment,
  people/HR management, financial reporting, legal/compliance, generic
  operations, or a different customer base/vertical than industry_context
  describes.
- "UNVERIFIED" if there simply isn't enough description text (current or
  recent prior roles) to prove the function either way. UNVERIFIED is NOT
  a pass -- treat it as a rejection with a clear note that the reason is
  insufficient evidence, not a demonstrated mismatch.

Adjacent or transferable experience alone is NOT sufficient for a PASS.
The specific function implied by target_titles / industry_context must be
directly, demonstrably present in a description field.

If industry_context or target_titles describe a role spanning MULTIPLE
named sub-functions (e.g. "leads pre-sales and customer success teams"),
do not require the candidate to separately prove every named
sub-function. Require clear evidence of at least the PRIMARY/dominant
function the role is built around; strong, demonstrated depth in one of
the stated sub-functions is sufficient for rule_3 = PASS on its own,
since real executives specialize and a hiring team asking for someone to
lead two related functions together is usually willing to accept deep
strength in one plus plausible adjacency to the other, not equal proof of
both independently.

When judging whether prior-role TITLES form a consistent ladder (Example
F below), treat commonly-interchangeable organizational labels for
essentially the same function as equivalent, not as a broken pattern --
e.g. "Customer Success," "Customer Support," and "Client Success" are
different companies' names for closely overlapping post-sale customer
organizations, and should count toward the same ladder rather than being
treated as unrelated functions just because the exact word differs.

Two specific traps to actively guard against (see Examples D and E below
for worked cases):
- KEYWORD CONTAMINATION: a description can mention an industry-relevant
  word (e.g. "SaaS", "cloud", "enterprise software", "AI") while
  describing a completely different FUNCTION than industry_context
  requires (e.g. internal IT modernization/implementation rather than
  customer-facing sales, success, or delivery). Never treat a matching
  keyword by itself as evidence -- verify the actual described activity
  matches the required function, not just that a relevant noun appears
  somewhere in the sentence.
- CIRCULAR EVIDENCE: a description that just restates the title in
  sentence form (e.g. title "VP of X", description "Leading X") contains
  zero new information and must be treated as equivalent to an EMPTY
  description -- fall back to prior-role evidence per Example C's logic,
  and if that's also absent, the verdict is UNVERIFIED, not PASS.

============================================================
WORKED EXAMPLES (illustrating failure patterns -- these are generic
patterns, not specific to any one industry; apply the same reasoning
regardless of what field the actual JD REQUIREMENTS describes)
============================================================

Example A -- hybrid title:
  target_titles: ["Director of Platform Engineering"]
  candidate current_title: "Director of Platform Strategy and Engineering
  Operations"
  -> This inserts extra scope ("Strategy and ... Operations") between the
  target words. Even though "Director" and "Engineering" both appear, this
  is a different, broader/hybrid role, not the same job. Treat rule_3 with
  suspicion here too, since a broadened title often means broadened (and
  thinner) actual engineering-specific responsibility -- check the
  description carefully rather than assuming it still matches.

Example B -- domain looks right, function doesn't:
  target_titles: ["VP of Customer Success"]
  candidate current_title: "VP of Customer Success"
  candidate current description: "Own the enterprise sales pipeline for
  the Central region, carrying a $12M annual quota, running the full
  sales cycle from prospecting to close."
  -> Title matches exactly, but the actual described work is quota-
  carrying new-business sales, not post-sale customer success (adoption,
  retention, renewals, expansion). Title inflation/mislabeling happens
  often on LinkedIn -- always verify against the description. rule_3 =
  FAIL despite rule_1 passing.

Example C -- thin evidence, don't assume:
  target_titles: ["Chief Marketing Officer"]
  candidate current_title: "Chief Marketing Officer"
  candidate current description: "" (empty)
  candidate prior role description: "Led paid acquisition campaigns and
  managed the marketing budget for a 15-person team."
  -> The current role has no description to verify against, but the prior
  role shows a consistent, genuine marketing background. This is enough
  supporting pattern for rule_3 = PASS, citing the prior role's
  description as the evidence, since it establishes she was already doing
  this function immediately before the current, presumably similar, role.
  (Contrast with a case where even the prior roles show an unrelated
  function -- that would stay UNVERIFIED or FAIL, not get the benefit of
  the doubt.)

Example D -- keyword contamination (a very common trap, watch for this
specifically):
  industry_context: "a B2B SaaS company selling customer success and
  pre-sales software to enterprise clients"
  target_titles: ["Senior Vice President"]
  candidate current description: "Oversee large-scale ERP, cloud, and SaaS
  implementations. Lead enterprise-wide digital transformation initiatives
  to modernize internal business processes and technology infrastructure."
  -> The word "SaaS" appears in this description, but read what it's
  actually describing: this person implements/modernizes internal systems
  and runs transformation programs -- that is IT strategy/implementation
  consulting, not customer success or pre-sales. The mere presence of an
  industry keyword ("SaaS", "cloud", "AI", "enterprise software") inside a
  description is NEVER by itself evidence of domain match. You must
  verify that the FUNCTION described (what the person actually does day
  to day: modernizing internal systems vs. leading customer-facing
  success/pre-sales teams) matches industry_context's function, not just
  that a matching noun appears somewhere in the text. rule_3 = FAIL.

Example E -- circular / restated-title description (treat as no evidence,
not as confirming evidence):
  candidate current_title: "Senior Vice President, Global SMB Solutions"
  candidate current description: "Leading Global SMB Solutions."
  -> This description adds no information beyond the title itself -- it
  is the title restated as a sentence, not a description of actual
  responsibilities, outcomes, or day-to-day work. Do not treat a
  restated-title description as evidence that the role matches
  industry_context; treat it exactly as you would an empty description
  (fall back to checking prior roles per Example C's logic; if those are
  also absent or unrelated, rule_3 = UNVERIFIED, not PASS).

Example F -- a genuine title ladder counts as evidence even with no prose
descriptions (do not confuse this with Example E):
  target function: customer success leadership
  candidate's recent_prior_roles, oldest to newest, all with empty
  description fields: "Director of Customer Success" -> "Senior Director
  of Customer Success" -> "Vice President, Head of Customer Success" ->
  current: "Senior Vice President, Customer Success"
  -> Unlike Example E (one title, no track record behind it), this is a
  multi-year, multi-role, sequential progression through the SAME named
  function, each a real promotion-level title change. That pattern is
  itself real evidence of the function, even with no prose text to quote
  -- a person does not get promoted through 4 consecutive
  "Customer Success" titles at real companies by accident. rule_3 = PASS,
  with the reason citing the title sequence itself as the evidence (e.g.
  "no description text available, but the candidate's title history shows
  a consistent 4-role progression through Customer Success roles from
  Director to SVP, which is sufficient evidence of genuine domain
  experience"). Do NOT require prose description text to exist before
  crediting a clear multi-role ladder like this -- many real, strong
  candidates simply have sparse LinkedIn descriptions, and rejecting them
  on that basis alone is a false negative, not appropriate strictness.

============================================================
OUTPUT FORMAT
============================================================
Return ONLY valid JSON, no markdown, no prose outside the JSON. Return a
JSON object with this exact schema:

{
  "results": [
    {
      "index": 0,
      "candidate": "candidate full name",
      "current_title": "their actual current title, from work_experience",
      "current_company": "their actual current employer",
      "location": "their location field, verbatim",
      "rule_2_company": "PASS" | "FAIL",
      "rule_3_domain": "PASS" | "FAIL" | "UNVERIFIED",
      "reason": "One or two sentences that QUOTE OR CLOSELY PARAPHRASE the
                  specific work_experience description text that drove the
                  rule_2 and rule_3 verdicts. If rule_3 is UNVERIFIED,
                  explicitly say so and say what evidence was missing --
                  never write a generic 'responsibilities align' sentence
                  with nothing concrete behind it."
    }
  ]
}

Every candidate in the input must appear exactly once in "results", in the
same order given. The "index" field must be copied exactly from the
candidate's given index -- never invent or guess it. Do not include a
"selected" field -- final selection is decided in code after this, based
on rule_2 and rule_3 plus the already-checked rule_1/rule_4.
"""


def _prep_candidate_for_llm(candidate: dict, max_prior_roles: int = 4) -> dict:
    """Strips a raw candidate profile down to just what the prompt needs,
    and includes a `resolved_current_role` computed deterministically in
    code (see get_current_role) so the LLM is told, not asked, which entry
    is current.

    Does NOT forward the candidate's full raw work_experience array. Some
    real profiles (active investors, board members, advisors) carry 40-60+
    entries, mostly side-role noise. Instead sends only
    `resolved_current_role` plus up to `max_prior_roles` of the candidate's
    most recent NON-side-role prior entries -- exactly the evidence Rule 3
    needs, nothing else (keeps token count down and signal-to-noise high)."""
    we_keys = ["company", "industry", "location", "role", "start", "end", "description"]
    work_experience = candidate.get("work_experience") or []
    current = get_current_role(work_experience)

    def start_key(w: dict) -> tuple:
        s = w.get("start") or {}
        return (s.get("year", 0), s.get("month", 0))

    prior_pool = [
        w for w in work_experience
        if w is not current and not _is_side_role(w.get("role", ""))
    ]
    recent_prior_roles = sorted(prior_pool, key=start_key, reverse=True)[:max_prior_roles]

    return {
        "name": candidate.get("name"),
        "headline": candidate.get("headline"),
        "location": candidate.get("location"),
        "resolved_current_role": (
            {k: current[k] for k in we_keys if k in current} if current else None
        ),
        "recent_prior_roles": [
            {k: w[k] for k in we_keys if k in w}
            for w in recent_prior_roles
        ],
    }


def select_matching_candidates(
    job_description: str,
    candidates: list,
    batch_size: int = 15,
    model: str = "gpt-4o",
    only_selected: bool = True,
) -> Optional[list]:
    """
    1. Extracts title/location/industry requirements from `job_description`
       (works for any JD in any field).
    2. Deterministically checks Rule 1 (title) and Rule 4 (location) in code
       for every candidate -- no LLM judgment involved, fully reproducible.
    3. Sends every candidate to the LLM (in batches) for Rule 2 (company fit)
       and Rule 3 (domain fit), which genuinely require semantic reading.
    4. Resolves any "UNVERIFIED" Rule 3 verdict deterministically via
       domain_keyword_evidence_found, so the final `rule_3_domain` is always
       PASS or FAIL -- never left ambiguous for a human to review.
    5. Merges all four: a candidate is `selected` only if ALL FOUR rules pass.

    Returns a list of result dicts, one per candidate, in original order.
    If `only_selected=True` (default), only passing candidates are returned.

    Returns None if any LLM batch fails to parse -- treat that as "retry",
    not "treat as empty result".
    """
    print(f"[select_matching_candidates] {len(candidates)} candidate(s) total.")
    jd_requirements = extract_jd_requirements(job_description)
    target_titles = jd_requirements["target_titles"]
    location_keywords = jd_requirements["location_keywords"]
    domain_keywords = jd_requirements["domain_keywords"]

    total = len(candidates)
    num_batches = math.ceil(total / batch_size) if total else 0
    all_results: list = []

    for batch_num in range(num_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, total)
        chunk = candidates[start:end]

        prepped_chunk = [_prep_candidate_for_llm(c) for c in chunk]
        indexed_chunk = [
            {"index": i, **prepped}
            for i, prepped in enumerate(prepped_chunk)
        ]

        user_content = f"""
JD REQUIREMENTS:
{json.dumps(jd_requirements, ensure_ascii=False, indent=2)}


CANDIDATES:
{json.dumps(indexed_chunk, ensure_ascii=False, indent=2)}
"""

        print(f"  [batch {batch_num + 1}/{num_batches}] sending {len(chunk)} candidate(s) to {model}...")
        try:
            parsed = _call_openai_json(_STRICT_SELECTION_SYSTEM_PROMPT, user_content, model=model)
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            print(f"  [batch {batch_num + 1}/{num_batches}] FAILED: {e}")
            return None

        batch_results = parsed.get("results", [])

        for r in batch_results:
            idx = r.get("index")
            if not (isinstance(idx, int) and 0 <= idx < len(chunk)):
                r["_index_flag"] = "invalid_or_missing_index"
                r["rule_1_title"] = "FAIL"
                r["rule_4_location"] = "FAIL"
                r["selected"] = False
                continue

            source = chunk[idx]
            prepped = prepped_chunk[idx]
            r["_source_id"] = source.get("id")
            r["_source_profile_url"] = source.get("profile_url") or source.get("public_profile_url")

            # Deterministic Rule 1 & Rule 4, computed ONLY from our own
            # code-resolved data -- never from anything the LLM echoed back.
            resolved = prepped.get("resolved_current_role") or {}
            ground_truth_title = resolved.get("role", "")
            ground_truth_location = source.get("location", "")

            r["current_title"] = ground_truth_title
            r["current_company"] = resolved.get("company", r.get("current_company"))
            r["location"] = ground_truth_location

            r["rule_1_title"] = "PASS" if title_matches(ground_truth_title, target_titles) else "FAIL"
            r["rule_4_location"] = "PASS" if location_matches(ground_truth_location, location_keywords) else "FAIL"

            # Resolve rule_3 == "UNVERIFIED" deterministically instead of
            # leaving it ambiguous. Title-history keyword evidence -> PASS.
            # No evidence anywhere -> FAIL (rejection-biased default).
            if r.get("rule_3_domain") == "UNVERIFIED":
                matched_kw = domain_keyword_evidence_found(
                    resolved, prepped.get("recent_prior_roles") or [], domain_keywords
                )
                if matched_kw:
                    r["rule_3_domain"] = "PASS"
                    r["reason"] = (
                        f"{r.get('reason', '')} [Auto-resolved from UNVERIFIED: candidate's own "
                        f"title history contains '{matched_kw}', matching the JD's required domain.]"
                    ).strip()
                else:
                    r["rule_3_domain"] = "FAIL"
                    r["reason"] = (
                        f"{r.get('reason', '')} [Auto-resolved from UNVERIFIED to FAIL: no domain "
                        f"keyword evidence found anywhere in the candidate's title history either.]"
                    ).strip()

            r["selected"] = (
                r["rule_1_title"] == "PASS"
                and r.get("rule_2_company") == "PASS"
                and r.get("rule_3_domain") == "PASS"
                and r["rule_4_location"] == "PASS"
            )

        all_results.extend(batch_results)
        n_selected = sum(1 for r in batch_results if r.get("selected"))
        print(f"  [batch {batch_num + 1}/{num_batches}] done -- {n_selected} selected of {len(batch_results)}")

    if len(all_results) != total:
        print(f"WARNING: got {len(all_results)} result(s) back for {total} candidate(s) sent -- "
              f"some candidates may be missing from the output. Do not treat this run as complete.")

    if only_selected:
        return [r for r in all_results if r.get("selected")]
    return all_results


# =============================================================================
# SECTION 7 -- Unipile recruiter project + pipeline add
# =============================================================================

def create_unipile_recruiter_project(
    project_name: str,
    visibility: str = "PRIVATE",
):
    print(f"\n[STEP 6] Creating Unipile recruiter project '{project_name}' (visibility={visibility})...")
    account_id = UNIPILE_PROJECTS_ACCOUNT_ID
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
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code in (200, 201):
            response = response.json()
            project_id = response.get("project_id")
            print(f"[STEP 6] Project created successfully. project_id={project_id}")
            return project_id

        print(f"❌ Unipile project creation failed: {response.status_code} {response.text[:300]}")
        return None

    except requests.RequestException as e:
        print(f"❌ Unipile project creation request error: {e}")
        return None


def add_candidate_to_pipeline(candidate_id: str, hiring_project_id: str, stage: str = "UNCONTACTED") -> bool:
    """POSTs a single candidate onto a Unipile recruiter project pipeline.
    Never raises -- returns True/False so one bad candidate doesn't kill
    the whole background loop."""
    url = f"{UNIPILE_BASE_URL}/api/v1/linkedin/user/{candidate_id}"
    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }
    body = {
        "api": "recruiter",
        "action": "addCandidateToPipeline",
        "stage": stage,
        "hiring_project_id": hiring_project_id,
        "account_id": UNIPILE_ACCOUNT_ID,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.ok:
            print(f"    [pipeline add] {candidate_id} -> added (stage={stage})")
            return True
        print(f"    [pipeline add] {candidate_id} -> FAILED {resp.status_code}: {resp.text[:300]}")
        return False
    except requests.RequestException as e:
        print(f"    [pipeline add] {candidate_id} -> request error: {e}")
        return False


def add_candidates_to_pipeline_with_delay(
    candidates: list,
    hiring_project_id: str,
    min_score: float = 70,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
) -> None:
    """Adds each eligible candidate (score >= min_score) to the pipeline,
    `delay_seconds` apart. `candidates` must have "id" and "score" keys --
    see `_adapt_selected_for_pipeline` for bridging the selector's output
    shape (which uses "_source_id" and has no score) into this."""
    eligible = [c for c in candidates if (c.get("score") or 0) >= min_score]
    print(f"\n[STEP 7] Adding {len(eligible)} candidate(s) with score >= {min_score} "
          f"to pipeline (project_id={hiring_project_id}), {delay_seconds}s apart...")

    for i, cand in enumerate(eligible):
        cid = cand.get("id")
        name = cand.get("candidate", "unknown")
        if not cid:
            print(f"    [pipeline add] skipping '{name}' -- no id")
            continue

        add_candidate_to_pipeline(cid, hiring_project_id, stage=stage)

        if i < len(eligible) - 1:
            time.sleep(delay_seconds)

    print(f"[STEP 7] Done adding candidates to pipeline.")


def start_pipeline_add_in_background(
    candidates: list,
    hiring_project_id: str,
    min_score: float = 70,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
) -> threading.Thread:
    """Kicks off add_candidates_to_pipeline_with_delay on a daemon thread
    and returns immediately, so main_function can hand its result back to
    the caller right away while the adds happen in the background."""
    thread = threading.Thread(
        target=add_candidates_to_pipeline_with_delay,
        args=(candidates, hiring_project_id),
        kwargs={"min_score": min_score, "delay_seconds": delay_seconds, "stage": stage},
        daemon=True,
    )
    thread.start()
    print(f"[STEP 7] Background pipeline-add thread started (thread_id={thread.ident}).")
    return thread


def _adapt_selected_for_pipeline(selected: list) -> list:
    """select_matching_candidates() returns dicts with "_source_id" and no
    "score" field (selection is already a clean pass/fail, nothing left to
    threshold on). The pipeline-add functions above were written for an
    older evaluator's shape ("id" + 0-100 "score"). This adapter copies
    "_source_id" -> "id" and stamps score=100 on every candidate here
    (every one already passed all 4 rules, so there's nothing to filter
    further), letting the existing pipeline functions work unchanged."""
    adapted = []
    for r in selected:
        c = dict(r)  # don't mutate the caller's list
        c["id"] = r.get("_source_id")
        c["score"] = 100
        if not c["id"]:
            print(f"    [pipeline adapter] WARNING: '{r.get('candidate')}' has no _source_id -- "
                  f"will be skipped when adding to pipeline.")
        adapted.append(c)
    return adapted


# =============================================================================
# SECTION 8 -- main entry point
# =============================================================================

def run_search_and_pipeline(jd: str, project_name: str) -> dict:
    """
    Runs the full pipeline end to end, with nothing written to disk: build
    search payload -> search LinkedIn -> evaluate candidates against the
    JD -> create a Unipile recruiter project -> add the selected candidates
    to that project's pipeline, 20 seconds apart, SYNCHRONOUSLY (this
    function blocks until every add has actually completed before it
    returns).

    Returns:
    {
        "selected": [...],
        "rejected": [...],
        "project_id": str|None,
    }

    Note on timing: with candidates added 20 seconds apart, this call takes
    roughly (number of selected candidates - 1) * 20 seconds on top of the
    search/evaluation time. That's intentional here -- running this
    synchronously means the pipeline adds can never be silently lost by the
    script exiting early (which is what happened with the background-
    thread version), at the cost of the function blocking until it's truly
    done.
    """
    print("\n################## PIPELINE START ##################")
    print(f"Project name: {project_name}")
    print(f"JD length: {len(jd)} chars")

    payload, companies_list, locations_list = build_payload(jd, save_to_dir=None)
    result = search_candidate(payload, save_to_dir=None)

    all_evaluated = select_matching_candidates(
        jd, result["items"], batch_size=15, model="gpt-4o", only_selected=False
    )
    if all_evaluated is None:
        print("❌ Candidate evaluation failed (a batch didn't parse) -- aborting before "
              "project creation or pipeline adds. Nothing was created.")
        return {"selected": [], "rejected": [], "project_id": None}

    selected = [r for r in all_evaluated if r["selected"]]
    rejected = [r for r in all_evaluated if not r["selected"]]

    print(f"\n{len(selected)} candidate(s) selected out of {len(all_evaluated)} evaluated:")
    for r in selected:
        print(f"- {r['candidate']} | {r['current_title']} @ {r['current_company']} | {r['location']}")
        print(f"    reason: {r['reason']}")

    project_id = create_unipile_recruiter_project(project_name)

    if project_id is None:
        print("⚠️  Project creation failed -- skipping pipeline adds. "
              "`selected`/`rejected` are still returned so nothing found is lost.")
    elif not selected:
        print("ℹ️  No candidates selected -- nothing to add to the pipeline.")
    else:
        adapted = _adapt_selected_for_pipeline(selected)
        print(f"[PIPELINE] Adding {len(adapted)} candidate(s) to project_id={project_id}, "
              f"20s apart, synchronously -- this will take roughly "
              f"{(len(adapted) - 1) * 20}s...")
        add_candidates_to_pipeline_with_delay(
            adapted,
            project_id,
            min_score=70,  # vestigial here -- every adapted candidate is stamped score=100
            delay_seconds=20.0,
            stage="UNCONTACTED",
        )
        print(f"[PIPELINE] Done -- all {len(adapted)} candidate(s) have been processed.")

    print("################## PIPELINE END ##################\n")

    return {
        "selected": selected,
        "rejected": rejected,
        "project_id": project_id,
    }