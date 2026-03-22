#!/usr/bin/env python3
"""
StepChange Daily Brief Generator
─────────────────────────────────
Calls Claude (Anthropic SDK, no tools) to generate a fresh intelligence brief
from its training knowledge. No external API calls — runs reliably in CI.

Usage:
  python generate_brief.py           # generate and write data.json
  python generate_brief.py --dry-run # print JSON without writing

Schedule (cron example — runs 7am daily):
  0 7 * * * cd /path/to/my-site && python generate_brief.py >> brief.log 2>&1
"""

import json, re, sys, argparse, os
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
# Works from both cohort-2-day-2/ (has my-site/ subdir) and my-site/ directly
_site_dir  = SCRIPT_DIR / "my-site"
DATA_FILE  = (_site_dir if _site_dir.exists() else SCRIPT_DIR) / "data.json"
LOG_FILE   = SCRIPT_DIR / "brief.log"

# ─────────────────────────────────────────────────────────────────
# BRIEF GENERATION PROMPT
# ─────────────────────────────────────────────────────────────────

BRIEF_PROMPT = """You are a research analyst for StepChange — a climate risk and ESG intelligence company
serving Indian financial institutions (banks, NBFCs, enterprises). StepChange's three
strategic bets are:
  1. Climate risk intelligence for banks (PD/LGD modelling from physical hazard data)
  2. Parametric insurance (helping banks design climate peril-based products)
  3. Global south sustainability data (proprietary ESG datasets for emerging markets)

Today's date: {today}

Generate a daily intelligence brief covering recent developments (last 30–60 days) in:

POLICY RADAR — regulatory and policy updates relevant to:
- Indian regulators: RBI climate risk guidelines, SEBI BRSR updates, IRDAI parametric insurance
- Global standards: ISSB/IFRS S2, TCFD, EU SFDR, EU Taxonomy, CSRD, Basel climate risk
- Green finance bodies: BIS, FSB, NGFS, IOSCO

COMPETITOR WATCH — recent fundraises, partnerships, or product launches for:
- India ESG & climate: UpDapt, Sprih, GIST Advisory, Enture, Greenizon, CarbonMint
- Global climate risk: Jupiter Intelligence, First Street Foundation, Cervest, Sust Global, XDI
- Global ESG platforms: Measurabl, Watershed, Persefoni, Sweep, Manifest Climate
- FI data & ratings: MSCI ESG, Sustainalytics, Moody's ESG, S&P Trucost, Bloomberg ESG

RESEARCH & REPORTS — new publications from:
IFC, World Bank, Swiss Re, Munich Re, IPCC, UNEPFI, NGFS, BIS, RBI, CPI, FSB, CRISIL

OUR READ — a sharp, opinionated synthesis of what today's combined signals mean for StepChange.

INSTRUCTIONS:
- Return up to 6 items per category, sorted newest first. Fewer is fine.
- Use real publication titles, real institution names, and real approximate dates.
- For URLs: use the real URL if you know it with high confidence, otherwise use the institution's
  homepage (e.g. https://www.rbi.org.in for RBI items, https://www.fsb.org for FSB items).
  Never construct or guess article-level URLs — homepage is safer than a fabricated path.
- Write 1-2 factual sentences per item description. Be specific: thresholds, timelines, findings.
- ourRead must reference specific items from the other three categories. Be direct and opinionated.

Respond with ONLY valid JSON. No preamble, no explanation, no markdown fences.

{{
  "meta": {{
    "generatedAt": "{today}T00:00:00",
    "lastUpdated": "{time_str}"
  }},
  "policy": [
    {{
      "id": "policy-1",
      "area": "<human-readable area>",
      "areaSlug": "<esg|climate|carbon|parametric>",
      "title": "<title>",
      "description": "<1-2 factual sentences>",
      "source": "<institution name>",
      "date": "<DD Mon YYYY>",
      "url": "<real URL or institution homepage>"
    }}
  ],
  "competitors": [
    {{
      "id": "comp-1",
      "company": "<company name>",
      "category": "<India ESG|Global Climate Risk|Global ESG|FI Data>",
      "tags": ["<Fundraise|Partnerships|Product>"],
      "title": "<headline of the development>",
      "description": "<1-2 factual sentences>",
      "date": "<DD Mon YYYY>",
      "url": "<real URL or company homepage>"
    }}
  ],
  "research": [
    {{
      "id": "research-1",
      "bet": "<Climate Risk for Banks|Parametric Insurance|Global South Data>",
      "betSlug": "<climate|parametric|global-south>",
      "title": "<report title>",
      "source": "<publishing institution>",
      "date": "<Mon YYYY>",
      "description": "<1-2 factual sentences with specific findings>",
      "url": "<real URL or institution homepage>"
    }}
  ],
  "ourRead": {{
    "headline": "<A single sharp insight sentence interpreting today's combined signals>",
    "body": [
      "<Opening paragraph: dominant theme across all signals.>",
      "<Paragraph on the most important policy signal and what it means for StepChange.>",
      "<Paragraph on the most important competitor signal and StepChange's positioning.>",
      "<Paragraph on the most important research signal and how StepChange should use it.>",
      "<Closing: one thing to watch, one action the team should take.>"
    ]
  }}
}}"""

