#!/usr/bin/env python3
"""
engagement_tracker.py -- status snapshot of external threads where we
have commented on v0.10.0 (and later).

For each tracked thread this reports:
  * total comments on the issue
  * whether any activity landed AFTER our comment (needs your attention)
  * reactions on our comment (+1 / -1 / heart / rocket / eyes / laugh / hooray / confused)
  * whether the issue itself is still open
  * timestamp of latest activity on the thread

Reads THREADS below; add rows as you comment on new threads. Uses `gh` CLI
for auth (no separate token setup).

Usage:
  python scripts/engagement_tracker.py                    # human-readable table
  python scripts/engagement_tracker.py --json             # machine-readable
  python scripts/engagement_tracker.py --unread-only      # only threads with activity since our comment
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Format: (repo, issue_number, our_comment_id, short_label)
# Add rows here whenever we post on a new external thread.
THREADS: list[tuple[str, int, int, str]] = [
    # v0.10.0 wedge fits on OpenClaw / Hermes (2026-07-01)
    ("openclaw/openclaw",              7707,  4851995145, "Memory Trust Tagging by Source"),
    ("NousResearch/hermes-agent",      8457,  4851996808, "Persistent Session Memory"),
    ("openclaw/openclaw",             20935,  4852020056, "Audit log for memory changes"),
    ("NousResearch/hermes-agent",     47349,  4852021666, "Configurable Memory Backends"),
    ("openclaw/openclaw",             40418,  4852023222, "Automated Session Memory Preservation"),
    ("openclaw/openclaw",             45608,  4852041165, "Pre-reset agentic memory flush"),

    # Same-thread integrity: v0.10.0 updates on prior threads (2026-07-01)
    ("anthropics/claude-code",        47023,  4852050778, "Compact/session lifecycle hooks (Patdolitse)"),
    ("openai/codex",                  19195,  4852052058, "Memory writability (ferhimedamine)"),
    ("openai/codex",                  21753,  4852053665, "Full Claude Code hook parity"),
    ("anthropics/claude-code",        14227,  4852055776, "Persistent Memory Between Sessions"),
    ("anthropics/claude-code",        30039,  4852057251, "Native Context Graph"),

    # v0.11.0 ship notification on the Hermes thread where the plugin was
    # specifically motivated (@TechFlipsi pushback + our follow-up).
    ("NousResearch/hermes-agent",     47349,  4869758223, "MemoryProvider plugin shipped (v0.11.0)"),
]


def gh_api(path: str) -> dict:
    """Call GitHub API via `gh` and return parsed JSON."""
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fmt_reactions(reactions: dict) -> str:
    """Compact reaction summary. Only show non-zero counts."""
    keys = [
        ("+1", "+1"), ("-1", "-1"), ("heart", "❤"), ("rocket", "🚀"),
        ("eyes", "👀"), ("laugh", "😄"), ("hooray", "🎉"), ("confused", "😕"),
    ]
    parts = []
    for api_key, label in keys:
        n = reactions.get(api_key, 0)
        if n:
            parts.append(f"{label}{n}")
    return " ".join(parts) if parts else "-"


def fetch_thread_status(repo: str, issue_num: int, comment_id: int, label: str) -> dict:
    """Fetch current status for one thread."""
    issue = gh_api(f"repos/{repo}/issues/{issue_num}")
    our_comment = gh_api(f"repos/{repo}/issues/comments/{comment_id}")

    our_ts = our_comment["created_at"]
    issue_updated_ts = issue["updated_at"]

    # Fetch replies to find activity after our comment
    all_comments = gh_api(
        f"repos/{repo}/issues/{issue_num}/comments?per_page=100&since={our_ts}"
    )
    # Filter to comments strictly AFTER ours (the `since` param is inclusive)
    replies_after = [c for c in all_comments if c["id"] != comment_id]

    return {
        "repo": repo,
        "issue": issue_num,
        "label": label,
        "url": f"https://github.com/{repo}/issues/{issue_num}",
        "our_comment_url": our_comment["html_url"],
        "issue_state": issue["state"],
        "total_comments": issue["comments"],
        "our_comment_at": our_ts,
        "issue_updated_at": issue_updated_ts,
        "replies_after_ours": len(replies_after),
        "reactions": our_comment.get("reactions", {}),
        "latest_reply_from": (
            replies_after[-1]["user"]["login"] if replies_after else None
        ),
        "latest_reply_at": (
            replies_after[-1]["created_at"] if replies_after else None
        ),
    }


def print_table(rows: list[dict], unread_only: bool = False) -> None:
    """Print a human-readable status table."""
    if unread_only:
        rows = [r for r in rows if r["replies_after_ours"] > 0]

    if not rows:
        print("No threads with new activity since our comments.")
        return

    print(f"\nEngagement tracker snapshot @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print("=" * 100)
    print()

    for r in rows:
        reactions = fmt_reactions(r["reactions"])
        state = r["issue_state"].upper()
        replies = r["replies_after_ours"]
        marker = "*NEW*" if replies else "     "
        print(f"{marker}  {r['repo']}#{r['issue']}  [{state}]  {r['label']}")
        print(f"        our comment reactions: {reactions}   |   replies after ours: {replies}")
        if replies:
            print(f"        latest reply @ {r['latest_reply_at']} by @{r['latest_reply_from']}")
        print(f"        {r['url']}")
        print()

    total = len(rows)
    with_replies = sum(1 for r in rows if r["replies_after_ours"] > 0)
    with_reactions = sum(
        1 for r in rows
        if any(v for k, v in r["reactions"].items() if k != "url" and k != "total_count")
    )
    print(f"Summary: {total} threads tracked | {with_replies} with new activity | {with_reactions} with reactions")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--unread-only", action="store_true", help="Only show threads with activity after our comment")
    args = parser.parse_args()

    rows = []
    for repo, issue_num, comment_id, label in THREADS:
        try:
            rows.append(fetch_thread_status(repo, issue_num, comment_id, label))
        except Exception as e:
            print(f"  WARN: {repo}#{issue_num}: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows, unread_only=args.unread_only)


if __name__ == "__main__":
    main()
