#!/usr/bin/env python3
"""
StepChange Daily Brief Generator
─────────────────────────────────
Calls Claude with WebSearch to find real updates across policy, competitors,
and research. Writes structured JSON to my-site/data.json, which the brief
site loads on every visit.

Usage:
  python generate_brief.py           # generate and write data.json
  python generate_brief.py --dry-run # print JSON without writing

Schedule (cron example — runs 7am daily):
  0 7 * * * cd /path/to/cohort-2-day-2 && python generate_brief.py >> brief.log 2>&1
"""

import subprocess, json, re, sys, argparse, os
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
# PROMPT
# ─────────────────────────────────────────────────────────────────

SEARCH_PROMPT = """
You are a research analyst for StepChange — a climate risk and ESG intelligence company
serving Indian financial institutions (banks, NBFCs, enterprises). StepChange's three
strategic bets are:
  1. Climate risk intelligence for banks (PD/LGD modelling from physical hazard data)
  2. Parametric insurance (helping banks design climate peril-based products)
  3. Global south sustainability data (proprietary ESG datasets for emerging markets)

Your job: search the web for the freshest news and research relevant to StepChange, then
output structured JSON for the daily brief. Use WebSearch and WebFetch.

─── STEP 1: POLICY RADAR ────────────────────────────────────────────
Search for regulatory and policy updates from the last 7 days relevant to:
- Indian regulators: RBI climate risk guidelines, SEBI BRSR updates, IRDAI parametric insurance
- Global standards: ISSB/IFRS S2, TCFD, EU SFDR, EU Taxonomy, CSRD, Basel climate risk
- Green finance: BIS, FSB, NGFS, IOSCO sustainable finance

Suggested searches:
  "RBI climate risk guidelines 2026"
  "SEBI BRSR disclosure 2026"
  "ISSB IFRS S2 climate disclosure update"
  "FSB NGFS climate risk banks 2026"

Pick the 6 most recent, most relevant results. Real URLs only.

─── STEP 2: COMPETITOR WATCH ────────────────────────────────────────
Search for news about fundraises, partnerships, or product launches in the last 30 days for:

India ESG & climate: UpDapt, Sprih, GIST Advisory, Enture, Greenizon, CarbonMint
Global climate risk: Jupiter Intelligence, First Street Foundation, Cervest, Sust Global, XDI
Global ESG platforms: Measurabl, Watershed, Persefoni, Sweep, Manifest Climate
FI data & ratings: MSCI ESG, Sustainalytics, Moody's ESG, S&P Trucost, Bloomberg ESG

Suggested searches:
  "Jupiter Intelligence First Street climate risk 2026"
  "UpDapt Sprih ESG India funding 2026"
  "MSCI Moody's ESG ratings update 2026"
  "climate risk fintech funding 2026"

Pick the 6 most recent results. Tag each: Fundraise | Partnerships | Product.

─── STEP 3: RESEARCH & REPORTS ──────────────────────────────────────
Search for new reports or publications in the last 60 days from:
IFC, World Bank, Swiss Re, Munich Re, IPCC, UNEPFI, NGFS, BIS, RBI, CPI, FSB, CRISIL

Suggested searches:
  "NGFS climate scenario update 2026"
  "IFC World Bank climate risk emerging markets 2026"
  "parametric insurance climate report 2026"
  "Swiss Re Munich Re climate risk report 2026"

Pick up to 6 relevant to StepChange's three bets.

─── OUTPUT INSTRUCTIONS ────────────────────────────────────────────
Respond with ONLY valid JSON. No preamble, no explanation, no markdown fences.
Use this exact schema:

{
  "meta": {
    "generatedAt": "<ISO 8601 datetime>",
    "lastUpdated": "<H:MM AM/PM>"
  },
  "policy": [
    {
      "id": "policy-1",
      "area": "<human-readable area>",
      "areaSlug": "<esg|climate|carbon|parametric>",
      "title": "<exact title from source>",
      "description": "<1-2 factual sentences. Include specific details: dates, thresholds, requirements.>",
      "source": "<institution name>",
      "date": "<DD Mon YYYY>",
      "url": "<real, working URL>"
    }
  ],
  "competitors": [
    {
      "id": "comp-1",
      "company": "<company name>",
      "category": "<India ESG|Global Climate Risk|Global ESG|FI Data>",
      "tags": ["<Fundraise|Partnerships|Product>"],
      "title": "<headline of the development>",
      "description": "<1-2 factual sentences with specifics.>",
      "date": "<DD Mon YYYY>",
      "url": "<real, working URL>"
    }
  ],
  "research": [
    {
      "id": "research-1",
      "bet": "<Climate Risk for Banks|Parametric Insurance|Global South Data>",
      "betSlug": "<climate|parametric|global-south>",
      "title": "<exact report title>",
      "source": "<publishing institution>",
      "date": "<Mon YYYY>",
      "description": "<1-2 factual sentences. Include specific findings, numbers, or implications.>",
      "url": "<real, working URL to the report or abstract>"
    }
  ],
  "ourRead": {
    "headline": "<A single sharp insight sentence that interprets today's combined signals>",
    "body": [
      "<Opening paragraph: name the dominant theme across all signals today.>",
      "<Paragraph on the most important policy signal and what it means for StepChange specifically.>",
      "<Paragraph on the most important competitor signal and how it maps to StepChange's positioning.>",
      "<Paragraph on the most important research signal and how StepChange should use it.>",
      "<Closing flag: one thing to watch, one action the team should take.>"
    ]
  }
}

QUALITY RULES:
- Return up to 6 items per category, sorted newest first. Fewer is fine if fewer exist.
- Every URL must be a real URL you actually retrieved — do not construct or guess URLs.
- Dates must be real publication dates, not today's date.
- Do not pad with fabricated content. If a category has only 2 real results, return 2.
- The ourRead synthesis must reference specific items from the other three categories.
- Be direct and opinionated in ourRead. This is analysis, not summary.
"""

