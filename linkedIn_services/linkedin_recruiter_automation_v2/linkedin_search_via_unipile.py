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
# 1) Extraction prompt — three fields (keywords / role_keywords / skills_keywords)
#    each with a distinct, non-overlapping job, matching Unipile's own framing:
#    "keywords" searches broadly across the profile, "role" focuses on job
#    titles, and "skills" targets listed competencies. Priority (CAN_HAVE vs
#    MUST_HAVE) is decided in code below, not by the LLM — that's what lets
#    the fallback ladder progressively loosen a search without a fresh call.
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = f"""You are a technical recruiter assistant. Given a job description,
extract structured LinkedIn Recruiter search parameters. Respond with ONLY valid JSON
(no markdown fences, no commentary) matching exactly this schema:

{{
  "keywords": "broad free-text signal that searches the WHOLE profile — domain/industry
    context, tools mentioned only in passing, methodologies, certifications. 1-2 terms,
    OR'd if more than one, e.g. \\"fintech\\" OR \\"payments\\". NEVER put a job title here
    (that belongs in role_keywords) and NEVER put a listed competency here (that belongs
    in skills_keywords). Leave empty if there's nothing broad and distinct left to say
    after role_keywords and skills_keywords are filled — do not restate them here.",

  "role_keywords": "2-4 job TITLE variants only, OR'd, e.g. \\"Backend Engineer\\" OR
    \\"Software Engineer\\" OR \\"Platform Engineer\\". At most one AND in this string, and
    only if the JD explicitly requires two title concepts together. NEVER include a company
    name — 'Zoho Developer' is WRONG (it forces the literal phrase 'Zoho Developer' and
    matches almost nobody); the correct extraction is role_keywords: \\"Developer\\" OR
    \\"Software Engineer\\" together with preferred_companies: [\\"Zoho\\"]. Company names
    belong ONLY in preferred_companies, never in keywords, role_keywords, or skills_keywords.",

  "skills_keywords": "at most 2-3 CORE listed competencies, OR'd, not AND. Only include a
    skill here if a candidate missing it would clearly be unqualified. Do not list every
    technology mentioned in the JD — that stacks filters and eliminates otherwise-good
    candidates. Do not repeat anything already captured in role_keywords.",

  "locations": ["city or region name — only what's explicitly stated, max 3"],
  "preferred_companies": ["Company Name — ONLY if the JD explicitly says target/poach from these companies. Do not infer companies from industry context."],
  "seniority_levels": ["optional. LOWERCASE ONLY, chosen strictly from this fixed set: {', '.join(SENIORITY_ALLOWED)}. Leave empty unless the JD is explicit about seniority — this filter is a common cause of 0 results when guessed."],
  "employment_type": ["FULL_TIME"]
}}

CRITICAL RULES TO AVOID ZERO-RESULT SEARCHES:
- Prefer OR over AND everywhere. A boolean string like "A" AND "B" AND "C" requires all
  three literally in the profile text and very often matches nobody. "A" OR "B" OR "C" is
  almost always what's actually wanted (candidate could be described any of those ways).
- keywords / role_keywords / skills_keywords must NOT overlap. Each concept from the JD
  goes in exactly ONE of the three fields — pick the single best fit: job title ->
  role_keywords, listed competency -> skills_keywords, everything else broad/contextual ->
  keywords. Never restate the same term across two fields — that double-filters the same
  concept and is a common cause of 0 results.
- Keep skills_keywords short. 6 ANDed or even 6 separately-required skills filters is a
  near-guaranteed 0-candidate search on real LinkedIn data.
- Only include locations/companies/seniority that are explicitly named or unambiguous in
  the JD. When in doubt, leave the field empty — an empty filter is skipped entirely and
  widens the search, which is safer than a wrong or overly narrow guess.
- Map JD seniority to the closest single value in the fixed set (e.g. "C-suite" -> "cxo",
  "VP"/"SVP" -> "vp", "entry-level" -> "entry") — never invent a value outside the set."""


