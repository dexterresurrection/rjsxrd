#!/usr/bin/env python3
"""Remove stale GitHub URLs from URLS.txt by checking last commit date.

Only checks URLs pointing to raw.githubusercontent.com or github.com.
Uses GitHub API (authenticated recommended — 5000 req/hr vs 60 req/hr).
Sequential with 1s delay between requests to avoid secondary rate limits.

Usage:
    cd source
    python scripts/purge_stale_urls.py                    # dry run (default)
    python scripts/purge_stale_urls.py --apply             # actually remove
    python scripts/purge_stale_urls.py --days 60 --apply   # custom threshold
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Any
from urllib.parse import urlparse

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

URLS_FILE = os.path.join(PROJECT_ROOT, "config", "URLS.txt")
CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "stale_urls_cache.json")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("MY_TOKEN")

# Rate limiting: GitHub allows 5000 req/hr authenticated, 60 req/hr unauthenticated.
# We pace ourselves to stay well under limits.
_REQUEST_DELAY = 1.2  # seconds between requests
_REQUESTS_PER_HOUR = 5000 if GITHUB_TOKEN else 60
_MAX_CONCURRENT = 1  # no parallelism — avoids secondary rate limits


def parse_github_url(url: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse a GitHub URL into (owner, repo, branch, path) or None.

    Handles raw.githubusercontent.com and github.com URLs. For
    raw.githubusercontent.com correctly splits refs/heads/branch paths.
    """
    url = url.split("#")[0].split("?")[0]
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    try:
        if host == "raw.githubusercontent.com":
            segments = parsed.path.strip("/").split("/")
            if len(segments) < 4:
                return None
            owner, repo = segments[0], segments[1]

            if segments[2] == "refs" and len(segments) >= 5:
                branch = segments[4]
                file_path = "/".join(segments[5:])
            else:
                branch = segments[2]
                file_path = "/".join(segments[3:])

            return owner, repo, branch, file_path

        elif host == "github.com":
            parts = parsed.path.strip("/").split("/", 5)
            if len(parts) >= 5 and parts[2] in ("blob", "raw"):
                return parts[0], parts[1], parts[3], "/".join(parts[4:])
    except (ValueError, IndexError):
        pass
    return None


def make_cache_key(parsed: Tuple[str, str, str, str]) -> str:
    return f"{parsed[0]}/{parsed[1]}/{parsed[3]}"


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/vnd.github.v3+json"})
    if GITHUB_TOKEN:
        session.headers.update({"Authorization": f"Bearer {GITHUB_TOKEN}"})
    return session


