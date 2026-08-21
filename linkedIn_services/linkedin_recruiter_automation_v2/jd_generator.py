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
  5. preferred_companies   - explicit company names, an industry domain/type,
                              OR an explicit "no preference" (which still
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
  Skills/Qualifications, Experience, Location, Preferred Company Background,
  Additional Requirements (if any).

- MANDATORY LOCATION EXPANSION RULE:
  You MUST ALWAYS output concrete, named cities.
  1. IF CITIES ARE MENTIONED: Use them directly.
  2. IF A STATE, REGION, OR ZONE IS MENTIONED (e.g., "Jharkhand", "Texas", "East Coast", "Bay Area"):
     You MUST identify and explicitly name 3 to 5 major cities or commercial hubs in that area. 
     Example for "Jharkhand": Write "Jharkhand (including major hubs such as Ranchi, Bokaro, Jamshedpur, or Dhanbad)".
     Never output just a state/region name alone without naming key cities.

- MANDATORY COMPANY LISTING RULE (STRICT):
  NEVER write generic phrases like "or comparable organizations" or "leading companies in the industry" without listing explicit company names. You MUST roll out actual company names in EVERY completion, unless the user explicitly requested "no preference".

  1. IF SPECIFIC COMPANIES WERE NAMED:
     Name the original companies PLUS up to 5 peer companies in the same tier/industry.
  2. IF NO SPECIFIC COMPANY WAS NAMED (or only a domain/industry was given):
     Identify the domain (SaaS, FinTech, E-commerce, Consulting, Banking, etc.) and explicitly name 4 to 5 top-tier industry benchmark companies for that role.

  Formatting Pattern (Always name explicit companies):
  "Preferred Company Background: Candidates with experience at [Company A], [Company B], [Company C], [Company D], or [Company E] are strongly preferred."

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