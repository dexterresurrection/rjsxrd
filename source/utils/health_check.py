"""Health check utilities for VPN config generator."""

import os
import socket
import shutil
from typing import Dict, Tuple
from utils.logger import log


def check_internet_connectivity() -> bool:
    """Check if internet is accessible.
    
    Returns:
        True if internet is reachable, False otherwise
    """
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except OSError:
        return False


def check_disk_space(path: str = ".", required_mb: float = 100.0) -> Tuple[bool, float]:
    """Check available disk space.
    
    Args:
        path: Path to check
        required_mb: Minimum required space in MB
        
    Returns:
        Tuple of (has_enough_space, available_mb)
    """
    try:
        total, used, free = shutil.disk_usage(path)
        available_mb = free / (1024 * 1024)
        return available_mb >= required_mb, available_mb
    except (OSError, PermissionError) as e:
        log(f"Could not check disk space: {e}")
        return True, float('inf')


def check_memory(min_mb: float = 256.0) -> Tuple[bool, float]:
    """Check available system memory.
    
    Args:
        min_mb: Minimum required memory in MB
        
    Returns:
        Tuple of (has_enough_memory, available_mb)
    """
    try:
        import psutil
        available = psutil.virtual_memory().available / (1024 * 1024)
        return available >= min_mb, available
    except ImportError:
        # psutil not available, assume OK
        return True, float('inf')
    except (OSError, AttributeError) as e:
        log(f"Could not check memory: {e}")
        return True, float('inf')


def check_xray_binary(xray_path: str) -> bool:
    """Check if Xray binary exists and is executable.
    
    Args:
        xray_path: Path to Xray binary
        
    Returns:
        True if Xray exists and is executable
    """
    if not os.path.exists(xray_path):
        return False
    
    if not os.access(xray_path, os.X_OK):
        return False
    
    return True


def check_github_token(token: str) -> bool:
    """Validate GitHub token format.
    
    Args:
        token: GitHub token to validate
        
    Returns:
        True if token looks valid
    """
    if not token:
        return False
    
    if len(token) < 10:
        return False
    
    return True


def health_check(xray_path: str = None, github_token: str = None) -> Dict[str, bool]:
    """Perform comprehensive health check.
    
    Args:
        xray_path: Path to Xray binary
        github_token: GitHub token for API access
        
    Returns:
        Dictionary of health check results
    """
    from config.settings import GITHUB_TOKEN
    
    results = {
        'internet': check_internet_connectivity(),
        'disk_space': check_disk_space()[0],
        'memory': check_memory()[0],
        'xray_installed': check_xray_binary(xray_path) if xray_path else False,
        'github_token': check_github_token(github_token or GITHUB_TOKEN),
    }
    
    # Log warnings for failed checks
    for check, passed in results.items():
        if not passed:
            log(f"WARNING: Health check failed: {check}")
    
    return results


def print_health_report(results: Dict[str, bool]) -> None:
    """Print formatted health check report.
    
    Args:
        results: Dictionary from health_check()
    """
    log("\n" + "=" * 60)
    log("HEALTH CHECK REPORT")
    log("=" * 60)
    
    all_passed = True
    for check, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        check_name = check.replace('_', ' ').title()
        log(f"  {check_name:25} {status}")
        if not passed:
            all_passed = False
    
    log("=" * 60)
    if all_passed:
        log("All health checks passed")
    else:
        log("Some health checks failed - review warnings above")
    log("=" * 60 + "\n")
