from __future__ import annotations

import os
import re
import json
import time
import math
from typing import Optional
import threading

import requests

from dotenv import load_dotenv
import os

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"
UNIPILE_BASE_URL = "https://api40.unipile.com:17060"

UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="
UNIPILE_PROJECTS_ACCOUNT_ID = "acc_01m09sdddhfetrdm9tzcbqncv1"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UNIPILE_SEARCH_PARAMS_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search/parameters"
UNIPILE_SEARCH_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search"

# ---------------------------------------------------------------------------
# The single LLM call: extraction (titles + locations) + company shortlist
# + core function keywords (NEW -- used later by the code-level function gate)
# ---------------------------------------------------------------------------

_EXTRACTION_AND_COMPANY_PROMPT = """You are an information extractor and sourcing assistant. You will be given a
job description. Return ONLY a single JSON object, no prose, no markdown fences,
matching this schema exactly:

{{
  "job_titles": [string, ...],
  "locations": [string, ...],
  "companies": [string, ...],
  "core_function_keywords": [string, ...],
  "must_have_requirements": [
    {{"requirement": string, "keywords": [string, ...]}},
    ...
  ]
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

"core_function_keywords": 4-8 keywords/phrases describing the DAY-TO-DAY FUNCTION
this role actually performs (e.g. "customer success", "pre-sales team",
"account management", "customer engagement strategy", "renewals", "upsell
motion"). This is about FUNCTION, not industry, not seniority, and not company
type. It is used later as an independent, code-level sanity check to make sure
a candidate isn't from an unrelated function (e.g. procurement, engineering,
finance) that merely happens to share a similar title, seniority level, or
employer. EVERY entry MUST be a real multi-word phrase (2-4 words) actually
usable as a substring match against resume text -- never a single generic
word like "customer", "strategy", "growth", "leadership", or "management" on
its own, since single generic words match almost any executive profile and
make this check useless. Ground every phrase directly in the JD's actual
responsibilities section -- never invent a function the JD doesn't describe.

"must_have_requirements": 3-6 objects, each describing ONE specific, named,
checkable requirement or responsibility from the JD (not the whole role in
general -- one distinct, separately-checkable capability per object, e.g.
"led a customer success team", "led a pre-sales team", "oversaw professional
services delivery", "SaaS company experience"). For each object:
  - "requirement": a short human-readable label for that one requirement.
  - "keywords": 2-5 short phrases (2-4 words each, lowercase-friendly) that
    would plausibly appear in a resume/LinkedIn profile as evidence of that
    SPECIFIC requirement being met (e.g. for "led a customer success team":
    ["customer success team", "customer success org", "led customer
    success", "head of customer success"]). Keep these tightly scoped to
    that one requirement -- do not reuse the same generic keyword across
    multiple requirements' lists.
Only extract requirements the JD actually and specifically states or clearly
implies as a distinct responsibility/qualification -- do not split one
requirement into several, and do not invent requirements the JD doesn't
support. If the JD only has 1-2 genuinely distinct checkable requirements,
return only that many rather than padding to 3.

General rule: "job_titles", "locations", "core_function_keywords", and
"must_have_requirements" must trace directly back to wording or
clearly-implied intent actually present in the JD. If unsure, leave it out /
use an empty list.
"""

_CANDIDATE_EVALUATION_SYSTEM_PROMPT = """ You are an expert executive recruiter and talent evaluation specialist. Your task is to evaluate candidates against a specific Job Description (JD). You will receive: 1. A JD containing the complete role requirements. 2. A list of candidate profiles extracted from LinkedIn. The JD may also contain explicitly appended: - Required Companies - Required Locations - Core Function Keywords These appended values are part of the hiring requirements and MUST be considered during evaluation. ================================================== CORE EVALUATION PRINCIPLE ================================================== Evaluate the candidate against the ACTUAL REQUIREMENTS of the JD. Do NOT rank candidates based primarily on: - prestigious companies - impressive job titles - number of years of experience - education - generic skills - LinkedIn headline Prioritize demonstrated responsibilities, relevant experience, domain knowledge, seniority, location, and evidence. A candidate with a less prestigious employer but highly relevant experience should rank above a candidate with a prestigious employer but weak relevance. ================================================== 1. ROLE / FUNCTION MATCH ================================================== Determine whether the candidate's actual responsibilities match the primary function of the JD (see Core Function Keywords if provided). Prioritize demonstrated responsibilities over job titles. A candidate should receive strong credit when their current or previous responsibilities directly match the core responsibilities of the JD. Generic or loosely related experience receives only partial credit. Internally classify: - "jd_function": the JD's core function, in a few words (e.g. "Customer Success / Pre-Sales") - "candidate_primary_function": the candidate's actual demonstrated primary function, in a few words, based on their work history (e.g. "Procurement / Supply Chain") If "candidate_primary_function" is a materially different function from "jd_function" (not closely adjacent), this MUST be treated as NO_MATCH on this section regardless of seniority, title, or company prestige, and the FINAL SCORE MUST NOT EXCEED 45 (see section 20). ================================================== 2. RESPONSIBILITY MATCH ================================================== Compare the candidate's demonstrated responsibilities against the major responsibilities in the JD. For each important JD responsibility determine internally: - DIRECT_MATCH - PARTIAL_MATCH - NO_MATCH - UNKNOWN Directly demonstrated responsibilities receive the most credit. Never assume a responsibility merely because the candidate has a similar title. ================================================== 3. MUST-HAVE REQUIREMENTS ================================================== Identify the important must-have requirements in the JD. For each requirement determine internally: - MATCH - PARTIAL - MISS - UNKNOWN Missing multiple critical requirements must materially reduce the score. ================================================== 4. DOMAIN / INDUSTRY MATCH ================================================== Determine how closely the candidate's industry/domain experience matches the JD. Use internally: - DIRECT_MATCH - COMPETITOR / VERY_SIMILAR - ADJACENT - GENERAL_RELEVANCE - UNRELATED - UNKNOWN Domain similarity is supporting evidence and cannot replace functional experience. ================================================== 5. COMPANY RELEVANCE ================================================== If the JD explicitly names target companies, check whether the candidate has experience with those companies. If the JD does NOT name target companies, infer the relevant company/domain universe from the JD. Consider: - direct competitors - companies with similar products - companies selling to similar customers - companies with similar business models - companies in the same technology ecosystem - companies operating in the same market If the JD contains a Required Companies list, use it as strong evidence when evaluating previous/current employers. Do NOT give a high score solely because the candidate worked for a famous company. ================================================== 6. CURRENT VS PREVIOUS EXPERIENCE ================================================== Always distinguish between: CURRENT COMPANY CURRENT ROLE PREVIOUS COMPANY PREVIOUS ROLE The most recent active role should be treated as the current role when the candidate data clearly indicates it. Never treat a previous employer as the candidate's current employer. If the JD requires CURRENT experience, previous experience does not satisfy that requirement. If the JD requires PREVIOUS experience, previous experience may satisfy it. ================================================== 7. SENIORITY MATCH ================================================== Evaluate actual seniority and scope. Consider: - years of relevant experience - ownership level - size of programs/projects - geographical scope - stakeholder scope - strategic responsibility - decision-making responsibility - leadership responsibility Do not determine seniority from title alone. ================================================== 8. SKILL MATCH ================================================== Compare demonstrated skills against the JD. Prioritize skills that are: - explicitly required - repeatedly used - demonstrated in work experience - directly relevant An isolated keyword should not receive full credit. ================================================== 9. STRATEGY → EXECUTION MATCH ================================================== For strategy, programs, transformation, operations or GTM roles, look for evidence that the candidate can: - define objectives - create plans - establish milestones - coordinate dependencies - execute programs - measure outcomes - identify risks - resolve issues - optimize results Candidates showing both strategy and execution should receive stronger credit than candidates showing only one. ================================================== 10. CROSS-FUNCTIONAL / STAKEHOLDER MATCH ================================================== Evaluate evidence of working across relevant functions such as: - Sales - Marketing - Product - Engineering - Customer Success - Operations - Finance - Executives - Customers Strong ownership of cross-functional initiatives receives more credit than simple participation. ================================================== 11. BUSINESS IMPACT ================================================== Look for measurable or clearly described outcomes. Examples: - revenue growth - pipeline growth - productivity improvement - adoption - customer retention - operational efficiency - program success - process improvement - time savings - conversion improvement Quantified outcomes are stronger evidence than generic claims. ================================================== 12. TECHNOLOGY / DOMAIN DEPTH ================================================== When technology is relevant, determine whether the candidate actually: - used - managed - implemented - marketed - sold - enabled - operated - transformed the relevant technology. Do not assume technology expertise simply because the candidate worked at a technology company. ================================================== 13. AI / EMERGING TECHNOLOGY ================================================== If AI or emerging technology is relevant to the JD, distinguish between: STRONG: Direct AI strategy, implementation, product, GTM, enablement, adoption, transformation or commercial experience. MODERATE: AI program management, operations, analytics or related work. WEAK: AI only mentioned as a skill or keyword. NONE: No relevant evidence. ================================================== 14. LOCATION MATCH ================================================== Evaluate the candidate's location against the JD and Required Locations. Use internally: - EXACT / STRONG MATCH - SAME REGION / COUNTRY - REASONABLY COMPATIBLE - UNCLEAR - POTENTIALLY INCOMPATIBLE Do not assume willingness to relocate unless explicitly stated. ================================================== 15. CAREER CONSISTENCY ================================================== Check whether the candidate's career demonstrates a consistent pattern relevant to the JD. Repeated experience in the same functional area is stronger evidence than one isolated relevant role. However, one highly relevant role can still be valuable if it directly matches the JD. ================================================== 16. TRANSFERABLE EXPERIENCE ================================================== Give reasonable credit for highly transferable experience. Consider: - similar customers - similar products - similar sales motion - similar business model - similar responsibilities - similar organizational complexity - similar technology - similar GTM environment Do not require an exact industry match when strong transferable evidence exists. ================================================== 17. NEGATIVE / RISK SIGNALS ================================================== Identify factors that materially reduce suitability. Examples: - missing critical requirement - unrelated functional background - insufficient seniority - predominantly technical experience for a business/GTM role - predominantly operational experience for a strategic role - no evidence of required domain - location conflict - no evidence of required skill Do not invent negative signals. ================================================== 18. EVIDENCE STANDARD ================================================== Every important positive or negative conclusion must be based on evidence contained in the candidate JSON. Use internally: - STRONG_EVIDENCE - MODERATE_EVIDENCE - WEAK_EVIDENCE - NO_EVIDENCE No evidence must never automatically be treated as a match. ================================================== 19. TITLE INDEPENDENCE ================================================== Never score a candidate primarily because of their title. For example: "Program Manager" does NOT automatically mean: "Program Manager experience matching this JD." Evaluate what the candidate actually did. ================================================== 20. SCORE ================================================== Give each candidate a score from 0 to 100. Use this general interpretation: 90-100 = Exceptional alignment 85-89 = Excellent alignment with minor gaps 80-84 = Strong alignment with moderate gaps 75-79 = Good alignment with meaningful gaps 70-74 = Moderate alignment 60-69 = Weak alignment Below 60 = Poor alignment The score must reflect actual suitability for THIS JD. A prestigious employer, senior title, or large number of years must never artificially inflate the score. HARD RULE: if section 1's "candidate_primary_function" is a materially different, non-adjacent function from "jd_function", the score MUST be 45 or below, no matter how strong any other section looks. ================================================== 21. FINAL DECISION ================================================== Determine internally: 90-100 → STRONG_YES 80-89 → YES 70-79 → MAYBE 0-69 → NO ================================================== 22. RANKING ================================================== Evaluate ALL candidates before assigning ranks. Rank candidates from strongest overall fit to weakest overall fit. Rank 1 must be the strongest candidate. Do not rank based only on score. When scores are close, prioritize: 1. Core responsibility match 2. Must-have requirement match 3. Relevant current/recent experience 4. Domain/company relevance 5. Seniority/scope 6. Location 7. Skills 8. Transferable experience ================================================== 23. OUTPUT FORMAT ================================================== Return ONLY valid JSON. Do NOT return markdown. Do NOT return explanations outside the JSON. Return a JSON ARRAY. Each array object MUST contain exactly these fields: { "index": 0, "rank": 1, "candidate": "candidate full name", "score": 95, "location": "candidate location", "current_company": "current/latest company", "current_role": "current/latest role", "headline": "candidate headline", "jd_function": "short label for the JD's core function", "candidate_primary_function": "short label for the candidate's actual primary function", "relevant_companies": [ "Company 1", "Company 2" ], "why_it_fits": "Concise evidence-based explanation of why the candidate matches the JD, including the strongest relevant responsibilities, domain/company experience, skills, seniority, location and any important gaps." } The "relevant_companies" field should contain companies from the candidate's career history that are particularly relevant to the JD. Do not put companies there merely because they are famous. The "why_it_fits" field must be concise but evidence-based. Mention important gaps when they materially affect the score. The "index" MUST be copied exactly from the candidate JSON's "index" field -- a small integer, not the candidate's name or any string. Never modify, invent, or guess it. The candidates you receive will NOT contain an "id" field at all -- do not invent one; reference candidates only by "index". Every candidate must appear exactly once in the output. Sort the final JSON array by rank, strongest candidate first. """


