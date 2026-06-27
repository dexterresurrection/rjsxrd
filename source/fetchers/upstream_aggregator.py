"""Fetches upstream source lists and extracts yudou226.top + guidongone gist configs."""

import re
import concurrent.futures
import threading
from typing import List, Optional
from urllib.parse import urlparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fetchers.fetcher import fetch_data
from utils.logger import log
from utils.file_utils import prepare_config_content
from utils.executor_cache import ExecutorCache

MERMEROO_URL = "https://raw.githubusercontent.com/mermeroo/V2RAY-CLASH-BASE64-Subscription.Links/main/SUB%20LINKS"
LEON406_URL = "https://raw.githubusercontent.com/Leon406/jsdelivr/master/subscribe/subpools"

GUIDONGONE_PATH_PATTERN = re.compile(r"/guidongone/")


def _fetch_and_extract_urls(source_url: str) -> List[str]:
    result = fetch_data(source_url, timeout=15)
    if not result.success:
        return []

    urls = []
    for line in result.text.splitlines():
        line = line.strip()
        if not line.startswith("http"):
            continue

        try:
            parsed = urlparse(line)
            host = parsed.netloc.lower()
        except ValueError:
            continue

        if host == "hh.yudou226.top":
            urls.append(line)
        elif host == "gist.githubusercontent.com" and GUIDONGONE_PATH_PATTERN.search(parsed.path):
            urls.append(line)

    return urls


def _fetch_config(url: str) -> Optional[List[str]]:
    result = fetch_data(url, timeout=15)
    if not result.success:
        return None

    configs = prepare_config_content(result.text)
    if configs:
        return configs

    # Try base64 decode
    try:
        import base64
        decoded = base64.b64decode(result.text.strip()).decode("utf-8", errors="ignore")
        configs = prepare_config_content(decoded)
    except (ValueError, TypeError, UnicodeDecodeError):
        pass

    return configs or None


def fetch_upstream_dynamic_configs(seen: Optional[set] = None, seen_lock: Optional[threading.Lock] = None) -> List[str]:
    all_new_urls = []
    for src_url in (MERMEROO_URL, LEON406_URL):
        urls = _fetch_and_extract_urls(src_url)
        log(f"Upstream {src_url.split('/')[2][:30]}...: {len(urls)} yudou/guidongone URLs found")
        all_new_urls.extend(urls)

    if not all_new_urls:
        return []
    all_configs = []
    executor = ExecutorCache.get('upstream_fetch', max_workers=20)
    future_to_url = {executor.submit(_fetch_config, url): url for url in all_new_urls}
    for future in concurrent.futures.as_completed(future_to_url):
        configs = future.result()
        if configs:
            if seen is not None and seen_lock is not None:
                with seen_lock:
                    for cfg in configs:
                        if cfg and cfg not in seen:
                            seen.add(cfg)
                            all_configs.append(cfg)
            else:
                all_configs.extend(configs)

    log(f"Upstream aggregator: {len(all_configs)} configs from {len(all_new_urls)} dynamic URLs")
    return all_configs
