#!/usr/bin/env python3
"""
StepChange Daily Brief Generator
─────────────────────────────────
Fetches Google News RSS feeds for curated topics, then passes the headlines
to Claude for categorization. No AI web search — fast and reliable in CI.

Usage:
  python generate_brief.py           # generate and write data.json
  python generate_brief.py --dry-run # print JSON without writing

Schedule (cron example — runs 7am daily):
  0 7 * * * cd /path/to/cohort-2-day-2 && python generate_brief.py >> brief.log 2>&1
"""

import json, re, sys, argparse, os, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
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
# COMPOSIO SEARCH
# ─────────────────────────────────────────────────────────────────

NEWS_QUERIES = [
    {"query": "RBI SEBI climate risk ESG disclosure India banks",      "when": "m", "gl": "in"},
    {"query": "ISSB IFRS FSB NGFS BIS climate disclosure banks",       "when": "m", "gl": "us"},
    {"query": "Jupiter Intelligence First Street UpDapt Sprih climate ESG", "when": "m"},
    {"query": "parametric insurance climate risk banks",               "when": "m"},
    {"query": "IFC World Bank NGFS climate risk report emerging markets", "when": "m"},
]

def fetch_news(days: int = 30) -> list[dict]:
    """Fetch news articles via Composio COMPOSIO_SEARCH_NEWS."""
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        print("ERROR: COMPOSIO_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    try:
        from composio import ComposioToolSet
    except ImportError:
        print("ERROR: 'composio-core' not installed. Run: pip install composio-core", file=sys.stderr)
        sys.exit(1)

    toolset = ComposioToolSet(api_key=api_key)
    items = []

    for q in NEWS_QUERIES:
        try:
            result = toolset.execute_action(
                action="COMPOSIO_SEARCH_NEWS",
                params=q,
            )
            articles = []
            # Handle different response shapes
            if isinstance(result, dict):
                articles = result.get("data", result.get("results", result.get("articles", [])))
            elif isinstance(result, list):
                articles = result

            for art in articles:
                if not isinstance(art, dict):
                    continue
                items.append({
                    "title":   (art.get("title") or "").strip(),
                    "url":     (art.get("link") or art.get("url") or "").strip(),
                    "source":  (art.get("source") or "Unknown"),
                    "date":    (art.get("published_at") or art.get("date") or "")[:10],
                    "snippet": (art.get("snippet") or art.get("description") or "")[:300].strip(),
                })
        except Exception as e:
            print(f"  Warning: Composio query failed ({q['query'][:40]}...): {e}", file=sys.stderr)

    # Deduplicate by URL
    seen, unique = set(), []
    for item in items:
        if item["url"] and item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique

# ─────────────────────────────────────────────────────────────────
# CATEGORIZATION PROMPT
# ─────────────────────────────────────────────────────────────────

CATEGORIZE_PROMPT = """You are a research analyst for StepChange — a climate risk and ESG intelligence company
serving Indian financial institutions (banks, NBFCs, enterprises). StepChange's three
strategic bets are:
  1. Climate risk intelligence for banks (PD/LGD modelling from physical hazard data)
  2. Parametric insurance (helping banks design climate peril-based products)
  3. Global south sustainability data (proprietary ESG datasets for emerging markets)

Below are recent news items fetched from Google News RSS feeds. Categorize the most relevant
ones into the brief JSON schema below.

ITEMS:
{items_text}

INSTRUCTIONS:
- Pick up to 6 items per category. Fewer is fine if fewer are relevant.
- Only include items genuinely relevant to StepChange's context.
- Use the exact titles and URLs from the items above — do not invent or modify them.
- For description, write 1-2 factual sentences expanding on the headline.
- Sort newest first within each category.

Respond with ONLY valid JSON. No preamble, no explanation, no markdown fences.

{{
  "meta": {{
    "generatedAt": "<ISO 8601 datetime>",
    "lastUpdated": "<H:MM AM/PM>"
  }},
  "policy": [
    {{
      "id": "policy-1",
      "area": "<human-readable area>",
      "areaSlug": "<esg|climate|carbon|parametric>",
      "title": "<title from items>",
      "description": "<1-2 factual sentences>",
      "source": "<source from items>",
      "date": "<date from items>",
      "url": "<url from items>"
    }}
  ],
  "competitors": [
    {{
      "id": "comp-1",
      "company": "<company name>",
      "category": "<India ESG|Global Climate Risk|Global ESG|FI Data>",
      "tags": ["<Fundraise|Partnerships|Product>"],
      "title": "<title from items>",
      "description": "<1-2 factual sentences>",
      "date": "<date from items>",
      "url": "<url from items>"
    }}
  ],
  "research": [
    {{
      "id": "research-1",
      "bet": "<Climate Risk for Banks|Parametric Insurance|Global South Data>",
      "betSlug": "<climate|parametric|global-south>",
      "title": "<title from items>",
      "source": "<source from items>",
      "date": "<date from items>",
      "description": "<1-2 factual sentences>",
      "url": "<url from items>"
    }}
  ],
  "ourRead": {{
    "headline": "<A single sharp insight sentence interpreting today's combined signals>",
    "body": [
      "<Opening paragraph: dominant theme across all signals today.>",
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

def run_claude(items: list[dict]) -> str:
    """Pass fetched items to Claude for categorization. Single API call, no tools."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: 'anthropic' not installed. Run: pip install anthropic", file=sys.stderr)
        sys.exit(1)

    # Format items as plain text for the prompt
    items_text = "\n".join(
        f"[{i+1}] {item['date']} | {item['source']}\n  Title: {item['title']}\n  URL: {item['url']}\n  Snippet: {item['snippet']}"
        for i, item in enumerate(items)
    )

    prompt = CATEGORIZE_PROMPT.format(items_text=items_text)

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
    parser.add_argument("--days", type=int, default=30, help="How many days back to fetch news (default: 30)")
    args = parser.parse_args()

    now = datetime.now()
    timestamp = now.strftime("%-I:%M %p")
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Generating brief...")

    print(f"  Fetching news via NewsAPI (last {args.days} days)...")
    items = fetch_news(days=args.days)
    print(f"  Fetched {len(items)} articles across {len(NEWS_QUERIES)} queries.")

    if not items:
        print("ERROR: No items fetched. Check network access and feed URLs.", file=sys.stderr)
        sys.exit(1)

    print("  Calling Claude to categorize...")
    raw = run_claude(items)

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
        data["meta"]["lastUpdated"] = timestamp

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
