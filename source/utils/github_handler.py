"""GitHub API handler for uploading files.

Uses a _GitHubClient abstraction behind the scenes so it can be tested
without real GitHub credentials. The default _PyGithubClient wraps the
PyGithub library; pass a FakeGitHubClient in tests for isolated testing.
"""

from github import Github, Auth, GithubException
import os
import time
import threading
import concurrent.futures
from datetime import datetime, timezone, timedelta
from typing import NamedTuple
from abc import ABC, abstractmethod

from utils.logger import log


class RateLimitStatus(NamedTuple):
    """Result of a rate limit check."""
    has_capacity: bool
    seconds_to_wait: int


# ── Client abstraction ────────────────────────────────────────────────

class _GitHubClient(ABC):
    """Interface for GitHub API operations used by GitHubHandler.

    Implementations: _PyGithubClient (real), FakeGitHubClient (tests).
    """

    @abstractmethod
    def get_repo(self, repo_name: str):
        """Get a repo handle. Returns None on failure."""
        return None

    @abstractmethod
    def get_rate_limiting(self):
        """Return (remaining, limit) tuple or (None, None) on error."""
        return None, None

    @abstractmethod
    def get_rate_limiting_reset(self):
        """Return reset timestamp (Unix seconds) or None on error."""
        return None

    @abstractmethod
    def get_contents(self, repo, remote_path: str):
        """Get file contents from repo. Returns (decoded_data, sha) or (None, None)."""
        return None, None

    @abstractmethod
    def create_file(self, repo, remote_path: str, message: str, content: str):
        """Create a new file in the repo. Returns True on success."""
        return True

    @abstractmethod
    def update_file(self, repo, remote_path: str, message: str, content: str, sha: str):
        """Update an existing file. Returns True on success."""
        return True


class _PyGithubClient(_GitHubClient):
    """Real GitHub client using PyGithub library."""

    def __init__(self) -> None:
        from config.settings import GITHUB_TOKEN
        if GITHUB_TOKEN:
            self.g = Github(auth=Auth.Token(GITHUB_TOKEN))
        else:
            self.g = Github()

    def get_repo(self, repo_name: str):
        try:
            return self.g.get_repo(repo_name)
        except GithubException as e:
            log("Could not access repository {0}: {1}".format(repo_name, e))
            return None

    def get_rate_limiting(self):
        try:
            return self.g.rate_limiting
        except (GithubException, OSError):
            return None, None

    def get_rate_limiting_reset(self):
        try:
            return self.g.rate_limiting_resettime
        except (GithubException, OSError):
            return None

    def get_contents(self, repo, remote_path: str):
        try:
            file_in_repo = repo.get_contents(remote_path)
            return file_in_repo.decoded_content, file_in_repo.sha
        except GithubException as e:
            if getattr(e, "status", None) == 404:
                return None, None
            raise

    def create_file(self, repo, remote_path: str, message: str, content: str):
        repo.create_file(path=remote_path, message=message, content=content)
        return True

    def update_file(self, repo, remote_path: str, message: str, content: str, sha: str):
        repo.update_file(path=remote_path, message=message, content=content, sha=sha)
        return True


# ── Handler ───────────────────────────────────────────────────────────