def _call_openai_json(system_prompt: str, user_content: str) -> dict:
    """Calls the LLM once, with temperature 0 and strict JSON output.
    Raises on anything that isn't valid, parseable JSON -- callers must
    not fall back to guessing when this fails."""
    print(f"    [OpenAI] Calling gpt-4o-mini (prompt length: {len(user_content)} chars)...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
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
    print(f"    [OpenAI] Response received ({len(content)} chars). Parsing JSON...")
    parsed = json.loads(content)  # raises if the model didn't return clean JSON
    print(f"    [OpenAI] JSON parsed successfully.")
    return parsed


def _clean_must_have_requirements(raw: list) -> list[dict]:
    """Validates/cleans the must_have_requirements list: each entry must be
    a dict with a non-empty 'requirement' label and a non-empty list of
    'keywords'. Anything malformed is dropped rather than crashing the
    pipeline on a slightly-off LLM response."""
    cleaned: list[dict] = []
    seen_labels: set[str] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        req = (entry.get("requirement") or "").strip()
        kws_raw = entry.get("keywords") or []
        if not req or req.lower() in seen_labels:
            continue
        kws: list[str] = []
        seen_kw: set[str] = set()
        for kw in kws_raw:
            kw = (kw or "").strip().lower()
            if kw and kw not in seen_kw:
                seen_kw.add(kw)
                kws.append(kw)
        if not kws:
            continue
        seen_labels.add(req.lower())
        cleaned.append({"requirement": req, "keywords": kws})
    return cleaned


def extract_signals(job_description: str, company_min_n: int = 25, company_max_n: int = 50) -> dict:
    """The ONE OpenAI call. Returns job_titles, locations, companies,
    core_function_keywords, and must_have_requirements -- all still just
    text/keywords, no candidate ids yet."""
    print("\n[STEP 1] Extracting job titles, locations, companies, core function keywords, "
          "and must-have requirements from JD via LLM...")
    prompt = _EXTRACTION_AND_COMPANY_PROMPT.format(min_n=company_min_n, max_n=company_max_n)
    data = _call_openai_json(prompt, job_description)

    data.setdefault("job_titles", [])
    data.setdefault("locations", [])
    data.setdefault("companies", [])
    data.setdefault("core_function_keywords", [])
    data.setdefault("must_have_requirements", [])

    # de-dup, preserve order, drop empties
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
    data["core_function_keywords"] = _clean(data["core_function_keywords"])
    data["must_have_requirements"] = _clean_must_have_requirements(data["must_have_requirements"])

    print(f"[STEP 1] Done. job_titles={data['job_titles']}")
    print(f"[STEP 1] Done. locations={data['locations']}")
    print(f"[STEP 1] Done. companies ({len(data['companies'])} total)={data['companies']}")
    print(f"[STEP 1] Done. core_function_keywords={data['core_function_keywords']}")
    print(f"[STEP 1] Done. must_have_requirements ({len(data['must_have_requirements'])} total):")
    for req in data["must_have_requirements"]:
        print(f"    - {req['requirement']}: {req['keywords']}")

    return data


# ---------------------------------------------------------------------------
# Resolve names to real Unipile ids (the ONLY source of ids)
# ---------------------------------------------------------------------------

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
    """Real LOCATION ids per location phrase, de-duplicated in order.

    Requests `per_location_limit` results per phrase (default 5) and
    keeps whatever actually comes back -- if a phrase only matches 2
    real places, you get 2; if it matches 5+, you get the top 5. Nothing
    is ever padded to hit a fixed count."""
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
    """First real COMPANY match per name, in the same order as the input
    list. Names with zero matches are silently dropped."""
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


# ---------------------------------------------------------------------------
# Filename helper + save-to-disk
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """'Senior Vice President / Chief Customer Officer' ->
    'senior_vice_president_chief_customer_officer'"""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "role"


def save_payload_to_file(payload: dict, role_name: str, output_dir: str = ".") -> str:
    """Writes `payload` as pretty-printed JSON to
    `{output_dir}/{slugified role_name}_payload.json` and returns the path.
    Overwrites if a file with that name already exists."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{_slugify(role_name)}_payload.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"    [save] Payload written to {path}")
    return path


# ---------------------------------------------------------------------------
# Assemble the final payload: role, location, current_company ONLY
# ---------------------------------------------------------------------------

def build_payload(
        job_description: str,
        company_min_n: int = 25,
        company_max_n: int = 50,
        save_to_dir: str = "./payloads",
) -> dict:
    """
    Always saves the final payload to
    '{save_to_dir}/{role_name}_payload.json' (role_name = the first
    title returned in `job_titles`, or 'role' if none were extracted).
    Pass save_to_dir=None only if you explicitly want to skip writing a
    file and just get the dict back.

    Returns (payload, companies_list, locations_list, core_function_keywords,
    must_have_requirements).
    """
    print("\n========== BUILD_PAYLOAD START ==========")
    signals = extract_signals(job_description, company_min_n, company_max_n)

    payload: dict = {"api": "recruiter", "category": "people"}

    # --- role ---
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

    # --- location (scope always CURRENT, per spec) ---
    location_ids = resolve_location_ids(signals["locations"], per_location_limit=5)
    if location_ids:
        payload["location"] = [
            {"id": _id, "priority": "CAN_HAVE", "scope": "CURRENT"} for _id in location_ids
        ]

    # --- current_company ---
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

    return (
        payload,
        signals["companies"],
        signals["locations"],
        signals["core_function_keywords"],
        signals["must_have_requirements"],
    )


# ---------------------------------------------------------------------------
# Run the actual LinkedIn search via Unipile, using the payload built above
# ---------------------------------------------------------------------------

def search_candidate(
        payload: dict,
        target_count: int = 500,
        save_to_dir: str = "./search_results",
) -> dict:
    """POSTs `payload` (as returned by build_payload) to Unipile's
    /api/v1/linkedin/search, following `cursor` across pages (100 per
    request, the API's per-page cap) until it has collected
    `target_count` candidates OR runs out of real results -- whichever
    comes first. If the search only has fewer than `target_count` total
    matches, every one of them is returned; nothing is padded to hit the
    target.

    Returns {'items': [...], 'total_count': int|None,
    'collected_count': int, '_saved_to': path}. Strips internal
    bookkeeping keys ('_saved_to', '_role_name') from the outgoing
    request body, since those aren't part of Unipile's schema. Pass
    save_to_dir=None to skip saving."""
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
    page_limit = 100  # Unipile's per-request cap for recruiter/sales_navigator
    page_num = 0

    while len(all_items) < target_count:
        page_num += 1
        params: dict = {
            "limit": min(page_limit, target_count - len(all_items)),
            "account_id": UNIPILE_ACCOUNT_ID,
        }
        if cursor:
            params["cursor"] = cursor

        print(f"    [page {page_num}] Requesting up to {params['limit']} results (cursor={'yes' if cursor else 'none'})...")
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
            break  # no more real results to page through
        if total_count is not None and len(all_items) >= total_count:
            print(f"    [page {page_num}] Collected every reported match -- stopping pagination.")
            break  # collected every real match that exists

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


def refactor_result_for_evaluation(result: dict) -> list:
    print(f"\n[STEP 4] Refactoring {len(result.get('items', []))} raw candidate profile(s) "
          f"down to the fields the evaluator LLM needs...")
    required_keys = {
        "id",
        "headline",
        "location",
        "name",
        "industry",
        "skills",
        "summary",
        "education",
        "work_experience",
        "certifications",
    }

    refactored_items = []

    for profile in result.get("items", []):
        refactored_profile = {
            key: profile[key]
            for key in required_keys
            if key in profile
        }

        if "work_experience" in refactored_profile:
            refactored_profile["work_experience"] = [
                {
                    key: experience[key]
                    for key in [
                        "company",
                        "company_id",
                        "industry",
                        "location",
                        "role",
                        "start",
                        "end",
                        "description",
                        "skills",
                    ]
                    if key in experience
                }
                for experience in refactored_profile["work_experience"]
            ]

        if "skills" in refactored_profile:
            refactored_profile["skills"] = [
                skill["name"]
                for skill in refactored_profile["skills"]
                if skill.get("name")
            ]

        if "education" in refactored_profile:
            refactored_profile["education"] = [
                {
                    key: education_item[key]
                    for key in [
                        "degree",
                        "school",
                        "field_of_study",
                        "start",
                        "end",
                    ]
                    if key in education_item
                }
                for education_item in refactored_profile["education"]
            ]

        if "certifications" in refactored_profile:
            refactored_profile["certifications"] = [
                {
                    key: certification[key]
                    for key in [
                        "name",
                        "organization",
                        "start",
                    ]
                    if key in certification
                }
                for certification in refactored_profile["certifications"]
            ]

        refactored_items.append(refactored_profile)

    print(f"[STEP 4] Done. Refactored {len(refactored_items)} candidate profile(s).")
    return refactored_items


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


def evaluate_candidates_with_llm(
    modified_jd: str,
    refactored_items: list,
    max_retries: int = 4,
    base_backoff_seconds: float = 15.0,
) -> str:
    """Evaluates ONE batch of candidates. Retries on HTTP 429 (rate limit)
    with exponential backoff, honoring the Retry-After header when OpenAI
    sends one. Raises on any other HTTP error, or if retries are
    exhausted."""
    print(f"    [LLM eval] Sending batch of {len(refactored_items)} candidate(s) to LLM...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Strip the real id out and give the model a small local index instead --
    # small integers are transcribed reliably; long opaque id strings are not.
    indexed_items = [
        {"index": i, **{k: v for k, v in c.items() if k != "id"}}
        for i, c in enumerate(refactored_items)
    ]

    user_content = f"""
JOB DESCRIPTION:

{modified_jd}


CANDIDATES:

{json.dumps(indexed_items, ensure_ascii=False, indent=2)}
"""

    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": _CANDIDATE_EVALUATION_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
    }

    attempt = 0
    while True:
        attempt += 1
        response = requests.post(
            OPENAI_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )

        if response.status_code == 429:
            print(f"    [LLM eval] 429 rate-limited (attempt {attempt}/{max_retries}). "
                  f"Body: {response.text[:300]}")
            if attempt >= max_retries:
                print(f"    [LLM eval] Out of retries -- raising.")
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                wait_seconds = float(retry_after)
            else:
                wait_seconds = base_backoff_seconds * (2 ** (attempt - 1))
            print(f"    [LLM eval] Waiting {wait_seconds:.0f}s before retrying...")
            time.sleep(wait_seconds)
            continue

        if not response.ok:
            print(f"    [LLM eval] HTTP {response.status_code} error. Body: {response.text[:500]}")

        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        print(f"    [LLM eval] Batch response received ({len(content)} chars).")
        return content


def evaluate_candidates_in_batches(
    modified_jd: str,
    refactored_items: list,
    batch_size: int = 25,
) -> Optional[list]:
    """Splits refactored_items into chunks of `batch_size`, sends each
    chunk to evaluate_candidates_with_llm separately (so a single call
    never gets big enough to trip a token-per-minute rate limit), parses
    each batch's JSON array, and merges every batch's candidates into
    ONE final list -- re-sorted by score (highest first) and re-numbered
    1..N so the ranks are consistent across the whole merged set, not
    just within each batch. Returns None if any batch fails to parse."""
    total = len(refactored_items)
    num_batches = math.ceil(total / batch_size) if total else 0
    print(f"\n[STEP 5] Evaluating {total} candidate(s) in {num_batches} batch(es) of up to {batch_size}...")

    merged: list = []

    for batch_num in range(num_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, total)
        chunk = refactored_items[start:end]

        print(f"\n[STEP 5] Batch {batch_num + 1}/{num_batches}: candidates {start}-{end - 1} ({len(chunk)} total)")
        raw = evaluate_candidates_with_llm(modified_jd, chunk)

        try:
            batch_result = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[STEP 5] ❌ Failed to parse LLM evaluation JSON for batch {batch_num + 1}: {e}")
            return None

        # The evaluator can return either a bare JSON array, or an object
        # wrapping one (e.g. {"candidates": [...]}) -- handle both.
        if isinstance(batch_result, dict):
            list_values = [v for v in batch_result.values() if isinstance(v, list)]
            if not list_values:
                print(f"[STEP 5] ❌ Batch {batch_num + 1} returned an object with no list inside it.")
                return None
            batch_result = list_values[0]

        print(f"[STEP 5] Batch {batch_num + 1}/{num_batches} done. Got {len(batch_result)} scored candidate(s).")

        # Resolve each candidate's small "index" back to the real id/name/location
        # from `chunk` (ground truth) -- never trust the LLM's own copy of these.
        for cand in batch_result:
            idx = cand.pop("index", None)
            if isinstance(idx, int) and 0 <= idx < len(chunk):
                source = chunk[idx]
                cand["id"] = source.get("id")
                cand["candidate"] = source.get("name")
                cand["location"] = source.get("location")
            else:
                cand["id"] = None
                cand["_index_flag"] = "invalid_or_missing_index"
                print(f"[STEP 5] WARNING: batch {batch_num + 1} candidate has bad/missing "
                      f"index={idx!r} -- id/name/location left unresolved.")

        merged.extend(batch_result)

    # Re-rank across the WHOLE merged set, not per-batch, so rank 1 is the
    # strongest candidate overall.
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, candidate in enumerate(merged, start=1):
        candidate["rank"] = i

    print(f"\n[STEP 5] All batches merged and re-ranked. {len(merged)} candidate(s) total, "
          f"top score={merged[0].get('score') if merged else 'n/a'}.")

    return merged


# ---------------------------------------------------------------------------
# NEW: independent, code-level function-match gate.
#
# The system prompt now asks the LLM to self-report jd_function /
# candidate_primary_function and cap its own score -- but small models
# (gpt-4o-mini) can and do ignore soft instructions like that under load.
# This gate does NOT trust the model's self-report; it independently
# checks the candidate's own text (headline/summary/work history) for the
# JD's core function keywords, extracted separately in STEP 1. If none of
# those keywords appear anywhere in the candidate's real profile text, the
# score is force-capped in code, regardless of what the LLM decided.
# ---------------------------------------------------------------------------

def enforce_function_gate(
    merged: list,
    refactored_items: list,
    core_function_keywords: list[str],
    cap_score: float = 45,
) -> list:
    """Independent, code-level check: does ANY evidence in the candidate's
    headline/summary/work_experience actually mention the JD's core function?
    If not, force the score down regardless of what the LLM said. This is a
    safety net for cheap models ignoring soft prompt instructions."""
    if not core_function_keywords:
        print("[function_gate] No core_function_keywords extracted -- skipping gate.")
        return merged

    keywords = [k.lower() for k in core_function_keywords]
    by_id = {item["id"]: item for item in refactored_items if item.get("id")}

    capped_count = 0
    for cand in merged:
        item = by_id.get(cand.get("id"))
        if not item:
            continue

        text_blobs = [item.get("headline", "") or "", item.get("summary", "") or ""]
        for exp in item.get("work_experience", []):
            text_blobs.append(exp.get("role", "") or "")
            text_blobs.append(exp.get("description", "") or "")
        haystack = " ".join(text_blobs).lower()

        matched = any(kw in haystack for kw in keywords)

        if not matched:
            original_score = cand.get("score", 0)
            if original_score > cap_score:
                print(f"    [function_gate] '{cand.get('candidate')}' scored "
                      f"{original_score} but no function-keyword evidence found "
                      f"-- capping to {cap_score}")
                cand["score"] = cap_score
                cand["_function_gate_flag"] = "capped_no_function_evidence"
                capped_count += 1

    print(f"[function_gate] Done. {capped_count} candidate(s) capped for missing function evidence.")

    # re-sort/re-rank after capping
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(merged, start=1):
        c["rank"] = i
    return merged


# ---------------------------------------------------------------------------
# NEW: independent, code-level MUST-HAVE requirement gate.
#
# The coarse function_gate above only catches a candidate with ZERO overlap
# with the JD's function (e.g. a procurement exec applied against a customer
# success JD). It does NOT catch the more common, more dangerous failure
# mode: a candidate who genuinely IS in an adjacent function (real customer/
# revenue leadership) but is missing several of the JD's specific, named
# must-have responsibilities (e.g. "led a customer success team", "led a
# pre-sales team", "professional services delivery"). The LLM's holistic
# scoring can let one spectacular business-impact number (e.g. "$3B ARR")
# drown out two completely unaddressed must-haves -- and the LLM's own
# self-reported jd_function/candidate_primary_function labels can't be
# trusted to catch this, since the model can (and did, in production)
# reword its own labels to make a partial match look like a full one.
#
# This gate re-checks, independently and in code, whether each specific
# must-have requirement (extracted in STEP 1, each with its own keyword
# set) has ANY textual evidence in the candidate's real profile. It then
# caps the score based on how many of the JD's must-haves are completely
# unaddressed -- regardless of what the LLM decided.
# ---------------------------------------------------------------------------

# Fraction of must-haves with zero evidence -> score cap. Interpolated
# between the nearest two breakpoints below; a candidate missing 100% of
# must-haves is capped the same as a full function mismatch (45).
_MUST_HAVE_MISS_CAPS = [
    (0.0, 100),   # no requirements missing -> no cap from this gate
    (0.34, 80),   # up to ~1/3 missing -> cap at 80 (blocks "Strong+" claims)
    (0.67, 65),   # up to ~2/3 missing -> cap at 65 (blocks even "Moderate+")
    (1.0, 45),    # all missing -> same cap as a full function mismatch
]


def _cap_for_miss_ratio(miss_ratio: float) -> float:
    for threshold, cap in _MUST_HAVE_MISS_CAPS:
        if miss_ratio <= threshold:
            return cap
    return _MUST_HAVE_MISS_CAPS[-1][1]


def enforce_must_have_gate(
    merged: list,
    refactored_items: list,
    must_have_requirements: list[dict],
) -> list:
    """Independent, code-level check: for each of the JD's specific
    must-have requirements, does the candidate's own profile text contain
    ANY of that requirement's keywords? Candidates missing a large share of
    the JD's named must-haves get their score capped in code -- this does
    not rely on the LLM correctly weighing "one strong number vs. two
    unaddressed requirements" on its own, and it does not trust the LLM's
    self-reported match labels, which can be reworded to look aligned."""
    if not must_have_requirements:
        print("[must_have_gate] No must_have_requirements extracted -- skipping gate.")
        return merged

    by_id = {item["id"]: item for item in refactored_items if item.get("id")}
    total_reqs = len(must_have_requirements)

    capped_count = 0
    for cand in merged:
        item = by_id.get(cand.get("id"))
        if not item:
            continue

        text_blobs = [item.get("headline", "") or "", item.get("summary", "") or ""]
        for exp in item.get("work_experience", []):
            text_blobs.append(exp.get("role", "") or "")
            text_blobs.append(exp.get("description", "") or "")
        haystack = " ".join(text_blobs).lower()

        missing: list[str] = []
        for req in must_have_requirements:
            if not any(kw in haystack for kw in req["keywords"]):
                missing.append(req["requirement"])

        miss_ratio = len(missing) / total_reqs
        cap = _cap_for_miss_ratio(miss_ratio)

        original_score = cand.get("score", 0)
        cand["_must_have_missing"] = missing
        cand["_must_have_total"] = total_reqs

        if original_score > cap:
            print(f"    [must_have_gate] '{cand.get('candidate')}' scored {original_score} "
                  f"but is missing {len(missing)}/{total_reqs} named must-haves "
                  f"({missing}) -- capping to {cap}")
            cand["score"] = cap
            cand["_must_have_gate_flag"] = "capped_missing_requirements"
            capped_count += 1

    print(f"[must_have_gate] Done. {capped_count} candidate(s) capped for missing must-have requirements.")

    # re-sort/re-rank after capping
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(merged, start=1):
        c["rank"] = i
    return merged


def enrich_with_profile_media(evaluated: list[dict], raw_items: list[dict]) -> list[dict]:
    """Re-attaches public_profile_url and profile_picture_url from the raw
    Unipile search results onto each evaluated candidate, matched by id.
    These fields are dropped in refactor_result_for_evaluation since the
    LLM doesn't need them, so they have to come back from the original
    search response, not from the LLM output."""
    media_by_id = {
        item["id"]: {
            "public_profile_url": item.get("public_profile_url"),
            "profile_picture_url": item.get("profile_picture_url"),
        }
        for item in raw_items
        if item.get("id")
    }

    for cand in evaluated:
        media = media_by_id.get(cand.get("id"))
        if media:
            cand["public_profile_url"] = media["public_profile_url"]
            cand["profile_picture_url"] = media["profile_picture_url"]
        else:
            cand["public_profile_url"] = None
            cand["profile_picture_url"] = None
            print(f"    [enrich] no raw match for id={cand.get('id')} "
                  f"('{cand.get('candidate')}') -- media urls left null")

    return evaluated


def reconcile_candidate_ids(evaluated: list[dict], refactored_items: list[dict]) -> list[dict]:
    """Cross-checks every evaluated candidate's id against refactored_items
    (the real Unipile results, the only source of truth for id<->name).
    If the id doesn't exist there, tries to recover it via an exact name
    match. Flags anything it can't resolve instead of silently trusting
    the LLM's echoed id."""
    id_to_item = {item["id"]: item for item in refactored_items if item.get("id")}
    name_to_ids: dict[str, list[str]] = {}
    for item in refactored_items:
        name_to_ids.setdefault((item.get("name") or "").strip().lower(), []).append(item["id"])

    fixed = []
    for cand in evaluated:
        cid = cand.get("id")
        cname = (cand.get("candidate") or "").strip().lower()

        if cid in id_to_item:
            true_name = (id_to_item[cid].get("name") or "").strip().lower()
            if true_name and true_name != cname:
                cand["_id_flag"] = "id_valid_but_name_mismatch"
                print(f"[reconcile] WARNING id={cid} is really '{true_name}', "
                      f"LLM labeled it '{cand.get('candidate')}'")
            fixed.append(cand)
            continue

        matches = name_to_ids.get(cname, [])
        if len(matches) == 1:
            print(f"[reconcile] corrected id for '{cand.get('candidate')}': {cid} -> {matches[0]}")
            cand["id"] = matches[0]
            cand["_id_flag"] = "corrected_via_name"
        elif len(matches) > 1:
            cand["_id_flag"] = "ambiguous_name_match"
            print(f"[reconcile] AMBIGUOUS name '{cname}' -> {matches}, can't auto-fix")
        else:
            cand["_id_flag"] = "unresolved"
            print(f"[reconcile] UNRESOLVED: bad id={cid}, name='{cand.get('candidate')}' not found at all")

        fixed.append(cand)
    return fixed


def create_unipile_recruiter_project(
    project_name: str,
    visibility: str = "PRIVATE",
) -> dict | None:
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
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code in (200, 201):
            response = response.json()
            project_id = response.get("project_id")
            print(f"[STEP 6] Project created successfully. project_id={project_id}")
            return project_id

        print(
            f"❌ Unipile project creation failed: "
            f"{response.status_code} {response.text[:300]}"
        )
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
    min_score: float = 80,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
    require_clean_function_gate: bool = True,
    require_clean_must_have_gate: bool = True,
) -> None:
    """Adds every eligible candidate to the given Unipile recruiter
    project's pipeline, one at a time, sleeping `delay_seconds` BETWEEN
    consecutive requests (not before the first, not after the last).
    Meant to run on a background thread.

    CHANGED: min_score default raised from 70 -> 80, and by default a
    candidate flagged by EITHER enforce_function_gate
    (_function_gate_flag == 'capped_no_function_evidence') OR
    enforce_must_have_gate (_must_have_gate_flag ==
    'capped_missing_requirements') is excluded even if something upstream
    pushed its score back up -- belt and braces against a single bad
    auto-push reaching the client's pipeline.
    """
    eligible = [
        c for c in candidates
        if (c.get("score") or 0) >= min_score
        and not (require_clean_function_gate and c.get("_function_gate_flag") == "capped_no_function_evidence")
        and not (require_clean_must_have_gate and c.get("_must_have_gate_flag") == "capped_missing_requirements")
    ]
    print(f"\n[STEP 7] Adding {len(eligible)} candidate(s) with score >= {min_score} "
          f"(function-gate clean={require_clean_function_gate}, "
          f"must-have-gate clean={require_clean_must_have_gate}) "
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
    min_score: float = 80,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
    require_clean_function_gate: bool = True,
    require_clean_must_have_gate: bool = True,
) -> threading.Thread:
    """Kicks off add_candidates_to_pipeline_with_delay on a daemon thread
    and returns immediately, so main_function can hand final_result back
    to the caller right away while the adds happen in the background."""
    thread = threading.Thread(
        target=add_candidates_to_pipeline_with_delay,
        args=(candidates, hiring_project_id),
        kwargs={
            "min_score": min_score,
            "delay_seconds": delay_seconds,
            "stage": stage,
            "require_clean_function_gate": require_clean_function_gate,
            "require_clean_must_have_gate": require_clean_must_have_gate,
        },
        daemon=True,
    )
    thread.start()
    print(f"[STEP 7] Background pipeline-add thread started (thread_id={thread.ident}).")
    return thread


# main function
def main_function(
    jd: str,
    project_name: str,
    hiring_for: str,
    auto_add_to_pipeline: bool = False,
    min_score_for_auto_add: float = 80,
):
    """
    CHANGED behavior vs. the original:

    1. Extraction now also returns core_function_keywords AND
       must_have_requirements (a list of {requirement, keywords} objects
       for each specific, named must-have in the JD), threaded into the
       evaluator's JD text as extra context lines.
    2. TWO independent, code-level gates run after
       evaluate_candidates_in_batches(), before anything else touches the
       score:
         - enforce_function_gate(): catches a candidate with ZERO overlap
           with the JD's function (e.g. procurement exec on a customer
           success JD) -- caps hard to 45.
         - enforce_must_have_gate(): catches the more common case of an
           adjacent, genuinely-relevant candidate who is nonetheless
           missing several of the JD's SPECIFIC named must-haves (e.g. "led
           a customer success team", "professional services delivery") --
           caps proportionally to how many are missing, so one great
           business-impact number can no longer single-handedly drown out
           multiple unaddressed requirements.
       Neither gate trusts the LLM's own self-reported match labels --
       both independently re-scan the candidate's actual profile text,
       because the LLM can (and has, in production) reworded its own
       labels to make a partial match look like a full one.
    3. auto_add_to_pipeline defaults to False. While you're rebuilding
       trust in the pipeline, call main_function(...) and manually review
       final_result yourself, then call
       start_pipeline_add_in_background(...) explicitly once you're happy
       with a batch. Pass auto_add_to_pipeline=True to restore the old
       automatic behavior (now gated at score>=80 and both gates clean,
       instead of the old score>=70 with no gates at all).
    """
    print("\n################## PIPELINE START ##################")
    print(f"Project name: {project_name}")
    print(f"Hiring for: {hiring_for}")
    print(f"JD length: {len(jd)} chars")

    payload, companies_list, locations_list, core_function_keywords, must_have_requirements = build_payload(jd)

    result = search_candidate(payload)

    must_have_lines = "\n".join(
        f"- {req['requirement']}" for req in must_have_requirements
    ) or "(none extracted)"

    modified_jd = (
        jd
        + "\n\nHiring For: "
        + hiring_for
        + "\nRequired Companies: "
        + ", ".join(companies_list)
        + "\nRequired Locations: "
        + ", ".join(locations_list)
        + "\nRequired Core Function: "
        + ", ".join(core_function_keywords)
        + "\nNamed Must-Have Requirements:\n"
        + must_have_lines
    )
    print(f"\n[STEP 4b] JD annotated with hiring_for, {len(companies_list)} required companies, "
          f"{len(locations_list)} required locations, {len(core_function_keywords)} "
          f"core function keywords, and {len(must_have_requirements)} named must-have "
          f"requirements for the evaluator.")

    refactored_items = refactor_result_for_evaluation(result)

    final_result = evaluate_candidates_in_batches(
        modified_jd,
        refactored_items,
        batch_size=15,
    )

    if final_result is None:
        print("❌ Candidate evaluation failed -- see batch errors above.")
        return None, None

    # NEW: two independent code-level gates, run before anything else
    # touches the score. Order doesn't matter much since each only lowers
    # scores, never raises them, but function_gate first means an already
    # fully-capped candidate doesn't need the (slightly more expensive to
    # reason about) must-have breakdown recalculated on top.
    final_result = enforce_function_gate(final_result, refactored_items, core_function_keywords)
    final_result = enforce_must_have_gate(final_result, refactored_items, must_have_requirements)

    final_result = reconcile_candidate_ids(final_result, refactored_items)
    final_result = enrich_with_profile_media(final_result, result.get("items", []))

    print(f"[STEP 5] Final merged + re-ranked result: {len(final_result)} candidate(s).")

    project_id = create_unipile_recruiter_project(project_name)

    if not project_id:
        print("❌ Failed to create Unipile recruiter project -- skipping pipeline adds.")
        print("################## PIPELINE END (no project) ##################\n")
        return final_result, None

    thread = None
    if auto_add_to_pipeline:
        thread = start_pipeline_add_in_background(
            final_result,
            project_id,
            min_score=min_score_for_auto_add,
            delay_seconds=20,
            require_clean_function_gate=True,
            require_clean_must_have_gate=True,
        )
    else:
        print("[STEP 7] auto_add_to_pipeline=False -- skipping automatic pipeline adds. "
              "Review final_result and call start_pipeline_add_in_background(...) "
              "yourself once you've approved the list.")

    return final_result, thread





from __future__ import annotations

import os
import re
import json
import time
import math
from typing import Optional
import threading

import requests

from dotenv import load_dotenv
import os

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

UNIPILE_API_KEY = "VPUyiWkr.rbbNVdUZfHrvh5uOV3Jtx/eoQCGXXrG5O2p+0AqOQwQ="
UNIPILE_ACCOUNT_ID = "D8lUBYotRuGOlA7cOQ4egQ"
UNIPILE_BASE_URL = "https://api40.unipile.com:17060"

UNIPILE_PROJECTS_BASE_URL = "https://api.unipile.com/v2"
UNIPILE_PROJECTS_API_KEY = "bKcyr7TB.app_01kznge4wxesmap4y2wk9qnqpv.PN4y1XB4VB1blVpdmZ+94MEM0llrJ5hGbV7MPgrjlr0="
UNIPILE_PROJECTS_ACCOUNT_ID = "acc_01m09sdddhfetrdm9tzcbqncv1"

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
UNIPILE_SEARCH_PARAMS_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search/parameters"
UNIPILE_SEARCH_URL = f"{UNIPILE_BASE_URL}/api/v1/linkedin/search"

# ---------------------------------------------------------------------------
# The single LLM call: extraction (titles + locations) + company shortlist
# + core function keywords (NEW -- used later by the code-level function gate)
# ---------------------------------------------------------------------------

_EXTRACTION_AND_COMPANY_PROMPT = """You are an information extractor and sourcing assistant. You will be given a
job description. Return ONLY a single JSON object, no prose, no markdown fences,
matching this schema exactly:

{{
  "job_titles": [string, ...],
  "locations": [string, ...],
  "companies": [string, ...],
  "core_function_keywords": [string, ...],
  "must_have_requirements": [
    {{"requirement": string, "keywords": [string, ...]}},
    ...
  ]
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

"core_function_keywords": 4-8 keywords/phrases describing the DAY-TO-DAY FUNCTION
this role actually performs (e.g. "customer success", "pre-sales team",
"account management", "customer engagement strategy", "renewals", "upsell
motion"). This is about FUNCTION, not industry, not seniority, and not company
type. It is used later as an independent, code-level sanity check to make sure
a candidate isn't from an unrelated function (e.g. procurement, engineering,
finance) that merely happens to share a similar title, seniority level, or
employer. EVERY entry MUST be a real multi-word phrase (2-4 words) actually
usable as a substring match against resume text -- never a single generic
word like "customer", "strategy", "growth", "leadership", or "management" on
its own, since single generic words match almost any executive profile and
make this check useless. Ground every phrase directly in the JD's actual
responsibilities section -- never invent a function the JD doesn't describe.

"must_have_requirements": 3-6 objects, each describing ONE specific, named,
checkable requirement or responsibility from the JD (not the whole role in
general -- one distinct, separately-checkable capability per object, e.g.
"led a customer success team", "led a pre-sales team", "oversaw professional
services delivery", "SaaS company experience"). For each object:
  - "requirement": a short human-readable label for that one requirement.
  - "keywords": 2-5 short phrases (2-4 words each, lowercase-friendly) that
    would plausibly appear in a resume/LinkedIn profile as evidence of that
    SPECIFIC requirement being met (e.g. for "led a customer success team":
    ["customer success team", "customer success org", "led customer
    success", "head of customer success"]). Keep these tightly scoped to
    that one requirement -- do not reuse the same generic keyword across
    multiple requirements' lists.
Only extract requirements the JD actually and specifically states or clearly
implies as a distinct responsibility/qualification -- do not split one
requirement into several, and do not invent requirements the JD doesn't
support. If the JD only has 1-2 genuinely distinct checkable requirements,
return only that many rather than padding to 3.

General rule: "job_titles", "locations", "core_function_keywords", and
"must_have_requirements" must trace directly back to wording or
clearly-implied intent actually present in the JD. If unsure, leave it out /
use an empty list.
"""

_CANDIDATE_EVALUATION_SYSTEM_PROMPT = """ You are an expert executive recruiter and talent evaluation specialist. Your task is to evaluate candidates against a specific Job Description (JD). You will receive: 1. A JD containing the complete role requirements. 2. A list of candidate profiles extracted from LinkedIn. The JD may also contain explicitly appended: - Required Companies - Required Locations - Core Function Keywords These appended values are part of the hiring requirements and MUST be considered during evaluation. ================================================== CORE EVALUATION PRINCIPLE ================================================== Evaluate the candidate against the ACTUAL REQUIREMENTS of the JD. Do NOT rank candidates based primarily on: - prestigious companies - impressive job titles - number of years of experience - education - generic skills - LinkedIn headline Prioritize demonstrated responsibilities, relevant experience, domain knowledge, seniority, location, and evidence. A candidate with a less prestigious employer but highly relevant experience should rank above a candidate with a prestigious employer but weak relevance. ================================================== 1. ROLE / FUNCTION MATCH ================================================== Determine whether the candidate's actual responsibilities match the primary function of the JD (see Core Function Keywords if provided). Prioritize demonstrated responsibilities over job titles. A candidate should receive strong credit when their current or previous responsibilities directly match the core responsibilities of the JD. Generic or loosely related experience receives only partial credit. Internally classify: - "jd_function": the JD's core function, in a few words (e.g. "Customer Success / Pre-Sales") - "candidate_primary_function": the candidate's actual demonstrated primary function, in a few words, based on their work history (e.g. "Procurement / Supply Chain") If "candidate_primary_function" is a materially different function from "jd_function" (not closely adjacent), this MUST be treated as NO_MATCH on this section regardless of seniority, title, or company prestige, and the FINAL SCORE MUST NOT EXCEED 45 (see section 20). ================================================== 2. RESPONSIBILITY MATCH ================================================== Compare the candidate's demonstrated responsibilities against the major responsibilities in the JD. For each important JD responsibility determine internally: - DIRECT_MATCH - PARTIAL_MATCH - NO_MATCH - UNKNOWN Directly demonstrated responsibilities receive the most credit. Never assume a responsibility merely because the candidate has a similar title. ================================================== 3. MUST-HAVE REQUIREMENTS ================================================== Identify the important must-have requirements in the JD. For each requirement determine internally: - MATCH - PARTIAL - MISS - UNKNOWN Missing multiple critical requirements must materially reduce the score. ================================================== 4. DOMAIN / INDUSTRY MATCH ================================================== Determine how closely the candidate's industry/domain experience matches the JD. Use internally: - DIRECT_MATCH - COMPETITOR / VERY_SIMILAR - ADJACENT - GENERAL_RELEVANCE - UNRELATED - UNKNOWN Domain similarity is supporting evidence and cannot replace functional experience. ================================================== 5. COMPANY RELEVANCE ================================================== If the JD explicitly names target companies, check whether the candidate has experience with those companies. If the JD does NOT name target companies, infer the relevant company/domain universe from the JD. Consider: - direct competitors - companies with similar products - companies selling to similar customers - companies with similar business models - companies in the same technology ecosystem - companies operating in the same market If the JD contains a Required Companies list, use it as strong evidence when evaluating previous/current employers. Do NOT give a high score solely because the candidate worked for a famous company. ================================================== 6. CURRENT VS PREVIOUS EXPERIENCE ================================================== Always distinguish between: CURRENT COMPANY CURRENT ROLE PREVIOUS COMPANY PREVIOUS ROLE The most recent active role should be treated as the current role when the candidate data clearly indicates it. Never treat a previous employer as the candidate's current employer. If the JD requires CURRENT experience, previous experience does not satisfy that requirement. If the JD requires PREVIOUS experience, previous experience may satisfy it. ================================================== 7. SENIORITY MATCH ================================================== Evaluate actual seniority and scope. Consider: - years of relevant experience - ownership level - size of programs/projects - geographical scope - stakeholder scope - strategic responsibility - decision-making responsibility - leadership responsibility Do not determine seniority from title alone. ================================================== 8. SKILL MATCH ================================================== Compare demonstrated skills against the JD. Prioritize skills that are: - explicitly required - repeatedly used - demonstrated in work experience - directly relevant An isolated keyword should not receive full credit. ================================================== 9. STRATEGY → EXECUTION MATCH ================================================== For strategy, programs, transformation, operations or GTM roles, look for evidence that the candidate can: - define objectives - create plans - establish milestones - coordinate dependencies - execute programs - measure outcomes - identify risks - resolve issues - optimize results Candidates showing both strategy and execution should receive stronger credit than candidates showing only one. ================================================== 10. CROSS-FUNCTIONAL / STAKEHOLDER MATCH ================================================== Evaluate evidence of working across relevant functions such as: - Sales - Marketing - Product - Engineering - Customer Success - Operations - Finance - Executives - Customers Strong ownership of cross-functional initiatives receives more credit than simple participation. ================================================== 11. BUSINESS IMPACT ================================================== Look for measurable or clearly described outcomes. Examples: - revenue growth - pipeline growth - productivity improvement - adoption - customer retention - operational efficiency - program success - process improvement - time savings - conversion improvement Quantified outcomes are stronger evidence than generic claims. ================================================== 12. TECHNOLOGY / DOMAIN DEPTH ================================================== When technology is relevant, determine whether the candidate actually: - used - managed - implemented - marketed - sold - enabled - operated - transformed the relevant technology. Do not assume technology expertise simply because the candidate worked at a technology company. ================================================== 13. AI / EMERGING TECHNOLOGY ================================================== If AI or emerging technology is relevant to the JD, distinguish between: STRONG: Direct AI strategy, implementation, product, GTM, enablement, adoption, transformation or commercial experience. MODERATE: AI program management, operations, analytics or related work. WEAK: AI only mentioned as a skill or keyword. NONE: No relevant evidence. ================================================== 14. LOCATION MATCH ================================================== Evaluate the candidate's location against the JD and Required Locations. Use internally: - EXACT / STRONG MATCH - SAME REGION / COUNTRY - REASONABLY COMPATIBLE - UNCLEAR - POTENTIALLY INCOMPATIBLE Do not assume willingness to relocate unless explicitly stated. ================================================== 15. CAREER CONSISTENCY ================================================== Check whether the candidate's career demonstrates a consistent pattern relevant to the JD. Repeated experience in the same functional area is stronger evidence than one isolated relevant role. However, one highly relevant role can still be valuable if it directly matches the JD. ================================================== 16. TRANSFERABLE EXPERIENCE ================================================== Give reasonable credit for highly transferable experience. Consider: - similar customers - similar products - similar sales motion - similar business model - similar responsibilities - similar organizational complexity - similar technology - similar GTM environment Do not require an exact industry match when strong transferable evidence exists. ================================================== 17. NEGATIVE / RISK SIGNALS ================================================== Identify factors that materially reduce suitability. Examples: - missing critical requirement - unrelated functional background - insufficient seniority - predominantly technical experience for a business/GTM role - predominantly operational experience for a strategic role - no evidence of required domain - location conflict - no evidence of required skill Do not invent negative signals. ================================================== 18. EVIDENCE STANDARD ================================================== Every important positive or negative conclusion must be based on evidence contained in the candidate JSON. Use internally: - STRONG_EVIDENCE - MODERATE_EVIDENCE - WEAK_EVIDENCE - NO_EVIDENCE No evidence must never automatically be treated as a match. ================================================== 19. TITLE INDEPENDENCE ================================================== Never score a candidate primarily because of their title. For example: "Program Manager" does NOT automatically mean: "Program Manager experience matching this JD." Evaluate what the candidate actually did. ================================================== 20. SCORE ================================================== Give each candidate a score from 0 to 100. Use this general interpretation: 90-100 = Exceptional alignment 85-89 = Excellent alignment with minor gaps 80-84 = Strong alignment with moderate gaps 75-79 = Good alignment with meaningful gaps 70-74 = Moderate alignment 60-69 = Weak alignment Below 60 = Poor alignment The score must reflect actual suitability for THIS JD. A prestigious employer, senior title, or large number of years must never artificially inflate the score. HARD RULE: if section 1's "candidate_primary_function" is a materially different, non-adjacent function from "jd_function", the score MUST be 45 or below, no matter how strong any other section looks. ================================================== 21. FINAL DECISION ================================================== Determine internally: 90-100 → STRONG_YES 80-89 → YES 70-79 → MAYBE 0-69 → NO ================================================== 22. RANKING ================================================== Evaluate ALL candidates before assigning ranks. Rank candidates from strongest overall fit to weakest overall fit. Rank 1 must be the strongest candidate. Do not rank based only on score. When scores are close, prioritize: 1. Core responsibility match 2. Must-have requirement match 3. Relevant current/recent experience 4. Domain/company relevance 5. Seniority/scope 6. Location 7. Skills 8. Transferable experience ================================================== 23. OUTPUT FORMAT ================================================== Return ONLY valid JSON. Do NOT return markdown. Do NOT return explanations outside the JSON. Return a JSON ARRAY. Each array object MUST contain exactly these fields: { "index": 0, "rank": 1, "candidate": "candidate full name", "score": 95, "location": "candidate location", "current_company": "current/latest company", "current_role": "current/latest role", "headline": "candidate headline", "jd_function": "short label for the JD's core function", "candidate_primary_function": "short label for the candidate's actual primary function", "relevant_companies": [ "Company 1", "Company 2" ], "why_it_fits": "Concise evidence-based explanation of why the candidate matches the JD, including the strongest relevant responsibilities, domain/company experience, skills, seniority, location and any important gaps." } The "relevant_companies" field should contain companies from the candidate's career history that are particularly relevant to the JD. Do not put companies there merely because they are famous. The "why_it_fits" field must be concise but evidence-based. Mention important gaps when they materially affect the score. The "index" MUST be copied exactly from the candidate JSON's "index" field -- a small integer, not the candidate's name or any string. Never modify, invent, or guess it. The candidates you receive will NOT contain an "id" field at all -- do not invent one; reference candidates only by "index". Every candidate must appear exactly once in the output. Sort the final JSON array by rank, strongest candidate first. """


# ---------------------------------------------------------------------------
# NEW: a SEPARATE, narrow, evidence-only verification pass.
#
# The hard code-level gates were originally checking for literal keyword
# substrings (e.g. "customer success team") in the candidate's own text.
# That is far too brittle: real resumes paraphrase constantly ("owned the
# CS org", "built our post-sales function", "led retention & expansion")
# and almost never contain the JD's exact extracted phrase, even when the
# person genuinely did the job. That brittleness is what capped 53/80 (and
# ultimately all 80) candidates in a real run despite several of them
# plausibly being legitimate matches.
#
# The fix is NOT to loosen the gate back into holistic scoring (that's the
# original bug -- the LLM averaging one great number against real gaps).
# Instead, this is a second, separate, much NARROWER LLM call whose only
# job is: for each specific named requirement, and for the JD's core
# function, is there genuine textual evidence in THIS candidate's profile?
# It is instructed to be skeptical and to answer false when uncertain,
# and it never sees the holistic score, so it can't rationalize a good
# score into a false "yes". Its per-requirement, evidence-only answers
# feed the same code-level gates as before -- OR'd together with the
# keyword hits, so either a literal phrase match OR a semantic
# verification confirms "satisfied", instead of requiring an exact string.
# ---------------------------------------------------------------------------

_REQUIREMENT_VERIFICATION_PROMPT = """You are a strict, skeptical, evidence-based resume verifier. You are NOT
scoring or ranking candidates -- a separate process does that. Your ONLY job
is to independently check specific factual claims against each candidate's
actual profile text.

You will be given:
- The JD's core function (a short label).
- A list of specific, named must-have requirements.
- A batch of candidate profiles (LinkedIn data: headline, summary, and
  work_experience entries with role/description/company for each job).

For EACH candidate, determine:

1. "function_match": true ONLY if the candidate's ACTUAL demonstrated work
   history (read the real role/description text, not just job titles) is
   genuinely in the same function as the JD's core function, or a closely
   adjacent one where the day-to-day work substantially overlaps. Do NOT
   infer this from job title, seniority, or company reputation alone --
   you must be able to point to real described responsibilities.

2. For EACH named requirement in the list, "true" ONLY if the candidate's
   work_experience descriptions contain genuine, specific, real evidence
   that they actually did that particular thing -- worded however they
   worded it (paraphrases and synonyms count -- "owned the CS org", "led
   retention and expansion", "built our post-sales function" are all valid
   evidence for "led a customer success team", for example). Do NOT
   require the JD's exact wording. But also do NOT mark true from a vague
   resemblance, an inference from seniority, or an assumption based on
   company or title alone -- if you can't point to real descriptive text
   supporting it, mark false.

Be skeptical: your entire purpose is to catch false positives that a more
generous, holistic evaluation might wave through. When genuinely uncertain
after reading the real text, answer false, not true.

Return ONLY a single JSON object, no prose, no markdown fences:

{
  "results": [
    {
      "index": 0,
      "function_match": true,
      "requirement_matches": {
        "<requirement label exactly as given>": true,
        "<requirement label exactly as given>": false
      }
    }
  ]
}

The "index" MUST be copied exactly from the candidate JSON's "index" field --
a small integer, never invented or guessed. Every candidate must appear
exactly once. Every requirement label given to you must appear as a key in
every candidate's "requirement_matches", spelled EXACTLY as given.
"""


def verify_requirements_with_llm(
    jd_function_label: str,
    must_have_requirements: list[dict],
    refactored_items: list,
    max_retries: int = 4,
    base_backoff_seconds: float = 15.0,
) -> str:
    """Calls the LLM once for ONE batch, asking it to verify (not score)
    function match and each named requirement against each candidate's
    real profile text. Same 429 retry/backoff behavior as
    evaluate_candidates_with_llm."""
    print(f"    [LLM verify] Sending batch of {len(refactored_items)} candidate(s) for requirement verification...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    indexed_items = [
        {"index": i, **{k: v for k, v in c.items() if k != "id"}}
        for i, c in enumerate(refactored_items)
    ]

    requirement_labels = [req["requirement"] for req in must_have_requirements]

    user_content = f"""
JD CORE FUNCTION: {jd_function_label}

NAMED REQUIREMENTS TO VERIFY (use these exact labels as JSON keys):
{json.dumps(requirement_labels, ensure_ascii=False, indent=2)}

CANDIDATES:

{json.dumps(indexed_items, ensure_ascii=False, indent=2)}
"""

    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _REQUIREMENT_VERIFICATION_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }

    attempt = 0
    while True:
        attempt += 1
        response = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=120)

        if response.status_code == 429:
            print(f"    [LLM verify] 429 rate-limited (attempt {attempt}/{max_retries}). "
                  f"Body: {response.text[:300]}")
            if attempt >= max_retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else base_backoff_seconds * (2 ** (attempt - 1))
            print(f"    [LLM verify] Waiting {wait_seconds:.0f}s before retrying...")
            time.sleep(wait_seconds)
            continue

        if not response.ok:
            print(f"    [LLM verify] HTTP {response.status_code} error. Body: {response.text[:500]}")
        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        print(f"    [LLM verify] Batch response received ({len(content)} chars).")
        return content