def sanitize_extracted_keywords(extracted: dict) -> dict:
    """
    Code-level safety net against the 'Zoho Developer' failure mode: the LLM
    sometimes glues a company name onto a generic role noun into ONE phrase,
    despite being told not to, which forces LinkedIn to match that literal
    compound phrase and returns almost nobody. Prompt instructions alone
    aren't reliable enough here (confirmed: GPT-4o-mini repeated this exact
    mistake even with an explicit rule + worked example in the system
    prompt), so this checks the extraction after the fact and fixes it.

    Runs on keywords, role_keywords, and skills_keywords. Only triggers on a
    plain 2-3 word phrase with NO boolean operators (i.e. it doesn't look
    like an intentional OR list already) — anything already boolean-formed
    is left untouched.
    """
    GENERIC_ROLE_WORDS = {
        "developer", "engineer", "manager", "architect", "analyst",
        "consultant", "specialist", "lead", "director", "designer",
        "administrator", "scientist", "intern", "associate", "recruiter",
        "programmer", "technician", "coordinator",
    }

    def _split(phrase: str) -> str | None:
        if not phrase or " OR " in phrase.upper() or " AND " in phrase.upper() or '"' in phrase:
            return None  # already a deliberate boolean string, don't touch it
        words = phrase.strip().split()
        if len(words) < 2:
            return None
        role_word = words[-1]
        company_candidate = " ".join(words[:-1])
        if role_word.lower() not in GENERIC_ROLE_WORDS:
            return None
        resolved = resolve_id(company_candidate, "COMPANY")
        if not resolved:
            return None
        _, comp_title = resolved
        if comp_title.strip().lower() != company_candidate.strip().lower():
            return None  # not a confident match, leave the phrase alone
        print(f"  NOTE: auto-split '{phrase}' -> company '{comp_title}' + role '{role_word}' "
              f"(LLM glued these together despite the prompt rule)")
        companies = extracted.setdefault("preferred_companies", [])
        if comp_title not in companies:
            companies.append(comp_title)
        return role_word

    for field in ("keywords", "role_keywords", "skills_keywords"):
        cleaned = _split(extracted.get(field, ""))
        if cleaned is not None:
            extracted[field] = cleaned

    return extracted


