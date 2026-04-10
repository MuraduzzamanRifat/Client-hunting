"""
Proxy rotation manager.
Loads proxies from PROXY_LIST env var or proxies.txt file.
Auto-rotates, tracks failures, and removes dead proxies.
"""

import os
import random
import time
import threading
import requests

# Lock for thread-safe proxy access
_lock = threading.Lock()


class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.failed = {}  # proxy -> fail_count
        self.max_fails = 3
        self._index = 0
        self._load_proxies()

    def _load_proxies(self):
        """Load proxies from env var or file."""
        # Try env var first: PROXY_LIST=ip:port:user:pass,ip2:port2:user2:pass2
        raw = os.getenv("PROXY_LIST", "")
        if raw:
            for entry in raw.split(","):
                proxy = self._parse_proxy(entry.strip())
                if proxy:
                    self.proxies.append(proxy)

        # Try proxies.txt file
        proxy_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "proxies.txt")
        if os.path.exists(proxy_file):
            with open(proxy_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxy = self._parse_proxy(line)
                        if proxy:
                            self.proxies.append(proxy)

    def _parse_proxy(self, entry):
        """
        Parse proxy string. Supports formats:
        - ip:port
        - ip:port:user:pass
        - http://ip:port
        - http://user:pass@ip:port
        - socks5://ip:port
        """
        if not entry:
            return None

        # Already a URL format
        if "://" in entry:
            return {"url": entry}

        parts = entry.split(":")
        if len(parts) == 2:
            return {"url": f"http://{parts[0]}:{parts[1]}"}
        elif len(parts) == 4:
            return {"url": f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"}

        return None

    def get_proxy(self):
        """Get next proxy in rotation. Returns None if no proxies available."""
        with _lock:
            if not self.proxies:
                return None

            # Filter out dead proxies
            alive = [p for p in self.proxies if self.failed.get(p["url"], 0) < self.max_fails]
            if not alive:
                # Reset all failures and try again
                self.failed = {}
                alive = self.proxies

            self._index = (self._index + 1) % len(alive)
            proxy = alive[self._index]

            return {
                "http": proxy["url"],
                "https": proxy["url"],
            }

    def report_success(self, proxy_dict):
        """Mark a proxy as working."""
        if proxy_dict:
            url = proxy_dict.get("http", "")
            with _lock:
                if url in self.failed:
                    self.failed[url] = max(0, self.failed[url] - 1)

    def report_failure(self, proxy_dict):
        """Mark a proxy as failed."""
        if proxy_dict:
            url = proxy_dict.get("http", "")
            with _lock:
                self.failed[url] = self.failed.get(url, 0) + 1

    def has_proxies(self):
        """Check if any proxies are loaded."""
        return len(self.proxies) > 0

    def count(self):
        """Total proxies loaded."""
        return len(self.proxies)

    def alive_count(self):
        """Proxies that haven't been marked dead."""
        return len([p for p in self.proxies if self.failed.get(p["url"], 0) < self.max_fails])


def make_request(url, proxy_manager=None, headers=None, timeout=15, max_retries=3, **kwargs):
    """
    Make an HTTP request with proxy rotation and auto-retry.
    Falls back to direct connection if no proxies or all fail.
    """
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    last_error = None

    for attempt in range(max_retries):
        proxy = proxy_manager.get_proxy() if proxy_manager and proxy_manager.has_proxies() else None

        try:
            resp = requests.get(
                url,
                headers=default_headers,
                proxies=proxy,
                timeout=timeout,
                **kwargs,
            )

            # Check if blocked by Google
            if resp.status_code == 429 or "unusual traffic" in resp.text.lower():
                if proxy:
                    proxy_manager.report_failure(proxy)
                # Backoff before retry
                time.sleep(2 ** attempt + random.uniform(1, 3))
                continue

            if resp.status_code == 200:
                if proxy:
                    proxy_manager.report_success(proxy)
                return resp

            last_error = f"HTTP {resp.status_code}"

        except requests.exceptions.ProxyError:
            if proxy:
                proxy_manager.report_failure(proxy)
            last_error = "Proxy error"
        except requests.exceptions.Timeout:
            if proxy:
                proxy_manager.report_failure(proxy)
            last_error = "Timeout"
        except Exception as e:
            last_error = str(e)

        # Backoff
        time.sleep(1 + random.uniform(0.5, 2))

    # Final attempt without proxy
    if proxy_manager and proxy_manager.has_proxies():
        try:
            resp = requests.get(url, headers=default_headers, timeout=timeout, **kwargs)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass

    return None
