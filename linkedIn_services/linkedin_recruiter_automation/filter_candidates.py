import re
import json
import requests
import os


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

print(OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# LLM: generate LinkedIn-style title variants from a job description
# ---------------------------------------------------------------------------
def generate_title_variants(
    job_description: str,
    model: str = "gpt-4o-mini"
) -> list[str]:
    """
    Ask OpenAI for realistic LinkedIn title variants for the role described.

    Returns a flat list of strings, e.g.:
        [
            "Chief Customer Officer",
            "CCO",
            "Chief Customer Experience Officer",
            "Global Chief Customer Officer",
            "Group Chief Customer Officer"
        ]
    """

    system_prompt = (
        "You are a recruiting assistant. Given a job description, output the realistic "
        "range of job titles a matching candidate might have on LinkedIn TODAY, in their "
        "*current* role. Include:\n"
        "- The canonical title itself\n"
        "- Common abbreviations (e.g. CCO, CTO)\n"
        "- Common scope/seniority prefixes actually seen on LinkedIn "
        "(e.g. 'Global', 'Group', 'EVP + ', 'Interim')\n"
        "- Close synonymous titles used interchangeably in industry for the same function "
        "(e.g. 'Chief Customer Experience Officer' for 'Chief Customer Officer')\n\n"
        "Do NOT include titles for a clearly lower seniority or a different function "
        "(e.g. do not include 'VP Customer Success' or 'Customer Success Manager' for a "
        "'Chief Customer Officer' search).\n\n"
        'Respond ONLY with JSON: {"titles": ["...", "...", ...]}. No other text.'
    )

    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": job_description,
            },
        ],
        "response_format": {
            "type": "json_object"
        },
        "temperature": 0,
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    content = data["choices"][0]["message"]["content"]

    result = json.loads(content)

    titles = result.get("titles", [])

    return [
        title.strip()
        for title in titles
        if isinstance(title, str) and title.strip()
    ]


# ---------------------------------------------------------------------------
# 2. Deterministic matcher: candidate JSON  vs  a list of acceptable titles
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)   # strip punctuation
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_candidate_titles(candidate: dict, require_current: bool) -> list[str]:
    titles = []
    for exp in candidate.get("work_experience", []) or []:
        if not isinstance(exp, dict):
            continue
        is_current = not exp.get("end")  # no "end" date = current role
        if is_current or not require_current:
            role = exp.get("role")
            if role:
                titles.append(role)

    headline = candidate.get("headline")
    if headline:
        titles.append(headline)  # LinkedIn headline usually reflects current role

    return titles


def matches_any_title(candidate: dict, acceptable_titles: list[str], require_current: bool = True) -> bool:
    """
    True if the candidate currently holds any title in acceptable_titles
    (word-boundary phrase match, so prefixes like 'EVP +', 'Global', etc.
    on the candidate's actual title don't break the match).
    """
    if not candidate or not acceptable_titles:
        return False

    candidate_texts = [normalize(t) for t in _get_candidate_titles(candidate, require_current)]
    if not candidate_texts:
        return False

    for target in acceptable_titles:
        target_norm = normalize(target)
        if not target_norm:
            continue
        pattern = re.compile(r"\b" + r"\s+".join(re.escape(w) for w in target_norm.split()) + r"\b")
        if any(pattern.search(text) for text in candidate_texts):
            return True

    return False


# ---------------------------------------------------------------------------
# 3. Orchestrator
# ---------------------------------------------------------------------------
def is_matching_candidate(candidate: dict, job_description: str, require_current: bool = True) -> bool:
    acceptable_titles = generate_title_variants(job_description)
    return matches_any_title(candidate, acceptable_titles, require_current=require_current)