def dedupe_across_fields(extracted: dict) -> dict:
    """
    Belt-and-suspenders against field overlap: if the LLM puts the same bare
    term in more than one of keywords / role_keywords / skills_keywords
    despite the prompt rule, that term gets ANDed against itself across two
    independent filters — the same over-constraint failure mode as the
    current_company/past_company bug, just at the keyword level. Keep the
    term only in the more specific field (role > skills > keywords) and
    strip it from the broader one rather than sending both.
    """
    def _terms(s: str) -> set[str]:
        return {
            w.strip('"').lower()
            for w in s.replace(" OR ", " ").replace(" AND ", " ").split()
        }

    role_terms = _terms(extracted.get("role_keywords", ""))
    skills_terms = _terms(extracted.get("skills_keywords", ""))

    kw = extracted.get("keywords", "")
    if kw and _terms(kw) & (role_terms | skills_terms):
        print(f"  NOTE: dropping 'keywords' ({kw!r}) — overlaps role/skills, would double-filter")
        extracted["keywords"] = ""

    if extracted.get("role_keywords") and extracted.get("skills_keywords"):
        if role_terms & skills_terms:
            print(f"  NOTE: dropping 'skills_keywords' ({extracted['skills_keywords']!r}) "
                  f"— overlaps role_keywords, would double-filter")
            extracted["skills_keywords"] = ""

    return extracted


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
#    location and company default to CAN_HAVE (never MUST_HAVE — profiles are
#    often incomplete/stale on these fields, so hard-requiring them silently
#    kills otherwise-good candidates). Only role gets MUST_HAVE, and only on
#    the first attempt; the fallback ladder below downgrades it if needed.
#
#    Company uses a single "company" filter with scope=CURRENT_OR_PAST rather
#    than setting BOTH current_company and past_company to the same list.
#    Those are two independent top-level filters that AND together, so
#    populating both with the same company demanded "works there now AND
#    worked there before" simultaneously — impossible for one company, and a
#    silent, invisible source of over-narrowed/0-result searches.
# ---------------------------------------------------------------------------
def build_payload(extracted: dict, *, role_priority: str = "MUST_HAVE",
                   include_skills: bool = True, include_company: bool = True,
                   include_keywords: bool = True, include_location: bool = True,
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
                # Single "company" filter, either era. NOT current_company +
                # past_company with the same list — see note above.
                company_objs.append({
                    "id": comp_id,
                    "priority": "CAN_HAVE",
                    "scope": "CURRENT_OR_PAST",
                })
                print(f"  company '{company_name}' -> {comp_title} ({comp_id})")
            else:
                print(f"  WARNING: no company match for '{company_name}', skipping")

    role_block = [
        {
            "keywords": extracted.get("role_keywords", ""),
            "priority": role_priority,
            "scope": "CURRENT_OR_PAST",
        }
    ] if extracted.get("role_keywords") else []

    skills_block = [
        {"keywords": extracted.get("skills_keywords", ""), "priority": "CAN_HAVE"}
    ] if (include_skills and extracted.get("skills_keywords")) else []

    payload = {
        "api": "recruiter",
        "category": "people",
        # keywords now carries its own distinct content (broad profile-text
        # signal, not a restatement of role/skills) — see extraction prompt
        # and dedupe_across_fields. It is no longer a spillover of role.
        "keywords": extracted.get("keywords", "") if include_keywords else "",
        "role": role_block,
        "skills": skills_block,
        "location": location_objs,
        "company": company_objs,
        "employment_type": extracted.get("employment_type", ["FULL_TIME"]),
    }

    # if include_seniority:
    #     seniority_levels = sanitize_seniority(extracted.get("seniority_levels", []))
    #     if seniority_levels:
    #         payload["seniority"] = {"include": seniority_levels}

    # Drop empty lists/strings so we don't send noisy filters
    return {k: v for k, v in payload.items() if v not in ("", [], None)}


def run_search(payload: dict, limit: int = 100) -> dict:
    """POST the assembled payload to the Unipile LinkedIn Recruiter search endpoint.

    limit defaults to 100 — the documented max for Recruiter/Sales Navigator
    (Classic search caps lower, around 50). The default of 10 was silently
    truncating result pages; this doesn't change how many candidates LinkedIn
    actually has (that's paging.total_count), but stops under-fetching when
    there ARE more than 10 to return.
    """
    headers = {
        "X-API-KEY": UNIPILE_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }
    params = {"account_id": UNIPILE_ACCOUNT_ID, "limit": limit}
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
        ("drop company filter", dict(role_priority="CAN_HAVE", include_skills=False,
                                      include_company=False)),
        ("drop keywords filter", dict(role_priority="CAN_HAVE", include_skills=False,
                                       include_company=False, include_keywords=False)),
        ("role keywords only, no location", dict(role_priority="CAN_HAVE", include_skills=False,
                                                   include_company=False, include_keywords=False,
                                                   include_location=False)),
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
    """Full pipeline: JD text -> OpenAI extraction -> sanitize -> ID resolution -> search (with fallback)."""
    print("Extracting search parameters from JD via OpenAI...")
    extracted = extract_search_params(jd_text)
    print(json.dumps(extracted, indent=2))

    extracted = sanitize_extracted_keywords(extracted)
    extracted = dedupe_across_fields(extracted)
    print("\nAfter sanitization:")
    print(json.dumps(extracted, indent=2))

    print("\nRunning LinkedIn Recruiter search with automatic loosening on 0 results...")
    result = search_with_fallback(extracted)
    print(f"\nFinal: used rung '{result.get('_fallback_step')}'")
    return result