"""
jd_generator.py

ONE OpenAI call does everything:
  - Reads the user's layman hiring requirement.
  - If required info is missing -> returns missing_fields + message.
  - If complete -> writes the full JD, and for every company the user
    mentioned, adds up to 5 similar/peer companies directly into the
    "Preferred Company Background" section.

Required information:
    - title_keywords
    - role_keywords
    - skills_keywords
    - locations
    - preferred_companies   (or an explicit "no preference")

Usage:
    from jd_generator import generate_job_description

    result = generate_job_description(user_input)
    if "jd_text" in result:
        print(result["jd_text"])
    else:
        print(result["message"])

Required environment variable:
    OPENAI_API_KEY
"""

import os
import json
import requests

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"

REQUIRED_JD_FIELDS = [
    "title_keywords",
    "role_keywords",
    "skills_keywords",
    "locations",
    "preferred_companies",
]

SYSTEM_PROMPT = """
You are an expert recruiter and professional job-description writer.

The user will give you a hiring requirement in informal, layman language.
Understand what they mean without requiring structured field names, and use
reasonable real-world recruiter judgment - don't be pedantic.

A requirement is COMPLETE only when you can reasonably determine ALL of:
  1. title_keywords      - the job title / type of position
  2. role_keywords        - what the person will actually do / function
  3. skills_keywords       - specific skills, tools, or competencies required
  4. locations             - at least one city, region, or remote status
  5. preferred_companies   - a company they want candidates to have worked
                              at, OR an explicit "no preference" (which still
                              counts as present)

IMPORTANT - INFER, DON'T INTERROGATE:
- If the title itself is clear and unambiguous about the domain (e.g. "Java
  Developer", "Node.js Developer", "React Frontend Engineer", "SEO
  Specialist", "Customer Support Officer"), you may DERIVE role_keywords and
  skills_keywords from that title yourself, using standard industry
  knowledge of what that role normally involves and what its core skill is.
  Example: "Java Developer" alone is enough to infer role_keywords =
  "Java / backend development" and skills_keywords = "Java, object-oriented
  programming, and related backend technologies" - do NOT ask the user to
  restate the obvious.
- Only treat role_keywords or skills_keywords as MISSING when the title
  itself is genuinely vague or ambiguous with no derivable domain - e.g.
  bare "Developer", "Engineer", "Manager", "Officer" with no technology,
  function, or domain named at all.
- Do not invent HIGHLY specific requirements not implied by the title (exact
  framework versions, certifications, degree, salary, shift) - general,
  standard skills for that role are fine to infer; hyper-specific ones are not.
- locations and preferred_companies still need to come from what the user
  actually said - do not infer these.

IF INCOMPLETE, return ONLY this JSON:
{
  "status": "incomplete",
  "missing_fields": ["field_name", "..."],
  "message": "a short, friendly explanation of exactly what to add"
}
missing_fields values must only be chosen from: title_keywords, role_keywords,
skills_keywords, locations, preferred_companies.

IF COMPLETE, write one full professional job description and return ONLY this JSON:
{
  "status": "complete",
  "jd_text": "the complete job description as plain text"
}

When writing jd_text:
- Convert the informal input into professional recruitment language while
  preserving the user's actual intent. Do not invent specifics (salary,
  certifications, degree, shift, tech stack) that weren't stated or clearly
  implied by the role.
- Structure: Job Title, About the Role, Key Responsibilities, Required
  Skills/Qualifications, Experience, Location, Preferred Company Background
  (only if companies were mentioned), Additional Requirements (if any).
- COMPANY EXPANSION (do this inline, do not skip it): for EVERY company the
  user mentioned, identify up to 5 genuinely similar/peer companies - same
  or closely related industry, similar business model, size, and prestige
  (e.g. EY -> Deloitte, PwC, KPMG, Accenture, BDO; TCS -> Infosys, Wipro,
  Cognizant, Accenture, HCLTech). If the user named several companies from
  different industries, expand EACH one separately - do not skip any.
  Weave the ORIGINAL companies plus ALL their similar companies naturally
  into the "Preferred Company Background" section, by name, with no
  omissions. Do not mention that these were "generated", "expanded", or
  "suggested" - phrase it as a normal recruiter preference, e.g.
  "Candidates with experience at TCS, or comparable organizations such as
  Infosys, Wipro, Cognizant, Accenture, or HCLTech, are preferred."
  If a company is small, niche, or unknown enough that you cannot confidently
  name real peers, just keep the original company and skip peers for that
  one rather than guessing.
- If the user said there's no company preference, omit that section entirely.

Return ONLY valid JSON. No markdown fences, no commentary, no extra fields.
"""


def generate_job_description(user_input: str) -> dict:
    """
    Single OpenAI call: understand the requirement, check completeness,
    and if complete, write the full JD with similar companies included.

    Returns:
        {"jd_text": "..."}                          if complete
        {"missing_fields": [...], "message": "..."}  if incomplete
    """
    if not user_input or not user_input.strip():
        return {
            "missing_fields": REQUIRED_JD_FIELDS,
            "message": (
                "Please provide the hiring requirement, including the "
                "job role, responsibilities, required skills, location, "
                "and preferred company background."
            ),
        }

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input.strip()},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(OPENAI_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)

    if result.get("status") == "complete" and result.get("jd_text"):
        return {"jd_text": result["jd_text"]}

    missing = [f for f in result.get("missing_fields", []) if f in REQUIRED_JD_FIELDS]
    message = result.get(
        "message",
        f"Please provide more information about: {', '.join(missing) if missing else 'title, role, skills, location, and preferred companies'}.",
    )
    return {"missing_fields": missing, "message": message}