def check_rate_limit(session: requests.Session) -> Tuple[int, int]:
    """Check remaining API quota. Returns (remaining, limit)."""
    try:
        resp = session.get("https://api.github.com/rate_limit", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            core = data["resources"]["core"]
            return core["remaining"], core["limit"]
    except (requests.RequestException, KeyError, ValueError):
        pass
    return 0, 0


def wait_if_needed(headers: Any) -> None:
    """Parse rate limit headers and sleep if we're close to the limit."""
    remaining = headers.get("X-RateLimit-Remaining")
    reset_at = headers.get("X-RateLimit-Reset")
    retry_after = headers.get("Retry-After")

    if remaining is not None:
        remaining = int(remaining)

    # Secondary rate limit — backoff with Retry-After
    if retry_after is not None:
        wait = int(retry_after) + 2
        print(f"  secondary rate limit hit, sleeping {wait}s...", flush=True)
        time.sleep(wait)
        return

    # Primary rate limit running low
    if remaining is not None and reset_at is not None and remaining < 10:
        reset_ts = int(reset_at)
        now = time.time()
        if reset_ts > now:
            wait = min(reset_ts - now + 2, 300)
            print(f"  rate limit nearly exhausted ({remaining} left), sleeping {wait:.0f}s until reset...", flush=True)
            time.sleep(wait)


def get_last_commit_date(session: requests.Session, owner: str, repo: str, path: str, branch: str) -> Optional[str]:
    """Return ISO date string of the last commit touching a file, or None.

    Handles primary and secondary GitHub rate limits with proper backoff.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"path": path, "per_page": 1, "sha": branch}

    for attempt in range(3):
        try:
            resp = session.get(api_url, params=params, timeout=15)

            if resp.status_code == 204:
                return None
            if resp.status_code in (404, 409, 422):
                return None
            if resp.status_code == 403:
                wait_if_needed(resp.headers)
                continue  # retry after backoff
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return data[0]["commit"]["committer"]["date"].replace("Z", "+00:00")
                return None

            return None

        except requests.RequestException:
            if attempt < 2:
                time.sleep(2)
                continue
            return None

    return None


def collect_github_urls() -> List[Tuple[int, str, Tuple[str, str, str, str]]]:
    if not os.path.exists(URLS_FILE):
        print(f"ERROR: {URLS_FILE} not found")
        sys.exit(1)

    with open(URLS_FILE, encoding="utf-8") as f:
        lines = f.readlines()

    entries: List[Tuple[int, str, Tuple[str, str, str, str]]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed = parse_github_url(stripped)
        if parsed:
            entries.append((i, stripped, parsed))

    return entries


def load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"warning: could not save cache: {e}")


def run(dry_run: bool, stale_days: int) -> None:
    session = build_session()
    cache = load_cache()
    entries = collect_github_urls()
    threshold = timedelta(days=stale_days)
    now = datetime.now(timezone.utc)

    # Warm up: check rate limit before starting
    remaining, limit = check_rate_limit(session)
    auth_status = f"authenticated ({limit} req/hr)" if GITHUB_TOKEN else f"unauthenticated ({limit} req/hr)"

    print(f"checking {len(entries)} GitHub URLs (threshold: {stale_days}d)")
    print(f"auth: {auth_status}")
    print(f"rate limit: {remaining}/{limit} remaining")
    print(f"mode: {'DRY RUN (no changes)' if dry_run else 'APPLY (will remove stale URLs)'}")

    if remaining < len(entries):
        print(f"warning: only {remaining} requests available, need {len(entries)}. run with a token or increase --days.")
        if remaining <= 0:
            print("rate limit exhausted. try again later.")
            return

    print()

    results: List[Tuple[str, Optional[str], bool]] = []
    done = 0
    fresh_count = 0
    stale_count = 0
    error_count = 0

    for entry in entries:
        idx, url, parsed = entry
        cache_key = make_cache_key(parsed)

        # Check cache first
        cached = cache.get(cache_key)
        if cached and cached.get("date"):
            dt = datetime.fromisoformat(cached["date"])
            is_stale = (now - dt) > threshold
            results.append((url, cached["date"], is_stale))
            if is_stale:
                stale_count += 1
            else:
                fresh_count += 1
            done += 1
            if done % 100 == 0 or done == len(entries) or done == 1:
                print(f"  progress: {done}/{len(entries)} (cached: {done - (error_count + stale_count + fresh_count) + fresh_count + stale_count}, fresh={fresh_count}, stale={stale_count}, errors={error_count})")
            continue

        # Make API call
        date_str = get_last_commit_date(session, *parsed)
        time.sleep(_REQUEST_DELAY)  # pace ourselves

        if date_str:
            dt = datetime.fromisoformat(date_str)
            is_stale = (now - dt) > threshold
            results.append((url, date_str, is_stale))
            if is_stale:
                stale_count += 1
            else:
                fresh_count += 1
        else:
            is_stale = True
            results.append((url, None, True))
            error_count += 1
            stale_count += 1

        # Update cache
        cache_key_final = make_cache_key(parsed)
        cache[cache_key_final] = {"date": date_str, "stale": is_stale}

        done += 1
        if done % 100 == 0 or done == len(entries) or done == 1:
            print(f"  progress: {done}/{len(entries)} (fresh={fresh_count}, stale={stale_count}, errors={error_count})")
            save_cache(cache)

        # Check rate limit every 100 requests
        if done % 100 == 0:
            rem, _ = check_rate_limit(session)
            if rem < 10:
                print(f"  warning: only {rem} rate limit requests remaining")
                if rem <= 0:
                    print("  rate limit exhausted, stopping early")
                    break

    # Save final cache
    save_cache(cache)

    stale_urls = [(url, d) for url, d, s in results if s]
    fresh_urls = [(url, d) for url, d, s in results if not s and d]

    print()
    print(f"results: {len(fresh_urls)} fresh, {len(stale_urls)} stale, {error_count} errors")

    if stale_urls:
        stale_with_age = [(datetime.now(timezone.utc) - datetime.fromisoformat(d)).days if d else -1 for _, d in stale_urls]
        aged = list(zip(stale_with_age, [u for u, _ in stale_urls], [d for _, d in stale_urls]))
        aged.sort(key=lambda x: -x[0])

        print()
        print(f"stale URLs ({len(aged)}):")
        for age, url, date_str in aged:
            label = f"[{age:>4}d]" if age >= 0 else "[  ???]"
            print(f"  {label} {url[:90]}")

        buckets = [(30, 60), (60, 90), (90, 180), (180, 365), (365, 9999)]
        print()
        print("breakdown by age:")
        for lo, hi in buckets:
            count = sum(1 for a, _, _ in aged if lo <= a < hi)
            if count:
                print(f"  {lo}-{hi}d: {count}")
        unknown = sum(1 for a, _, _ in aged if a < 0)
        if unknown:
            print(f"  unknown (no commit data): {unknown}")

    if dry_run:
        print()
        print("dry run — no changes made.")
        print(f"run with --apply to remove {len(stale_urls)} stale URLs.")
        print()
        return

    # Remove stale lines
    stale_set = {url for url, _ in stale_urls}
    with open(URLS_FILE, encoding="utf-8") as f:
        all_lines = f.readlines()

    new_lines = []
    removed = []
    for line in all_lines:
        stripped = line.strip()
        if stripped in stale_set:
            removed.append(stripped)
            continue
        new_lines.append(line)

    backup = URLS_FILE + ".stale.backup"
    with open(backup, "w", encoding="utf-8") as f:
        f.writelines(all_lines)
    with open(URLS_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print()
    print(f"removed {len(removed)} stale URLs from URLS.txt")
    print(f"backup saved to: {backup}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove stale GitHub URLs from URLS.txt")
    parser.add_argument("--apply", action="store_true", help="actually remove (default: dry run)")
    parser.add_argument("--days", type=int, default=30, help="stale threshold in days (default: 30)")
    args = parser.parse_args()
    run(dry_run=not args.apply, stale_days=args.days)