# ─────────────────────────────────────────────────────────────────
# CLAUDE CALLER
# ─────────────────────────────────────────────────────────────────

def generate_brief(today: str, time_str: str) -> str:
    """Call Claude to generate the brief from knowledge. Single API call, no tools."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: 'anthropic' not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    prompt = BRIEF_PROMPT.format(today=today, time_str=time_str)

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        print(f"ERROR: Anthropic API call failed: {e}", file=sys.stderr)
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────
# JSON EXTRACTION
# ─────────────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    """Try progressively looser strategies to pull valid JSON from Claude's response."""

    # Strategy 1: entire response is JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: ```json ... ``` fenced block
    match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: first { ... } spanning the whole JSON object
    match = re.search(r'(\{[\s\S]+\})', text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None

# ─────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────

def validate(data: dict) -> list[str]:
    """Return a list of warning strings for any schema issues."""
    warnings = []
    required_keys = {"meta", "policy", "competitors", "research", "ourRead"}
    missing = required_keys - set(data.keys())
    if missing:
        warnings.append(f"Missing top-level keys: {missing}")

    for section in ("policy", "competitors", "research"):
        items = data.get(section, [])
        if not isinstance(items, list):
            warnings.append(f"'{section}' is not a list")
            continue
        if len(items) == 0:
            warnings.append(f"'{section}' has 0 items")
        for i, item in enumerate(items):
            if not item.get("url") or item["url"] in ("#", ""):
                warnings.append(f"{section}[{i}] has no URL: {item.get('title','(no title)')}")
            if not item.get("title"):
                warnings.append(f"{section}[{i}] has no title")

    our_read = data.get("ourRead", {})
    if not our_read.get("headline"):
        warnings.append("ourRead is missing a headline")
    if len(our_read.get("body", [])) < 3:
        warnings.append("ourRead body has fewer than 3 paragraphs")

    return warnings

# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate StepChange Daily Brief data.json")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON without writing to file")
    args = parser.parse_args()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%-I:%M %p")
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Generating brief...")

    print("  Calling Claude to generate brief from knowledge...")
    raw = generate_brief(today=today, time_str=time_str)

    print("  Extracting JSON from response...")
    data = extract_json(raw)

    if data is None:
        print("ERROR: Could not extract valid JSON from Claude's response.")
        print("─── Raw response (first 1000 chars) ───")
        print(raw[:1000])
        sys.exit(1)

    # Inject generatedAt
    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["generatedAt"] = now.isoformat()
    if "lastUpdated" not in data["meta"]:
        data["meta"]["lastUpdated"] = time_str

    # Validate
    warnings = validate(data)
    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    ⚠  {w}")

    counts = {k: len(data.get(k, [])) for k in ("policy", "competitors", "research")}
    print(f"  Found: {counts['policy']} policy · {counts['competitors']} competitors · {counts['research']} research")

    if args.dry_run:
        print("\n─── data.json (dry run, not written) ───")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Written to: {DATA_FILE}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Done.")


if __name__ == "__main__":
    main()