# ─────────────────────────────────────────────────────────────────
# CLAUDE RUNNER
# ─────────────────────────────────────────────────────────────────

def run_claude(prompt: str, timeout: int = 240) -> str:
    """Call Anthropic API directly with web search. No CLI required."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: 'anthropic' package not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    try:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        messages = [{"role": "user", "content": prompt}]

        for _ in range(20):  # max turns safety limit
            response = client.beta.messages.create(
                model="claude-opus-4-5",
                max_tokens=8000,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 15}],
                messages=messages,
                betas=["web-search-2025-03-05"],
            )

            # Collect any text in this response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            if response.stop_reason == "end_turn":
                return text

            # Tool use — add assistant turn and continue
            messages.append({"role": "assistant", "content": response.content})

            tool_results = [
                {"type": "tool_result", "tool_use_id": block.id, "content": ""}
                for block in response.content
                if block.type == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                return text  # no tool calls and not end_turn — return what we have

        return ""

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
            warnings.append(f"'{section}' has 0 items — may indicate search failure")
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
    parser.add_argument("--timeout", type=int, default=240, help="Claude timeout in seconds (default: 240)")
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%-I:%M %p")
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Generating brief...")

    # Inject current time into prompt so Claude uses it in meta
    prompt = SEARCH_PROMPT.replace(
        '"lastUpdated": "<H:MM AM/PM>"',
        f'"lastUpdated": "{timestamp}"'
    )

    print("  Calling Claude with WebSearch — this takes 2–4 minutes...")
    raw = run_claude(prompt, timeout=args.timeout)

    print("  Extracting JSON from response...")
    data = extract_json(raw)

    if data is None:
        print("ERROR: Could not extract valid JSON from Claude's response.")
        print("─── Raw response (first 1000 chars) ───")
        print(raw[:1000])
        print("─── End ───")
        print("\nTip: Run again. If it keeps failing, check that WebSearch is working.")
        sys.exit(1)

    # Inject generatedAt if missing
    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["generatedAt"] = now.isoformat()
    if "lastUpdated" not in data["meta"]:
        data["meta"]["lastUpdated"] = timestamp

    # Validate
    warnings = validate(data)
    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    ⚠  {w}")

    # Report counts
    counts = {k: len(data.get(k, [])) for k in ("policy", "competitors", "research")}
    print(f"  Found: {counts['policy']} policy · {counts['competitors']} competitors · {counts['research']} research")

    if args.dry_run:
        print("\n─── data.json (dry run, not written) ───")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    # Write
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  Written to: {DATA_FILE}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Done.")


if __name__ == "__main__":
    main()