def verify_requirements_in_batches(
    jd_function_label: str,
    must_have_requirements: list[dict],
    refactored_items: list,
    batch_size: int = 15,
) -> dict:
    """Splits refactored_items into batches, verifies each batch, and
    returns a dict keyed by candidate id:
        { id: {"function_match": bool, "requirement_matches": {label: bool}} }
    Candidates whose batch fails to parse, or who are missing/invalid in
    the response, get a conservative default (function_match=False, every
    requirement False) rather than silently being skipped -- a
    verification failure must never accidentally look like a pass."""
    total = len(refactored_items)
    num_batches = math.ceil(total / batch_size) if total else 0
    print(f"\n[STEP 5b] Verifying {total} candidate(s) against {len(must_have_requirements)} "
          f"named requirement(s) in {num_batches} batch(es)...")

    requirement_labels = [req["requirement"] for req in must_have_requirements]
    default_result = {
        "function_match": False,
        "requirement_matches": {label: False for label in requirement_labels},
    }

    verification_by_id: dict = {}

    for batch_num in range(num_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, total)
        chunk = refactored_items[start:end]

        raw = verify_requirements_with_llm(jd_function_label, must_have_requirements, chunk)

        try:
            parsed = json.loads(raw)
            results = parsed.get("results", parsed if isinstance(parsed, list) else [])
        except json.JSONDecodeError as e:
            print(f"[STEP 5b] ❌ Failed to parse verification JSON for batch {batch_num + 1}: {e} "
                  f"-- defaulting this batch's candidates to all-false (conservative).")
            for item in chunk:
                if item.get("id"):
                    verification_by_id[item["id"]] = dict(default_result)
            continue

        seen_indices = set()
        for entry in results:
            idx = entry.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(chunk)):
                continue
            seen_indices.add(idx)
            cid = chunk[idx].get("id")
            if not cid:
                continue
            req_matches = entry.get("requirement_matches") or {}
            # Ensure every requirement label is present, defaulting missing
            # ones to False rather than treating an absent key as a pass.
            normalized = {label: bool(req_matches.get(label, False)) for label in requirement_labels}
            verification_by_id[cid] = {
                "function_match": bool(entry.get("function_match", False)),
                "requirement_matches": normalized,
            }

        # Any candidate in this chunk the LLM didn't return at all -> conservative default.
        for i, item in enumerate(chunk):
            if i not in seen_indices and item.get("id"):
                verification_by_id.setdefault(item["id"], dict(default_result))

        print(f"[STEP 5b] Batch {batch_num + 1}/{num_batches} verified.")

    print(f"[STEP 5b] Done. Verified {len(verification_by_id)}/{total} candidate(s).")
    return verification_by_id


