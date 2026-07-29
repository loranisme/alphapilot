"""SEC EDGAR companyfacts collector.

The only networked component of the EDGAR fundamental path. Resolves tickers to
CIKs via SEC's ``company_tickers.json`` and fetches each ticker's
``companyfacts`` XBRL JSON, caching raw responses on disk so the pure transform
in ``research_platform.edgar_fundamentals`` can run offline afterwards.

SEC access rules honoured here: a descriptive ``User-Agent`` with a contact is
required, and requests are rate-limited to <=10/s. See
https://www.sec.gov/os/accessing-edgar-data.
"""

from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_DEFAULT_UA = "factor-research contact@example.com"  # override with a real contact
_MIN_INTERVAL = 0.15  # seconds between requests (<10/s; SEC drops on sustained bursts)
_RETRY_ATTEMPTS = 4
_RETRY_SLEEP = 2.0

# Transient network failures worth retrying. RemoteDisconnected (a ConnectionError
# / HTTPException subclass) is what SEC raises when it drops a connection under
# load, and it is NOT a urllib.error.URLError — it must be caught explicitly or it
# escapes retry and aborts the whole crawl.
_TRANSIENT_ERRORS = (
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionError,
    TimeoutError,
    socket.timeout,
)


class _RateLimiter:
    def __init__(self, min_interval: float = _MIN_INTERVAL):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


def _get_json(url: str, user_agent: str, limiter: _RateLimiter) -> dict | None:
    for attempt in range(_RETRY_ATTEMPTS):
        limiter.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            # 403/429/5xx: back off (SEC throttles bursts) and retry
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_SLEEP * (attempt + 1))
        except _TRANSIENT_ERRORS:
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_SLEEP * (attempt + 1))
    return None


def load_ticker_cik_map(
    user_agent: str, cache_dir: Path, limiter: _RateLimiter | None = None
) -> dict[str, int]:
    """Ticker (upper) -> CIK int, cached as ``company_tickers.json`` on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "company_tickers.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        limiter = limiter or _RateLimiter()
        payload = _get_json(_TICKERS_URL, user_agent, limiter)
        path.write_text(json.dumps(payload), encoding="utf-8")
    return {
        str(row["ticker"]).upper(): int(row["cik_str"])
        for row in payload.values()
    }


def fetch_companyfacts(
    tickers: list[str],
    user_agent: str = _DEFAULT_UA,
    cache_dir: Path | None = None,
    cache_max_age_days: float = 30.0,
    on_progress=None,
) -> dict[str, dict]:
    """Fetch (or load cached) companyfacts JSON for each ticker.

    Returns ``{ticker: companyfacts_json}`` for tickers with data. Cache files are
    ``data/edgar_cache/<TICKER>_companyfacts.json``; a cache newer than
    ``cache_max_age_days`` is reused without any network call.
    """
    if cache_dir is None:
        cache_dir = _default_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    limiter = _RateLimiter()
    cik_map = load_ticker_cik_map(user_agent, cache_dir, limiter)

    results: dict[str, dict] = {}
    for i, raw_ticker in enumerate(tickers):
        ticker = str(raw_ticker).upper()
        cache_path = cache_dir / f"{ticker}_companyfacts.json"
        facts = _load_cached_facts(cache_path, cache_max_age_days)
        if facts is None:
            cik = cik_map.get(ticker) or cik_map.get(ticker.replace("-", "."))
            if cik is None:
                if on_progress:
                    on_progress(i, ticker, "no_cik")
                continue
            # A ticker that still fails after all retries is skipped, not fatal:
            # one dropped connection must not lose an in-progress crawl.
            try:
                facts = _get_json(_FACTS_URL.format(cik=cik), user_agent, limiter)
            except Exception:
                if on_progress:
                    on_progress(i, ticker, "error")
                continue
            if facts is not None:
                cache_path.write_text(json.dumps(facts), encoding="utf-8")
        if facts:
            results[ticker] = facts
            if on_progress:
                on_progress(i, ticker, "ok")
        elif on_progress:
            on_progress(i, ticker, "no_data")
    return results


def _load_cached_facts(path: Path, max_age_days: float) -> dict | None:
    if not path.exists():
        return None
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > max_age_days:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _default_cache_dir() -> Path:
    here = Path(__file__).resolve().parent
    for _ in range(4):
        if (here / "data").exists():
            return here / "data" / "edgar_cache"
        here = here.parent
    return Path("data") / "edgar_cache"