class GitHubHandler:
    def __init__(self, client: _GitHubClient = None) -> None:
        """Initialize GitHub handler.

        Args:
            client: Optional _GitHubClient instance. If None, creates a
                    _PyGithubClient using GITHUB_TOKEN from settings.
        """
        self._rate_limit_lock = threading.Lock()
        self._client = client or _PyGithubClient()

        from config.settings import REPO_NAME
        self.repo = self._client.get_repo(REPO_NAME)

        # Check GitHub API limits
        remaining, limit = self._client.get_rate_limiting()
        if remaining is not None:
            if remaining < 100:
                log("Warning: {0}/{1} GitHub API requests remaining".format(remaining, limit))
            else:
                log("Available GitHub API requests: {0}/{1}".format(remaining, limit))

    def _check_rate_limit(self, required_requests: int = 1) -> RateLimitStatus:
        """Check if we have enough API requests remaining.

        Args:
            required_requests: Number of requests needed

        Returns:
            RateLimitStatus with has_capacity and seconds_to_wait
        """
        try:
            remaining, limit = self._client.get_rate_limiting()
            if remaining is None:
                return RateLimitStatus(True, 0)  # can't check, proceed

            reset_time = self._client.get_rate_limiting_reset()
            if reset_time is None:
                return RateLimitStatus(True, 0)  # can't check reset time, proceed

            if remaining < required_requests:
                wait_seconds = max(0, reset_time - time.time())
                if wait_seconds > 0:
                    log("Rate limit reached. Waiting {0:.0f}s for reset... ({1}/{2} remaining)".format(
                        wait_seconds, remaining, limit))
                    return RateLimitStatus(False, int(wait_seconds))
                return RateLimitStatus(False, 0)

            # Warn if running low
            if remaining < 100:
                log("Warning: Only {0}/{1} API requests remaining".format(remaining, limit))

            return RateLimitStatus(True, 0)

        except (GithubException, OSError) as e:
            log("Could not check rate limit ({0}): {1}".format(type(e).__name__, e))
            return RateLimitStatus(True, 0)  # Proceed on error

    def _wait_for_rate_limit(self, seconds: int) -> None:
        """Wait for rate limit to reset with progress logging."""
        if seconds <= 0:
            return
        log("Waiting {0}s for GitHub API rate limit reset...".format(seconds))
        time.sleep(seconds)

    def _handle_rate_limit_error(self, e: GithubException, attempt: int) -> bool:
        """Handle rate limit error with exponential backoff.

        Args:
            e: The GithubException that was raised
            attempt: Current attempt number (1-based)

        Returns:
            True if should retry, False if should give up
        """
        if getattr(e, "status", None) == 403:
            # Check for Retry-After header
            retry_after = None
            if hasattr(e, 'headers') and e.headers:
                retry_after = e.headers.get('Retry-After')

            if retry_after:
                wait_time = int(retry_after)
                log("Rate limited by GitHub. Waiting {0}s (Retry-After header)...".format(wait_time))
                time.sleep(wait_time)
                return True

            # Exponential backoff if no Retry-After header
            wait_time = min(60, 2 ** attempt)
            log("Rate limited (403). Waiting {0}s (attempt {1})...".format(wait_time, attempt))
            time.sleep(wait_time)
            return True

        return False

    @staticmethod
    def _file_exists(local_path: str) -> bool:
        """Check if local file exists before uploading."""
        return os.path.exists(local_path) and os.path.isfile(local_path)

    @staticmethod
    def _get_basename(remote_path: str) -> str:
        """Extract filename from remote path."""
        return os.path.basename(remote_path)

    @staticmethod
    def _get_timestamp() -> str:
        """Return current UTC timestamp string for commit messages."""
        return datetime.now(timezone(timedelta(hours=3))).strftime("%H:%M | %d.%m.%Y")

    @staticmethod
    def _add_to_updated_files(remote_path: str) -> None:
        """Track uploaded files. Currently a no-op — was planned for dedup reporting."""
        pass

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Uploads a local file to GitHub repository.

        Returns:
            True on success, False on failure
        """
        if self.repo is None:
            log("ERROR: GitHub repository not available (repo is None)")
            return False

        if not self._file_exists(local_path):
            log("File {0} not found.".format(local_path))
            return False

        # Check rate limit before starting (need ~2 requests: get + update)
        has_capacity, wait_time = self._check_rate_limit(required_requests=2)
        if not has_capacity:
            self._wait_for_rate_limit(wait_time)
            # Check again after waiting
            has_capacity, _ = self._check_rate_limit(required_requests=2)
            if not has_capacity:
                log("ERROR: Still rate limited, skipping {0}".format(remote_path))
                return False

        with open(local_path, "r", encoding="utf-8") as file:
            content = file.read()

        max_retries = 5

        for attempt in range(1, max_retries + 1):
            try:
                # Try to get the existing file to check for changes
                decoded_content, current_sha = self._client.get_contents(self.repo, remote_path)

                if current_sha is None:
                    # File doesn't exist, create it
                    try:
                        basename = self._get_basename(remote_path)
                        self._client.create_file(
                            self.repo, remote_path,
                            "auto: add {0}: {1}".format(basename, self._get_timestamp()),
                            content,
                        )
                        log("File {0} created.".format(remote_path))
                        self._add_to_updated_files(remote_path)
                        return True
                    except GithubException as e_create:
                        status = getattr(e_create, "status", None)
                        if status == 403:
                            if self._handle_rate_limit_error(e_create, attempt):
                                continue
                            return False
                        if status in (409, 422):
                            # Race: file was created between our get_contents
                            # and create_file. Retry outer loop to re-read SHA.
                            if attempt < max_retries:
                                log("File {0} already exists, retrying with update "
                                    "(attempt {1}/{2})".format(
                                    remote_path, attempt, max_retries))
                                time.sleep(0.5)
                                continue
                            log("Could not create {0} after {1} attempts".format(
                                remote_path, max_retries))
                            return False
                        msg = e_create.data.get("message", str(e_create))
                        log("Error creating {0}: {1}".format(remote_path, msg))
                        return False

                try:
                    remote_content = decoded_content.decode("utf-8", errors="replace")
                    if remote_content == content:
                        log("No changes for {0}.".format(remote_path))
                        return True
                except (UnicodeDecodeError, ValueError):
                    pass

                # Update the file
                basename = self._get_basename(remote_path)
                try:
                    self._client.update_file(
                        self.repo, remote_path,
                        "auto: update {0}: {1}".format(basename, self._get_timestamp()),
                        content, current_sha,
                    )
                    log("File {0} updated in repository.".format(remote_path))
                    self._add_to_updated_files(remote_path)
                    return True
                except GithubException as e_upd:
                    status = getattr(e_upd, "status", None)
                    if status == 409:
                        # Conflict - retry with backoff
                        if attempt < max_retries:
                            wait_time = 0.5 * (2 ** (attempt - 1))
                            log("SHA conflict for {0}, attempt {1}/{2}, waiting {3} sec".format(
                                remote_path, attempt, max_retries, wait_time))
                            time.sleep(wait_time)
                            continue
                        else:
                            log("Could not update {0} after {1} attempts".format(remote_path, max_retries))
                            return False
                    elif status == 403:
                        # Rate limited
                        if self._handle_rate_limit_error(e_upd, attempt):
                            continue
                        return False
                    else:
                        msg = e_upd.data.get("message", str(e_upd))
                        log("Error uploading {0}: {1}".format(remote_path, msg))
                        return False

            except (OSError, GithubException) as e_general:
                short_msg = str(e_general)
                if len(short_msg) > 200:
                    short_msg = short_msg[:200] + "..."
                log("Unexpected error updating {0}: {1}".format(remote_path, short_msg))
                return False

        log("Could not update {0} after {1} attempts".format(remote_path, max_retries))
        return False

    def upload_multiple_files(self, file_pairs: list, dry_run: bool = False) -> int:
        """Uploads multiple config files to GitHub.

        Args:
            file_pairs: List of (local_path, remote_path) tuples
            dry_run: If True, skip actual upload

        Returns:
            Number of failed uploads (0 = success)
        """
        from utils.executor_cache import ExecutorCache

        max_workers_upload = max(2, min(6, len(file_pairs)))
        failures = 0

        # Use cached executor for network I/O
        executor = ExecutorCache.get('github_upload', max_workers=max_workers_upload)
        upload_futures = []

        for local_path, remote_path in file_pairs:
            if dry_run:
                log("Dry-run: skipping upload of {0} (local path {1})".format(remote_path, local_path))
            else:
                upload_futures.append(
                    executor.submit(self.upload_file, local_path, remote_path)
                )

        for uf in concurrent.futures.as_completed(upload_futures):
            try:
                result = uf.result()
                # upload_file returns False on failure
                if result is False:
                    failures += 1
            except (concurrent.futures.CancelledError, RuntimeError) as e:
                log("Upload future failed: {0}".format(e))
                failures += 1

        if failures > 0:
            log("WARNING: {0} upload(s) failed".format(failures))

        return failures