def _call_openai_json(system_prompt: str, user_content: str) -> dict:
    """Calls the LLM once, with temperature 0 and strict JSON output.
    Raises on anything that isn't valid, parseable JSON -- callers must
    not fall back to guessing when this fails."""
    print(f"    [OpenAI] Calling gpt-4o-mini (prompt length: {len(user_content)} chars)...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
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
    print(f"    [OpenAI] Response received ({len(content)} chars). Parsing JSON...")
    parsed = json.loads(content)  # raises if the model didn't return clean JSON
    print(f"    [OpenAI] JSON parsed successfully.")
    return parsed


def _clean_must_have_requirements(raw: list) -> list[dict]:
    """Validates/cleans the must_have_requirements list: each entry must be
    a dict with a non-empty 'requirement' label and a non-empty list of
    'keywords'. Anything malformed is dropped rather than crashing the
    pipeline on a slightly-off LLM response."""
    cleaned: list[dict] = []
    seen_labels: set[str] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        req = (entry.get("requirement") or "").strip()
        kws_raw = entry.get("keywords") or []
        if not req or req.lower() in seen_labels:
            continue
        kws: list[str] = []
        seen_kw: set[str] = set()
        for kw in kws_raw:
            kw = (kw or "").strip().lower()
            if kw and kw not in seen_kw:
                seen_kw.add(kw)
                kws.append(kw)
        if not kws:
            continue
        seen_labels.add(req.lower())
        cleaned.append({"requirement": req, "keywords": kws})
    return cleaned


def extract_signals(job_description: str, company_min_n: int = 25, company_max_n: int = 50) -> dict:
    """The ONE OpenAI call. Returns job_titles, locations, companies,
    core_function_keywords, and must_have_requirements -- all still just
    text/keywords, no candidate ids yet."""
    print("\n[STEP 1] Extracting job titles, locations, companies, core function keywords, "
          "and must-have requirements from JD via LLM...")
    prompt = _EXTRACTION_AND_COMPANY_PROMPT.format(min_n=company_min_n, max_n=company_max_n)
    data = _call_openai_json(prompt, job_description)

    data.setdefault("job_titles", [])
    data.setdefault("locations", [])
    data.setdefault("companies", [])
    data.setdefault("core_function_keywords", [])
    data.setdefault("must_have_requirements", [])

    # de-dup, preserve order, drop empties
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
    data["core_function_keywords"] = _clean(data["core_function_keywords"])
    data["must_have_requirements"] = _clean_must_have_requirements(data["must_have_requirements"])

    print(f"[STEP 1] Done. job_titles={data['job_titles']}")
    print(f"[STEP 1] Done. locations={data['locations']}")
    print(f"[STEP 1] Done. companies ({len(data['companies'])} total)={data['companies']}")
    print(f"[STEP 1] Done. core_function_keywords={data['core_function_keywords']}")
    print(f"[STEP 1] Done. must_have_requirements ({len(data['must_have_requirements'])} total):")
    for req in data["must_have_requirements"]:
        print(f"    - {req['requirement']}: {req['keywords']}")

    return data


# ---------------------------------------------------------------------------
# Resolve names to real Unipile ids (the ONLY source of ids)
# ---------------------------------------------------------------------------

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
    """Real LOCATION ids per location phrase, de-duplicated in order.

    Requests `per_location_limit` results per phrase (default 5) and
    keeps whatever actually comes back -- if a phrase only matches 2
    real places, you get 2; if it matches 5+, you get the top 5. Nothing
    is ever padded to hit a fixed count."""
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
    """First real COMPANY match per name, in the same order as the input
    list. Names with zero matches are silently dropped."""
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


# ---------------------------------------------------------------------------
# Filename helper + save-to-disk
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """'Senior Vice President / Chief Customer Officer' ->
    'senior_vice_president_chief_customer_officer'"""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "role"


def save_payload_to_file(payload: dict, role_name: str, output_dir: str = ".") -> str:
    """Writes `payload` as pretty-printed JSON to
    `{output_dir}/{slugified role_name}_payload.json` and returns the path.
    Overwrites if a file with that name already exists."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{_slugify(role_name)}_payload.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"    [save] Payload written to {path}")
    return path


# ---------------------------------------------------------------------------
# Assemble the final payload: role, location, current_company ONLY
# ---------------------------------------------------------------------------

def build_payload(
        job_description: str,
        company_min_n: int = 25,
        company_max_n: int = 50,
        save_to_dir: str = "./payloads",
) -> dict:
    """
    Always saves the final payload to
    '{save_to_dir}/{role_name}_payload.json' (role_name = the first
    title returned in `job_titles`, or 'role' if none were extracted).
    Pass save_to_dir=None only if you explicitly want to skip writing a
    file and just get the dict back.

    Returns (payload, companies_list, locations_list, core_function_keywords,
    must_have_requirements).
    """
    print("\n========== BUILD_PAYLOAD START ==========")
    signals = extract_signals(job_description, company_min_n, company_max_n)

    payload: dict = {"api": "recruiter", "category": "people"}

    # --- role ---
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

    # --- location (scope always CURRENT, per spec) ---
    location_ids = resolve_location_ids(signals["locations"], per_location_limit=5)
    if location_ids:
        payload["location"] = [
            {"id": _id, "priority": "CAN_HAVE", "scope": "CURRENT"} for _id in location_ids
        ]

    # --- current_company ---
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

    return (
        payload,
        signals["companies"],
        signals["locations"],
        signals["core_function_keywords"],
        signals["must_have_requirements"],
    )


# ---------------------------------------------------------------------------
# Run the actual LinkedIn search via Unipile, using the payload built above
# ---------------------------------------------------------------------------

def search_candidate(
        payload: dict,
        target_count: int = 500,
        save_to_dir: str = "./search_results",
) -> dict:
    """POSTs `payload` (as returned by build_payload) to Unipile's
    /api/v1/linkedin/search, following `cursor` across pages (100 per
    request, the API's per-page cap) until it has collected
    `target_count` candidates OR runs out of real results -- whichever
    comes first. If the search only has fewer than `target_count` total
    matches, every one of them is returned; nothing is padded to hit the
    target.

    Returns {'items': [...], 'total_count': int|None,
    'collected_count': int, '_saved_to': path}. Strips internal
    bookkeeping keys ('_saved_to', '_role_name') from the outgoing
    request body, since those aren't part of Unipile's schema. Pass
    save_to_dir=None to skip saving."""
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
    page_limit = 100  # Unipile's per-request cap for recruiter/sales_navigator
    page_num = 0

    while len(all_items) < target_count:
        page_num += 1
        params: dict = {
            "limit": min(page_limit, target_count - len(all_items)),
            "account_id": UNIPILE_ACCOUNT_ID,
        }
        if cursor:
            params["cursor"] = cursor

        print(f"    [page {page_num}] Requesting up to {params['limit']} results (cursor={'yes' if cursor else 'none'})...")
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
            break  # no more real results to page through
        if total_count is not None and len(all_items) >= total_count:
            print(f"    [page {page_num}] Collected every reported match -- stopping pagination.")
            break  # collected every real match that exists

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


def refactor_result_for_evaluation(result: dict) -> list:
    print(f"\n[STEP 4] Refactoring {len(result.get('items', []))} raw candidate profile(s) "
          f"down to the fields the evaluator LLM needs...")
    required_keys = {
        "id",
        "headline",
        "location",
        "name",
        "industry",
        "skills",
        "summary",
        "education",
        "work_experience",
        "certifications",
    }

    refactored_items = []

    for profile in result.get("items", []):
        refactored_profile = {
            key: profile[key]
            for key in required_keys
            if key in profile
        }

        if "work_experience" in refactored_profile:
            refactored_profile["work_experience"] = [
                {
                    key: experience[key]
                    for key in [
                        "company",
                        "company_id",
                        "industry",
                        "location",
                        "role",
                        "start",
                        "end",
                        "description",
                        "skills",
                    ]
                    if key in experience
                }
                for experience in refactored_profile["work_experience"]
            ]

        if "skills" in refactored_profile:
            refactored_profile["skills"] = [
                skill["name"]
                for skill in refactored_profile["skills"]
                if skill.get("name")
            ]

        if "education" in refactored_profile:
            refactored_profile["education"] = [
                {
                    key: education_item[key]
                    for key in [
                        "degree",
                        "school",
                        "field_of_study",
                        "start",
                        "end",
                    ]
                    if key in education_item
                }
                for education_item in refactored_profile["education"]
            ]

        if "certifications" in refactored_profile:
            refactored_profile["certifications"] = [
                {
                    key: certification[key]
                    for key in [
                        "name",
                        "organization",
                        "start",
                    ]
                    if key in certification
                }
                for certification in refactored_profile["certifications"]
            ]

        refactored_items.append(refactored_profile)

    print(f"[STEP 4] Done. Refactored {len(refactored_items)} candidate profile(s).")
    return refactored_items


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


def evaluate_candidates_with_llm(
    modified_jd: str,
    refactored_items: list,
    max_retries: int = 4,
    base_backoff_seconds: float = 15.0,
) -> str:
    """Evaluates ONE batch of candidates. Retries on HTTP 429 (rate limit)
    with exponential backoff, honoring the Retry-After header when OpenAI
    sends one. Raises on any other HTTP error, or if retries are
    exhausted."""
    print(f"    [LLM eval] Sending batch of {len(refactored_items)} candidate(s) to LLM...")
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    # Strip the real id out and give the model a small local index instead --
    # small integers are transcribed reliably; long opaque id strings are not.
    indexed_items = [
        {"index": i, **{k: v for k, v in c.items() if k != "id"}}
        for i, c in enumerate(refactored_items)
    ]

    user_content = f"""
JOB DESCRIPTION:

{modified_jd}


CANDIDATES:

{json.dumps(indexed_items, ensure_ascii=False, indent=2)}
"""

    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": _CANDIDATE_EVALUATION_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
    }

    attempt = 0
    while True:
        attempt += 1
        response = requests.post(
            OPENAI_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )

        if response.status_code == 429:
            print(f"    [LLM eval] 429 rate-limited (attempt {attempt}/{max_retries}). "
                  f"Body: {response.text[:300]}")
            if attempt >= max_retries:
                print(f"    [LLM eval] Out of retries -- raising.")
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                wait_seconds = float(retry_after)
            else:
                wait_seconds = base_backoff_seconds * (2 ** (attempt - 1))
            print(f"    [LLM eval] Waiting {wait_seconds:.0f}s before retrying...")
            time.sleep(wait_seconds)
            continue

        if not response.ok:
            print(f"    [LLM eval] HTTP {response.status_code} error. Body: {response.text[:500]}")

        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]
        print(f"    [LLM eval] Batch response received ({len(content)} chars).")
        return content


def evaluate_candidates_in_batches(
    modified_jd: str,
    refactored_items: list,
    batch_size: int = 25,
) -> Optional[list]:
    """Splits refactored_items into chunks of `batch_size`, sends each
    chunk to evaluate_candidates_with_llm separately (so a single call
    never gets big enough to trip a token-per-minute rate limit), parses
    each batch's JSON array, and merges every batch's candidates into
    ONE final list -- re-sorted by score (highest first) and re-numbered
    1..N so the ranks are consistent across the whole merged set, not
    just within each batch. Returns None if any batch fails to parse."""
    total = len(refactored_items)
    num_batches = math.ceil(total / batch_size) if total else 0
    print(f"\n[STEP 5] Evaluating {total} candidate(s) in {num_batches} batch(es) of up to {batch_size}...")

    merged: list = []

    for batch_num in range(num_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, total)
        chunk = refactored_items[start:end]

        print(f"\n[STEP 5] Batch {batch_num + 1}/{num_batches}: candidates {start}-{end - 1} ({len(chunk)} total)")
        raw = evaluate_candidates_with_llm(modified_jd, chunk)

        try:
            batch_result = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[STEP 5] ❌ Failed to parse LLM evaluation JSON for batch {batch_num + 1}: {e}")
            return None

        # The evaluator can return either a bare JSON array, or an object
        # wrapping one (e.g. {"candidates": [...]}) -- handle both.
        if isinstance(batch_result, dict):
            list_values = [v for v in batch_result.values() if isinstance(v, list)]
            if not list_values:
                print(f"[STEP 5] ❌ Batch {batch_num + 1} returned an object with no list inside it.")
                return None
            batch_result = list_values[0]

        print(f"[STEP 5] Batch {batch_num + 1}/{num_batches} done. Got {len(batch_result)} scored candidate(s).")

        # Resolve each candidate's small "index" back to the real id/name/location
        # from `chunk` (ground truth) -- never trust the LLM's own copy of these.
        for cand in batch_result:
            idx = cand.pop("index", None)
            if isinstance(idx, int) and 0 <= idx < len(chunk):
                source = chunk[idx]
                cand["id"] = source.get("id")
                cand["candidate"] = source.get("name")
                cand["location"] = source.get("location")
            else:
                cand["id"] = None
                cand["_index_flag"] = "invalid_or_missing_index"
                print(f"[STEP 5] WARNING: batch {batch_num + 1} candidate has bad/missing "
                      f"index={idx!r} -- id/name/location left unresolved.")

        merged.extend(batch_result)

    # Re-rank across the WHOLE merged set, not per-batch, so rank 1 is the
    # strongest candidate overall.
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, candidate in enumerate(merged, start=1):
        candidate["rank"] = i

    print(f"\n[STEP 5] All batches merged and re-ranked. {len(merged)} candidate(s) total, "
          f"top score={merged[0].get('score') if merged else 'n/a'}.")

    return merged


# ---------------------------------------------------------------------------
# NEW: independent, code-level function-match gate.
#
# The system prompt now asks the LLM to self-report jd_function /
# candidate_primary_function and cap its own score -- but small models
# (gpt-4o-mini) can and do ignore soft instructions like that under load.
# This gate does NOT trust the model's self-report; it independently
# checks the candidate's own text (headline/summary/work history) for the
# JD's core function keywords, extracted separately in STEP 1. If none of
# those keywords appear anywhere in the candidate's real profile text, the
# score is force-capped in code, regardless of what the LLM decided.
# ---------------------------------------------------------------------------

def enforce_function_gate(
    merged: list,
    refactored_items: list,
    core_function_keywords: list[str],
    verification_by_id: Optional[dict] = None,
    cap_score: float = 45,
) -> list:
    """Independent check: does ANY evidence in the candidate's
    headline/summary/work_experience actually mention the JD's core
    function -- either as a literal keyword hit, OR (when
    verification_by_id is provided) via the separate, narrower LLM
    verification pass, which understands paraphrases that plain substring
    matching misses. A candidate is only capped if BOTH signals say no
    evidence exists. If not, force the score down regardless of what the
    original holistic scoring LLM said."""
    if not core_function_keywords and not verification_by_id:
        print("[function_gate] No core_function_keywords and no verification data -- skipping gate.")
        return merged

    keywords = [k.lower() for k in core_function_keywords]
    by_id = {item["id"]: item for item in refactored_items if item.get("id")}

    capped_count = 0
    for cand in merged:
        cid = cand.get("id")
        item = by_id.get(cid)
        if not item:
            continue

        text_blobs = [item.get("headline", "") or "", item.get("summary", "") or ""]
        for exp in item.get("work_experience", []):
            text_blobs.append(exp.get("role", "") or "")
            text_blobs.append(exp.get("description", "") or "")
        haystack = " ".join(text_blobs).lower()

        keyword_match = any(kw in haystack for kw in keywords) if keywords else False
        verified_match = bool((verification_by_id or {}).get(cid, {}).get("function_match", False))
        matched = keyword_match or verified_match

        if not matched:
            original_score = cand.get("score", 0)
            if original_score > cap_score:
                print(f"    [function_gate] '{cand.get('candidate')}' scored "
                      f"{original_score} but no function evidence found (keyword or "
                      f"verified) -- capping to {cap_score}")
                cand["score"] = cap_score
                cand["_function_gate_flag"] = "capped_no_function_evidence"
                capped_count += 1

    print(f"[function_gate] Done. {capped_count} candidate(s) capped for missing function evidence.")

    # re-sort/re-rank after capping
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(merged, start=1):
        c["rank"] = i
    return merged


# ---------------------------------------------------------------------------
# NEW: independent, code-level MUST-HAVE requirement gate.
#
# The coarse function_gate above only catches a candidate with ZERO overlap
# with the JD's function (e.g. a procurement exec applied against a customer
# success JD). It does NOT catch the more common, more dangerous failure
# mode: a candidate who genuinely IS in an adjacent function (real customer/
# revenue leadership) but is missing several of the JD's specific, named
# must-have responsibilities (e.g. "led a customer success team", "led a
# pre-sales team", "professional services delivery"). The LLM's holistic
# scoring can let one spectacular business-impact number (e.g. "$3B ARR")
# drown out two completely unaddressed must-haves -- and the LLM's own
# self-reported jd_function/candidate_primary_function labels can't be
# trusted to catch this, since the model can (and did, in production)
# reword its own labels to make a partial match look like a full one.
#
# This gate re-checks, independently and in code, whether each specific
# must-have requirement (extracted in STEP 1, each with its own keyword
# set) has ANY textual evidence in the candidate's real profile. It then
# caps the score based on how many of the JD's must-haves are completely
# unaddressed -- regardless of what the LLM decided.
# ---------------------------------------------------------------------------

# Fraction of must-haves with zero evidence -> score cap. Interpolated
# between the nearest two breakpoints below; a candidate missing 100% of
# must-haves is capped the same as a full function mismatch (45).
_MUST_HAVE_MISS_CAPS = [
    (0.0, 100),   # no requirements missing -> no cap from this gate
    (0.34, 80),   # up to ~1/3 missing -> cap at 80 (blocks "Strong+" claims)
    (0.67, 65),   # up to ~2/3 missing -> cap at 65 (blocks even "Moderate+")
    (1.0, 45),    # all missing -> same cap as a full function mismatch
]


def _cap_for_miss_ratio(miss_ratio: float) -> float:
    for threshold, cap in _MUST_HAVE_MISS_CAPS:
        if miss_ratio <= threshold:
            return cap
    return _MUST_HAVE_MISS_CAPS[-1][1]


def enforce_must_have_gate(
    merged: list,
    refactored_items: list,
    must_have_requirements: list[dict],
    verification_by_id: Optional[dict] = None,
) -> list:
    """Independent check: for each of the JD's specific must-have
    requirements, does the candidate's own profile text contain ANY of
    that requirement's keywords, OR (when verification_by_id is provided)
    did the separate, narrower LLM verification pass find genuine
    paraphrased evidence for it? A requirement counts as satisfied if
    EITHER signal says yes -- this fixes literal keyword matching being
    too brittle against real, paraphrased resume text, while still never
    trusting the original holistic-scoring LLM's own self-reported match
    labels, which can be reworded to look aligned. Candidates missing a
    large share of the JD's named must-haves get their score capped in
    code regardless of what the holistic score said."""
    if not must_have_requirements:
        print("[must_have_gate] No must_have_requirements extracted -- skipping gate.")
        return merged

    by_id = {item["id"]: item for item in refactored_items if item.get("id")}
    total_reqs = len(must_have_requirements)

    capped_count = 0
    for cand in merged:
        cid = cand.get("id")
        item = by_id.get(cid)
        if not item:
            continue

        text_blobs = [item.get("headline", "") or "", item.get("summary", "") or ""]
        for exp in item.get("work_experience", []):
            text_blobs.append(exp.get("role", "") or "")
            text_blobs.append(exp.get("description", "") or "")
        haystack = " ".join(text_blobs).lower()

        verified_matches = (verification_by_id or {}).get(cid, {}).get("requirement_matches", {})

        missing: list[str] = []
        for req in must_have_requirements:
            keyword_hit = any(kw in haystack for kw in req["keywords"])
            verified_hit = bool(verified_matches.get(req["requirement"], False))
            if not (keyword_hit or verified_hit):
                missing.append(req["requirement"])

        miss_ratio = len(missing) / total_reqs
        cap = _cap_for_miss_ratio(miss_ratio)

        original_score = cand.get("score", 0)
        cand["_must_have_missing"] = missing
        cand["_must_have_total"] = total_reqs

        if original_score > cap:
            print(f"    [must_have_gate] '{cand.get('candidate')}' scored {original_score} "
                  f"but is missing {len(missing)}/{total_reqs} named must-haves "
                  f"({missing}) -- capping to {cap}")
            cand["score"] = cap
            cand["_must_have_gate_flag"] = "capped_missing_requirements"
            capped_count += 1

    print(f"[must_have_gate] Done. {capped_count} candidate(s) capped for missing must-have requirements.")

    # re-sort/re-rank after capping
    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(merged, start=1):
        c["rank"] = i
    return merged



# ---------------------------------------------------------------------------
# NEW: independent, code-level COMPANY-DOMAIN gate.
#
# This implements the specific ordering Rahul asked for: title matches the
# search filter (STEP 2, unchanged); then company relevance; then, and this
# is the part nothing else checks, whether the candidate's ACTUAL ROLE at
# that specific relevant company was in the JD's domain -- not just "worked
# at a good-fit company at some point, in some unrelated capacity."
#
# A candidate can pass enforce_function_gate and enforce_must_have_gate
# using domain evidence from ANY employer on their resume. This gate adds a
# narrower, additional check: when the candidate has time at one of the
# JD's own relevant companies, was THAT SPECIFIC STINT actually in-domain?
# If they worked at a perfect-fit company but in an unrelated function
# there (e.g. procurement at a SaaS company, engineering at a customer-
# success-heavy company), this flags it -- so "great company" evidence
# can't get credited as "great company, great function" when it wasn't.
#
# If the candidate has NO experience at any of the JD's relevant companies
# at all, this gate does not penalize them -- that is a separate, softer
# signal (company relevance / transferable experience) already handled by
# the LLM's holistic scoring, not a hard requirement.
# ---------------------------------------------------------------------------

def enforce_company_domain_gate(
    merged: list,
    refactored_items: list,
    relevant_companies: list[str],
    core_function_keywords: list[str],
    must_have_requirements: list[dict],
    verification_by_id: Optional[dict] = None,
    cap_score: float = 55,
) -> list:
    """Independent check: for a candidate who has time at one of the JD's
    own relevant companies, was that SPECIFIC stint actually in the JD's
    domain? Checked via literal keyword hits in that stint's text, OR (as
    an exoneration, since per-stint LLM verification isn't run separately)
    the overall verification pass already confirming a genuine, paraphrase-
    aware function_match for this candidate. If neither signal shows
    domain evidence at the relevant company, cap the score -- this catches
    'right company, wrong function while there', which the broader
    function/must-have gates (which scan the WHOLE resume) can miss."""
    if not relevant_companies:
        print("[company_domain_gate] No relevant_companies extracted -- skipping gate.")
        return merged

    domain_keywords = [k.lower() for k in core_function_keywords]
    for req in must_have_requirements:
        domain_keywords.extend(req["keywords"])
    if not domain_keywords and not verification_by_id:
        print("[company_domain_gate] No domain keywords or verification data available -- skipping gate.")
        return merged

    companies_lower = [c.lower() for c in relevant_companies]
    by_id = {item["id"]: item for item in refactored_items if item.get("id")}

    capped_count = 0
    for cand in merged:
        cid = cand.get("id")
        item = by_id.get(cid)
        if not item:
            continue

        relevant_stints = []
        for exp in item.get("work_experience", []):
            company_name = (exp.get("company") or "").lower()
            if any(rc in company_name or company_name in rc for rc in companies_lower if company_name):
                relevant_stints.append(exp)

        if not relevant_stints:
            # No experience at a JD-relevant company at all -- not this
            # gate's job to penalize that; leave to holistic scoring.
            continue

        stint_text = " ".join(
            (exp.get("role", "") or "") + " " + (exp.get("description", "") or "")
            for exp in relevant_stints
        ).lower()

        keyword_hit = any(kw in stint_text for kw in domain_keywords)
        # Exoneration: if the separate verification pass already found a
        # genuine, paraphrase-aware function match for this candidate
        # overall, don't fail them here just because their relevant-
        # company stint happened not to contain a literal keyword --
        # per-stint LLM verification isn't run separately for cost reasons.
        verified_overall = bool((verification_by_id or {}).get(cid, {}).get("function_match", False))
        in_domain_while_there = keyword_hit or verified_overall

        if not in_domain_while_there:
            original_score = cand.get("score", 0)
            company_names = sorted({exp.get("company") for exp in relevant_stints if exp.get("company")})
            if original_score > cap_score:
                print(f"    [company_domain_gate] '{cand.get('candidate')}' scored {original_score}, "
                      f"has time at relevant company/companies {company_names} but that specific "
                      f"experience shows no domain evidence -- capping to {cap_score}")
                cand["score"] = cap_score
                cand["_company_domain_gate_flag"] = "capped_offdomain_at_relevant_company"
                cand["_company_domain_gate_companies"] = company_names
                capped_count += 1

    print(f"[company_domain_gate] Done. {capped_count} candidate(s) capped for off-domain "
          f"experience at an otherwise-relevant company.")

    merged.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(merged, start=1):
        c["rank"] = i
    return merged


# ---------------------------------------------------------------------------
# NEW: human-review report. Prints a quick, scannable summary so a person
# can look at the JD + this output and agree/disagree with "perfect match"
# without having to re-read every raw candidate JSON blob.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# NEW: strict, zero-manual-intervention qualification filter.
#
# Everything above (the three gates) CAPS a score -- it's a signal, not a
# removal. The requirement here is stronger: every named must-have is
# non-negotiable, and a candidate missing even one is "of no use" and
# should not appear in the final list at all, with no human reviewing
# scores to decide. This function is the hard cut that makes the pipeline
# fully automatic: it does not look at score at all, only at whether each
# specific, independently-checked requirement was actually satisfied.
#
# A candidate is KEPT only if ALL of the following hold:
#   1. enforce_function_gate found real function overlap (no
#      _function_gate_flag).
#   2. enforce_must_have_gate found evidence for EVERY named must-have
#      (_must_have_missing is empty) -- not "most", not "the important
#      ones", ALL of them.
#   3. enforce_company_domain_gate didn't catch them coasting on
#      off-domain time at an otherwise-relevant company
#      (no _company_domain_gate_flag).
# Everyone else is dropped entirely, not ranked lower -- matching "if
# anything is not there then they are of no use."
# ---------------------------------------------------------------------------

def filter_qualified_candidates(merged: list) -> list:
    """Hard, code-level filter -- no score threshold, no human judgment
    call. Keeps only candidates who independently satisfy every gate.
    Everyone else is removed from the list entirely. Re-ranks the survivors
    1..N by score so rank 1 is still the strongest among the qualified."""
    qualified = [
        c for c in merged
        if not c.get("_function_gate_flag")
        and not c.get("_must_have_missing")  # empty list/None == nothing missing
        and not c.get("_company_domain_gate_flag")
    ]

    dropped = len(merged) - len(qualified)
    print(f"\n[qualify] {len(qualified)}/{len(merged)} candidate(s) satisfy EVERY named "
          f"must-have with no gate flags -- {dropped} dropped entirely as not usable "
          f"for this JD.")

    qualified.sort(key=lambda c: c.get("score", 0), reverse=True)
    for i, c in enumerate(qualified, start=1):
        c["rank"] = i
    return qualified


def print_human_review_report(final_result: list, top_n: int = 10) -> None:
    """Prints the top_n ranked candidates with their score, every gate
    flag that fired, and what's missing -- so a human reviewer can sanity
    check the ranking against the JD in under a minute, instead of trusting
    the score blindly."""
    print("\n================== HUMAN REVIEW REPORT ==================")
    for cand in final_result[:top_n]:
        flags = [
            f for f in (
                cand.get("_function_gate_flag"),
                cand.get("_must_have_gate_flag"),
                cand.get("_company_domain_gate_flag"),
            ) if f
        ]
        print(f"\n#{cand.get('rank')}  {cand.get('candidate')}  --  score {cand.get('score')}")
        print(f"    Current: {cand.get('current_role')} @ {cand.get('current_company')}")
        print(f"    Location: {cand.get('location')}")
        if flags:
            print(f"    ⚠️  GATE FLAGS: {flags}")
        missing = cand.get("_must_have_missing")
        if missing:
            print(f"    Missing must-haves: {missing}")
        off_domain_companies = cand.get("_company_domain_gate_companies")
        if off_domain_companies:
            print(f"    Off-domain despite time at: {off_domain_companies}")
        if not flags:
            print(f"    ✅ No gate flags -- score reflects the LLM's holistic evaluation, unmodified.")
        print(f"    Why it fits (LLM): {cand.get('why_it_fits')}")
    print("\n===========================================================\n")


def enrich_with_profile_media(evaluated: list[dict], raw_items: list[dict]) -> list[dict]:
    """Re-attaches public_profile_url and profile_picture_url from the raw
    Unipile search results onto each evaluated candidate, matched by id.
    These fields are dropped in refactor_result_for_evaluation since the
    LLM doesn't need them, so they have to come back from the original
    search response, not from the LLM output."""
    media_by_id = {
        item["id"]: {
            "public_profile_url": item.get("public_profile_url"),
            "profile_picture_url": item.get("profile_picture_url"),
        }
        for item in raw_items
        if item.get("id")
    }

    for cand in evaluated:
        media = media_by_id.get(cand.get("id"))
        if media:
            cand["public_profile_url"] = media["public_profile_url"]
            cand["profile_picture_url"] = media["profile_picture_url"]
        else:
            cand["public_profile_url"] = None
            cand["profile_picture_url"] = None
            print(f"    [enrich] no raw match for id={cand.get('id')} "
                  f"('{cand.get('candidate')}') -- media urls left null")

    return evaluated


def reconcile_candidate_ids(evaluated: list[dict], refactored_items: list[dict]) -> list[dict]:
    """Cross-checks every evaluated candidate's id against refactored_items
    (the real Unipile results, the only source of truth for id<->name).
    If the id doesn't exist there, tries to recover it via an exact name
    match. Flags anything it can't resolve instead of silently trusting
    the LLM's echoed id."""
    id_to_item = {item["id"]: item for item in refactored_items if item.get("id")}
    name_to_ids: dict[str, list[str]] = {}
    for item in refactored_items:
        name_to_ids.setdefault((item.get("name") or "").strip().lower(), []).append(item["id"])

    fixed = []
    for cand in evaluated:
        cid = cand.get("id")
        cname = (cand.get("candidate") or "").strip().lower()

        if cid in id_to_item:
            true_name = (id_to_item[cid].get("name") or "").strip().lower()
            if true_name and true_name != cname:
                cand["_id_flag"] = "id_valid_but_name_mismatch"
                print(f"[reconcile] WARNING id={cid} is really '{true_name}', "
                      f"LLM labeled it '{cand.get('candidate')}'")
            fixed.append(cand)
            continue

        matches = name_to_ids.get(cname, [])
        if len(matches) == 1:
            print(f"[reconcile] corrected id for '{cand.get('candidate')}': {cid} -> {matches[0]}")
            cand["id"] = matches[0]
            cand["_id_flag"] = "corrected_via_name"
        elif len(matches) > 1:
            cand["_id_flag"] = "ambiguous_name_match"
            print(f"[reconcile] AMBIGUOUS name '{cname}' -> {matches}, can't auto-fix")
        else:
            cand["_id_flag"] = "unresolved"
            print(f"[reconcile] UNRESOLVED: bad id={cid}, name='{cand.get('candidate')}' not found at all")

        fixed.append(cand)
    return fixed


def create_unipile_recruiter_project(
    project_name: str,
    visibility: str = "PRIVATE",
) -> dict | None:
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
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code in (200, 201):
            response = response.json()
            project_id = response.get("project_id")
            print(f"[STEP 6] Project created successfully. project_id={project_id}")
            return project_id

        print(
            f"❌ Unipile project creation failed: "
            f"{response.status_code} {response.text[:300]}"
        )
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
    min_score: float = 80,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
    require_clean_function_gate: bool = True,
    require_clean_must_have_gate: bool = True,
    require_clean_company_domain_gate: bool = True,
) -> None:
    """Adds every eligible candidate to the given Unipile recruiter
    project's pipeline, one at a time, sleeping `delay_seconds` BETWEEN
    consecutive requests (not before the first, not after the last).
    Meant to run on a background thread.

    CHANGED: min_score default raised from 70 -> 80, and by default a
    candidate flagged by ANY of the three gates
    (_function_gate_flag == 'capped_no_function_evidence',
    _must_have_gate_flag == 'capped_missing_requirements', or
    _company_domain_gate_flag == 'capped_offdomain_at_relevant_company')
    is excluded even if something upstream pushed its score back up --
    belt and braces against a single bad auto-push reaching the client's
    pipeline.
    """
    eligible = [
        c for c in candidates
        if (c.get("score") or 0) >= min_score
        and not (require_clean_function_gate and c.get("_function_gate_flag") == "capped_no_function_evidence")
        and not (require_clean_must_have_gate and c.get("_must_have_gate_flag") == "capped_missing_requirements")
        and not (require_clean_company_domain_gate and c.get("_company_domain_gate_flag") == "capped_offdomain_at_relevant_company")
    ]
    print(f"\n[STEP 7] Adding {len(eligible)} candidate(s) with score >= {min_score} "
          f"(function-gate clean={require_clean_function_gate}, "
          f"must-have-gate clean={require_clean_must_have_gate}, "
          f"company-domain-gate clean={require_clean_company_domain_gate}) "
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
    min_score: float = 80,
    delay_seconds: float = 20.0,
    stage: str = "UNCONTACTED",
    require_clean_function_gate: bool = True,
    require_clean_must_have_gate: bool = True,
    require_clean_company_domain_gate: bool = True,
) -> threading.Thread:
    """Kicks off add_candidates_to_pipeline_with_delay on a daemon thread
    and returns immediately, so main_function can hand final_result back
    to the caller right away while the adds happen in the background."""
    thread = threading.Thread(
        target=add_candidates_to_pipeline_with_delay,
        args=(candidates, hiring_project_id),
        kwargs={
            "min_score": min_score,
            "delay_seconds": delay_seconds,
            "stage": stage,
            "require_clean_function_gate": require_clean_function_gate,
            "require_clean_must_have_gate": require_clean_must_have_gate,
            "require_clean_company_domain_gate": require_clean_company_domain_gate,
        },
        daemon=True,
    )
    thread.start()
    print(f"[STEP 7] Background pipeline-add thread started (thread_id={thread.ident}).")
    return thread


# main function
def main_function(
    jd: str,
    project_name: str,
    hiring_for: str,
    min_score_for_auto_add: float = 0,
):
    """
    CHANGED behavior vs. the original:

    1. Extraction now also returns core_function_keywords AND
       must_have_requirements (a list of {requirement, keywords} objects
       for each specific, named must-have in the JD), threaded into the
       evaluator's JD text as extra context lines.
    2. THREE independent, code-level gates run after
       evaluate_candidates_in_batches(), before anything else touches the
       score:
         - enforce_function_gate(): catches a candidate with ZERO overlap
           with the JD's function (e.g. procurement exec on a customer
           success JD).
         - enforce_must_have_gate(): flags exactly which of the JD's
           SPECIFIC named must-haves each candidate is missing.
         - enforce_company_domain_gate(): catches "right company, wrong
           function while there" -- time at a relevant company that
           wasn't actually in the JD's domain.
       None of the three trust the LLM's own self-reported match labels --
       all independently re-scan the candidate's actual profile text,
       because the LLM can (and has, in production) reworded its own
       labels to make a partial match look like a full one.
    3. filter_qualified_candidates() then applies a HARD, automatic cut: a
       candidate survives ONLY if they have real function overlap, ZERO
       missing must-haves (not "most" -- every single one), and no
       off-domain-at-relevant-company flag. Everyone else is removed from
       the list entirely, with no score threshold and no human reviewing
       the list to decide -- "missing anything means not usable for this
       JD" is enforced automatically, in code.
    4. CHANGED: pipeline adds now run SYNCHRONOUSLY (add_candidates_to_
       pipeline_with_delay called directly, not backgrounded on a daemon
       thread). The previous background-thread version returned control
       to the caller immediately, and since this is a standalone script
       with nothing else running, the process exited right after starting
       the thread -- Python kills daemon threads on interpreter exit, so
       zero candidates were ever actually added. There is no reason to
       background this work in a one-shot script, so we just wait for it.
    """
    print("\n################## PIPELINE START ##################")
    print(f"Project name: {project_name}")
    print(f"Hiring for: {hiring_for}")
    print(f"JD length: {len(jd)} chars")

    payload, companies_list, locations_list, core_function_keywords, must_have_requirements = build_payload(jd)

    result = search_candidate(payload)

    must_have_lines = "\n".join(
        f"- {req['requirement']}" for req in must_have_requirements
    ) or "(none extracted)"

    modified_jd = (
        jd
        + "\n\nHiring For: "
        + hiring_for
        + "\nRequired Companies: "
        + ", ".join(companies_list)
        + "\nRequired Locations: "
        + ", ".join(locations_list)
        + "\nRequired Core Function: "
        + ", ".join(core_function_keywords)
        + "\nNamed Must-Have Requirements:\n"
        + must_have_lines
    )
    print(f"\n[STEP 4b] JD annotated with hiring_for, {len(companies_list)} required companies, "
          f"{len(locations_list)} required locations, {len(core_function_keywords)} "
          f"core function keywords, and {len(must_have_requirements)} named must-have "
          f"requirements for the evaluator.")

    refactored_items = refactor_result_for_evaluation(result)

    final_result = evaluate_candidates_in_batches(
        modified_jd,
        refactored_items,
        batch_size=15,
    )

    if final_result is None:
        print("❌ Candidate evaluation failed -- see batch errors above.")
        return None, None

    # A separate, narrow, evidence-only verification pass (distinct from
    # the holistic scoring call above). This is what makes the gates below
    # robust to real, paraphrased resume text instead of requiring an
    # exact keyword substring match.
    jd_function_label = ", ".join(core_function_keywords) or "the JD's core responsibilities"
    verification_by_id = verify_requirements_in_batches(
        jd_function_label,
        must_have_requirements,
        refactored_items,
        batch_size=15,
    )

    # Three independent code-level gates, run before anything else touches
    # the score. Each accepts BOTH signals -- literal keyword hits AND the
    # verification pass above -- and treats a requirement as satisfied if
    # EITHER says yes.
    #   1. function_gate: catches zero-overlap function mismatches
    #      (e.g. procurement exec on a customer success JD).
    #   2. must_have_gate: flags exactly which SPECIFIC named requirements
    #      are missing (this is what filter_qualified_candidates uses).
    #   3. company_domain_gate: catches "right company, wrong function
    #      while there" -- time at a relevant company that wasn't actually
    #      in the JD's domain.
    final_result = enforce_function_gate(
        final_result, refactored_items, core_function_keywords, verification_by_id=verification_by_id
    )
    final_result = enforce_must_have_gate(
        final_result, refactored_items, must_have_requirements, verification_by_id=verification_by_id
    )
    final_result = enforce_company_domain_gate(
        final_result, refactored_items, companies_list, core_function_keywords, must_have_requirements,
        verification_by_id=verification_by_id,
    )

    final_result = reconcile_candidate_ids(final_result, refactored_items)
    final_result = enrich_with_profile_media(final_result, result.get("items", []))

    print(f"[STEP 5] Evaluated + gated result: {len(final_result)} candidate(s) before qualification filter.")

    # Hard, automatic qualification filter -- no score threshold, no
    # manual review. Only candidates matching EVERY named must-have (plus
    # a clean function/company-domain check) survive into final_result.
    final_result = filter_qualified_candidates(final_result)

    # Still print the report -- not as a gate for a human to act on, just
    # a visible log of why the qualified list looks the way it does.
    print_human_review_report(final_result)

    if not final_result:
        print("❌ No candidate satisfied every named must-have for this JD -- "
              "nothing to add to a pipeline. Consider whether the JD's "
              "must-have list is too strict, or widen the LinkedIn search.")
        print("################## PIPELINE END (no qualified candidates) ##################\n")
        return final_result, None

    project_id = create_unipile_recruiter_project(project_name)

    if not project_id:
        print("❌ Failed to create Unipile recruiter project -- skipping pipeline adds.")
        print("################## PIPELINE END (no project) ##################\n")
        return final_result, None

    # Run synchronously so the process doesn't exit before the adds finish.
    # (37 candidates x 20s apart == ~12 minutes; that's expected, not a hang.)
    add_candidates_to_pipeline_with_delay(
        final_result,
        project_id,
        min_score=min_score_for_auto_add,
        delay_seconds=20,
        require_clean_function_gate=True,
        require_clean_must_have_gate=True,
        require_clean_company_domain_gate=True,
    )

    print("################## PIPELINE END ##################\n")
    return final_result, None


jd = """Job Title: Senior Vice President / Chief Customer Officer
About the Role: We are seeking a dynamic and experienced Senior Vice President or Chief Customer Officer to lead our pre-sales and customer success teams on a global scale. This leadership role is critical in driving customer satisfaction and success in our Software as a Service (SaaS) offerings.

Key Responsibilities:
- Lead and manage global pre-sales and customer success teams to ensure alignment with company objectives.
- Develop and implement strategies to enhance customer engagement and satisfaction.
- Collaborate with cross-functional teams to optimize the customer experience throughout the lifecycle.
- Drive revenue growth through effective customer relationship management and upselling strategies.
- Oversee the delivery of professional services to ensure high-quality outcomes for our clients.

Required Skills/Qualifications:
- Proven track record of leadership in pre-sales and customer success in a SaaS environment.
- Strong understanding of global professional services and customer engagement strategies.
- Excellent communication and interpersonal skills, with the ability to build relationships at all levels.
- Strategic thinker with a results-oriented mindset.

Experience:
- Extensive experience in customer success, pre-sales, or related leadership roles, preferably within the software industry.
- Experience leading teams in a global capacity is highly desirable.

Location:
- Candidates must be based on the East Coast or Central time zones in the United States, with preferred locations including New York, Chicago, or Austin.

Preferred Company Background:
- Candidates with experience at leading SaaS companies or comparable organizations are preferred."""

main_function(jd,"Senior Vice President / Chief Customer Officer : 16-09-2026","celonis")
