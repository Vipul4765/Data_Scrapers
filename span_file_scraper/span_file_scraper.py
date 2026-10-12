#!/usr/bin/env python3
"""Download the latest SPAN risk-parameter file for NSE, BSE and MCX.

Why browser-backed discovery?
-----------------------------
The exchange sites are dynamic and their internal/static download URLs change.
This script uses Playwright only to discover the actual public file download,
then validates/extracts the .spn file before writing it atomically.

How a file is found (same steps for every exchange):
  1. open the exchange page in Chromium (NSE: home page first, for cookies);
  2. collect every file link the page exposes -- <a href>s, URLs inside the
     HTML/JS, and file names inside the JSON the page loads (a report list
     like {"filePath": ".../", "fileActlName": "x.zip"} is joined up);
  3. keep the links whose file name is a SPAN file for --date, newest first;
  4. download it (HTTP request with the page's cookies, then a real browser
     tab if that is refused), unzip/gunzip, validate, write.
  When the page has no direct link at all, the script clicks the page's
  SPAN/download controls and keeps whatever the browser downloads.

Nothing is written unless it passes validation: the exchange's file-name
pattern, the date in the name, a complete <spanFile>...</spanFile> XML body,
and the <pointInTime><date> inside the file -- all must match --date.

Install:
    pip install playwright
    playwright install chromium

Examples:
    python span_file_scraper.py
    python span_file_scraper.py --exchange nse
    python span_file_scraper.py --exchange nse,bse,mcx --date 2026-10-01
    python span_file_scraper.py --exchange nse --list   # show what is published, download nothing
    python span_file_scraper.py --headed
    python span_file_scraper.py --dest ./span_files

Output:    <dest>/<YYYYMMDD>/<NSE|BSE|MCX>/<exchange's own file name>.spn
Exit code: 0 when every requested exchange ended with a valid file, else 2.
"""

from __future__ import annotations

import argparse
import gzip
import html
import io
import json
import os
import re
import sys
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # the helpers stay importable; main() prints the install step
    sync_playwright = None
    PlaywrightError = PlaywrightTimeoutError = Exception

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Download, Page, Request

IST = ZoneInfo("Asia/Kolkata")

NSE_PAGE = "https://www.nseindia.com/all-reports-derivatives"
BSE_PAGE = "https://www.bseclearing.com/riskmgmt/RiskparameterNEW.aspx"
MCX_PAGE = "https://www.mcxindia.com/education-training/daily-span-risk-parameter-file"

PAGES = {
    "nse": NSE_PAGE,
    "bse": BSE_PAGE,
    "mcx": MCX_PAGE,
}

# NSE refuses a cold client; its home page sets the cookies the reports page needs.
WARMUP_PAGES = {"nse": "https://www.nseindia.com/"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

SPAN_NAME_PATTERNS = {
    "nse": re.compile(r"nsccl\.(\d{8})\.(?:i(\d+)|s|b)(?:\.parallel)?\.spn(?:\.gz)?$", re.I),
    "bse": re.compile(r"BSERISK(\d{8})-(\d+)\.spn(?:\.gz)?$", re.I),
    "mcx": re.compile(r"MCXRPF-(\d{8})-(\d{4})-(\d+)-([IE])\.spn(?:\.gz)?$", re.I),
}

# The XML valuation date appears near the beginning of CME-SPAN files.
POINT_IN_TIME_RE = re.compile(rb"<pointInTime>\s*<date>(\d{8})</date>", re.I)
POINT_IN_TIME_SCAN_BYTES = 262_144
CREATED_RE = re.compile(rb"<created>(\d{12})</created>")

# Bounded lengths: an unbounded lazy run backtracks across every "/" of an
# inline base64 blob and stalls on a ~1 MB page (it hung on NSE's).
FILE_URL_RE = re.compile(
    r"(?:https?://[^\s\"'<>]{1,500}?|/[^\s\"'<>]{1,300}?)"
    r"(?:\.zip|\.spn(?:\.gz)?|\.gz)(?![\w.-])(?:\?[^\s\"'<>]{0,300})?",
    re.I,
)

LIKELY_SPAN_RE = re.compile(
    r"(?:nsccl|span|risk.?parameter|BSERISK|MCXRPF|RPF)",
    re.I,
)

DOWNLOAD_TEXT_RE = re.compile(r"download|span|risk\s*parameter|rpf", re.I)

ARCHIVE_NAME_RE = re.compile(r"\.(?:zip|gz|spn)$", re.I)

CLICKABLE = "a, button, input[type=submit], input[type=button], input[type=image], [role=button]"
CLICKABLE_LABELS_JS = """els => els.map(e => [
    e.innerText, e.value, e.title, e.id, e.getAttribute('name'),
    e.getAttribute('href'), e.getAttribute('aria-label')
].filter(Boolean).join(' '))"""
MAX_CLICKS = 12

# Response bodies are scanned for links only when they are text and this small.
TEXT_BODY_LIMIT = 8_000_000
SKIP_BODY_TYPES = {"image", "font", "media", "stylesheet"}
DOWNLOAD_TIMEOUT_MS = 180_000

DEFAULT_DEST = Path(__file__).resolve().parent / "span_files"


@dataclass(frozen=True)
class FoundFile:
    exchange: str
    name: str
    data: bytes
    source: str


@dataclass(frozen=True)
class Candidate:
    """One SPAN file the page links to, with every URL seen for it."""

    name: str
    rank: tuple[int, ...]
    urls: tuple[str, ...]


def _target_date(value: str | None) -> date:
    if value is None:
        return datetime.now(IST).date()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("--date must be YYYY-MM-DD, YYYYMMDD, DD-MM-YYYY or DD/MM/YYYY")


def _exchange_list(raw: str) -> list[str]:
    items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    bad = sorted(set(items) - set(PAGES))
    if bad:
        raise ValueError(f"unknown exchange(s): {', '.join(bad)}")
    return list(dict.fromkeys(items))


def _safe_name(name: str) -> str:
    name = unquote(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name or "span_file.spn"


def _content_disposition_name(headers: dict[str, str]) -> str | None:
    cd = headers.get("content-disposition", "")
    if not cd:
        return None
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd, re.I)
    return _safe_name(m.group(1).strip()) if m else None


def _as_spn_name(name: str) -> str:
    """The .spn name an archive unpacks to: x.zip / x.spn.gz / x.gz -> x.spn."""
    stem = re.sub(r"(?:\.zip|\.gz)+$", "", name, flags=re.I)
    return stem if stem.lower().endswith(".spn") else f"{stem}.spn"


def _first_line(exc: BaseException) -> str:
    """Playwright errors carry a multi-line call log; the first line is the error."""
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def _short(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}"


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or b"<html" in head


# ---------------------------------------------------------------------------
# File names: matching and ordering
# ---------------------------------------------------------------------------


def _match_name(exchange: str, name: str) -> re.Match[str] | None:
    return SPAN_NAME_PATTERNS[exchange].search(name)


def _rank(exchange: str, m: re.Match[str]) -> tuple[int, ...]:
    """Sort key for files of one day: a higher key was published later."""
    if exchange == "nse":
        text = m.group(0).lower()
        plain = 0 if ".parallel." in text else 1
        if m.group(2) is not None:  # intraday iNN
            return (1, int(m.group(2)), plain)
        if ".s." in text:  # end-of-day settlement file
            return (2, 0, plain)
        return (0, 0, plain)  # beginning-of-day
    if exchange == "bse":
        return (int(m.group(2)),)
    hhmm, seq, kind = m.group(2), m.group(3), m.group(4).upper()
    return (int(hhmm), int(seq), 1 if kind == "E" else 0)


def _is_intraday(exchange: str, m: re.Match[str]) -> bool:
    if exchange == "nse":
        return m.group(2) is not None
    if exchange == "mcx":
        return m.group(4).upper() == "I"
    return True


def _candidates(
    exchange: str, urls: set[str], day_str: str, intraday_only: bool
) -> tuple[list[Candidate], set[str]]:
    """SPAN files for day_str among urls (newest first), plus every SPAN
    file name seen for any day (for the 'nothing for today' message)."""
    by_name: dict[str, list[str]] = {}
    ranked: dict[str, tuple[tuple[int, ...], str]] = {}
    seen: set[str] = set()
    for url in sorted(urls):
        name = _as_spn_name(_safe_name(urlsplit(url).path))
        m = _match_name(exchange, name)
        if not m:
            continue
        seen.add(name)
        if m.group(1) != day_str or (intraday_only and not _is_intraday(exchange, m)):
            continue
        key = name.lower()
        by_name.setdefault(key, []).append(url)
        ranked[key] = (_rank(exchange, m), name)
    # BSE links the same file twice: Risk_Automate/X.ZIP holds an .XML copy,
    # Risk_Automate/SPN/X.ZIP the .spn -- try a /SPN/ path first.
    cands = [
        Candidate(ranked[k][1], ranked[k][0], tuple(sorted(v, key=lambda u: "/spn/" not in u.lower())))
        for k, v in by_name.items()
    ]
    cands.sort(key=lambda c: c.rank, reverse=True)
    return cands, seen


# ---------------------------------------------------------------------------
# Unpack + validate + write
# ---------------------------------------------------------------------------


def _extract_spn_members(blob: bytes, hinted_name: str, source: str, exchange: str) -> list[FoundFile]:
    """The .spn file(s) a download carries: every .spn / .spn.gz member of a
    zip, a gunzipped body, or the body itself. Nothing is validated here."""
    if blob[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        found = []
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for member in zf.namelist():
                lower = member.lower()
                if lower.endswith(".spn"):
                    found.append(FoundFile(exchange, _safe_name(member), zf.read(member), source))
                elif lower.endswith(".spn.gz"):
                    data = gzip.decompress(zf.read(member))
                    found.append(FoundFile(exchange, _safe_name(member[:-3]), data, source))
            if not found:
                print(f"    {hinted_name} has no .spn inside ({', '.join(zf.namelist()[:5])})")
        return found
    if blob[:2] == b"\x1f\x8b":
        name = re.sub(r"\.gz$", "", hinted_name, flags=re.I)
        return _extract_spn_members(gzip.decompress(blob), name, source, exchange)
    return [FoundFile(exchange, _as_spn_name(_safe_name(hinted_name)), blob, source)]


def _unpack(blob: bytes, hinted_name: str, source: str, exchange: str) -> list[FoundFile]:
    try:
        return _extract_spn_members(blob, hinted_name, source, exchange)
    except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as exc:
        print(f"    could not unpack {hinted_name}: {exc}")
        return []


def _problem(found: FoundFile, day_str: str, intraday_only: bool) -> str | None:
    """Why `found` is not a usable SPAN file for day_str, or None if it is."""
    m = _match_name(found.exchange, found.name)
    if not m:
        return f"{found.name!r} is not a {found.exchange.upper()} SPAN file name"
    if m.group(1) != day_str:
        return f"file is for {m.group(1)}, not {day_str}"
    if intraday_only and not _is_intraday(found.exchange, m):
        return "end-of-day/begin-day file, skipped (pass --include-eod to keep it)"
    if b"<spanFile>" not in found.data[:4096]:
        return "content is not SPAN XML (no <spanFile> root)"
    if not found.data.rstrip().endswith(b"</spanFile>"):
        return "file is truncated (no closing </spanFile>)"
    pit = POINT_IN_TIME_RE.search(found.data[:POINT_IN_TIME_SCAN_BYTES])
    if not pit:
        return "no <pointInTime><date> near the start of the file"
    if pit.group(1).decode("ascii") != day_str:
        return f"valuation date inside the file is {pit.group(1).decode('ascii')}, not {day_str}"
    return None


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)  # readers never see a half-written file
    finally:
        tmp.unlink(missing_ok=True)


def _keep_valid(found: list[FoundFile], day_str: str, intraday_only: bool, out_dir: Path, tag: str) -> Path | None:
    """Write the newest valid file in `found`; report why the others were rejected."""
    good = []
    for f in found:
        problem = _problem(f, day_str, intraday_only)
        if problem:
            print(f"{tag}   rejected {f.name}: {problem}")
        else:
            good.append(f)
    if not good:
        return None
    best = max(good, key=lambda f: _rank(f.exchange, _match_name(f.exchange, f.name)))
    path = out_dir / best.name
    _write_atomic(path, best.data)
    created = CREATED_RE.search(best.data[:4096])
    stamp = ""
    if created:
        c = created.group(1).decode("ascii")
        stamp = f", created by the exchange {c[:4]}-{c[4:6]}-{c[6:8]} {c[8:10]}:{c[10:]}"
    print(f"{tag} saved {path} ({len(best.data):,} bytes{stamp})")
    return path


def _prune_older(out_dir: Path, exchange: str, keep: Path, tag: str) -> None:
    """--prune: delete this exchange's SPAN files in out_dir that are older
    than `keep`. A later day's file (the next session's pre-published one) stays."""
    keep_m = _match_name(exchange, keep.name)
    keep_key = (keep_m.group(1), _rank(exchange, keep_m))
    for path in sorted(out_dir.iterdir()):
        m = _match_name(exchange, path.name)
        if path.is_file() and path != keep and m and (m.group(1), _rank(exchange, m)) < keep_key:
            path.unlink()
            print(f"{tag} deleted older {path.name}")


def _existing(out_dir: Path, cand: Candidate, exchange: str, day_str: str, intraday_only: bool) -> Path | None:
    """A valid copy of `cand` already on disk. Matched on the parsed day and
    sequence, not the spelling: NSE links nsccl.D.i3.zip, which holds nsccl.D.i03.spn."""
    if not out_dir.is_dir():
        return None
    for path in out_dir.iterdir():
        m = _match_name(exchange, path.name)
        if path.is_file() and m and m.group(1) == day_str and _rank(exchange, m) == cand.rank:
            found = FoundFile(exchange, path.name, path.read_bytes(), str(path))
            if _problem(found, day_str, intraday_only) is None:
                return path
    return None


# ---------------------------------------------------------------------------
# Link discovery
# ---------------------------------------------------------------------------


def _urls_in_text(text: str, base: str) -> set[str]:
    text = html.unescape(text.replace("\\/", "/"))
    return {urljoin(base, m.group(0)) for m in FILE_URL_RE.finditer(text)}


def _urls_in_json(node: object, base: str, out: set[str]) -> None:
    """File URLs in a JSON document, including ones split into a directory
    field and a file-name field ({"filePath": ".../", "fileActlName": "x.zip"})."""
    if isinstance(node, dict):
        strings = [v for v in node.values() if isinstance(v, str)]
        names = [s for s in strings if "/" not in s and ARCHIVE_NAME_RE.search(s)]
        dirs = [
            s for s in strings
            if s.startswith(("http://", "https://", "/")) and not FILE_URL_RE.fullmatch(s)
        ]
        for d in dirs:
            for n in names:
                out.add(urljoin(urljoin(base, d.rstrip("/") + "/"), n))
        for v in node.values():
            _urls_in_json(v, base, out)
    elif isinstance(node, list):
        for v in node:
            _urls_in_json(v, base, out)
    elif isinstance(node, str):
        out.update(_urls_in_text(node, base))


def _content_length(headers: dict[str, str]) -> int:
    try:
        return int(headers.get("content-length") or 0)
    except ValueError:
        return 0


def _harvest(page: Page, finished: list[Request]) -> set[str]:
    """Every absolute file URL the page shows or loaded. Only FINISHED
    requests are read: a response that is still streaming (a live feed)
    would block the body read forever."""
    urls: set[str] = set()
    for frame in page.frames:
        try:
            urls.update(frame.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"))
            urls.update(_urls_in_text(frame.content(), frame.url))
        except PlaywrightError:
            continue
    # Pages poll (NSE re-requests its report lists every few seconds): read
    # each URL once, newest first.
    read: set[str] = set()
    for req in reversed(finished):
        urls.add(req.url)
        if req.url in read or req.resource_type in SKIP_BODY_TYPES:
            continue
        read.add(req.url)
        try:
            resp = req.response()
        except PlaywrightError:
            continue
        if resp is None or resp.status != 200:
            continue
        headers = resp.headers
        ctype = headers.get("content-type", "").lower()
        if _content_length(headers) > TEXT_BODY_LIMIT:
            continue
        if not any(t in ctype for t in ("json", "html", "javascript", "text", "xml")):
            continue
        try:
            body = resp.text()
        except (PlaywrightError, UnicodeDecodeError):
            continue
        if "json" in ctype:
            try:
                _urls_in_json(json.loads(body), resp.url, urls)
                continue
            except ValueError:
                pass
        urls.update(_urls_in_text(body, resp.url))
    return {u for u in urls if u.startswith(("http://", "https://"))}


def _settle(page: Page, idle_ms: int = 15_000) -> None:
    """Let the page's own XHRs finish; dynamic pages may never go fully idle."""
    try:
        page.wait_for_load_state("networkidle", timeout=idle_ms)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1_500)


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


def _read_download(dl: Download) -> tuple[bytes, str] | None:
    failure = dl.failure()  # waits for the download to finish
    if failure:
        print(f"    browser download {dl.suggested_filename} failed: {failure}")
        return None
    data = Path(dl.path()).read_bytes()
    print(f"    browser download {dl.suggested_filename} ({len(data):,} bytes)")
    return data, _safe_name(dl.suggested_filename)


def _download_in_tab(context: BrowserContext, url: str, referer: str, fallback_name: str) -> tuple[bytes, str] | None:
    """Open url in a real tab: the browser's own network stack gets past
    checks a plain HTTP client fails. Handles both a download and an inline body."""
    tab = context.new_page()
    downloads: list[Download] = []
    tab.on("download", lambda d: downloads.append(d))
    try:
        try:
            resp = tab.goto(url, referer=referer)
        except PlaywrightError:
            resp = None  # a download aborts the navigation ("Download is starting")
        if not downloads and resp is not None and resp.ok:
            body = resp.body()
            if not _looks_like_html(body):
                print(f"    browser tab {_short(url)} -> {resp.status} ({len(body):,} bytes)")
                return body, _content_disposition_name(resp.headers) or fallback_name
        if not downloads:
            try:
                downloads.append(tab.wait_for_event("download", timeout=15_000))
            except PlaywrightError:
                print(f"    browser tab {_short(url)} -> no file")
                return None
        return _read_download(downloads[0])
    finally:
        tab.close()


def _download(context: BrowserContext, url: str, referer: str) -> tuple[bytes, str] | None:
    """(body, file name) for url: an HTTP request carrying the page's cookies
    first, then a browser tab if the request is refused or answered with HTML."""
    fallback_name = _safe_name(urlsplit(url).path)
    try:
        resp = context.request.get(url, headers={"Referer": referer}, timeout=DOWNLOAD_TIMEOUT_MS)
        body = resp.body() if resp.ok else b""
        if resp.ok and not _looks_like_html(body):
            print(f"    GET {_short(url)} -> {resp.status} ({len(body):,} bytes)")
            return body, _content_disposition_name(resp.headers) or fallback_name
        why = "an HTML page" if resp.ok else f"HTTP {resp.status}"
        print(f"    GET {_short(url)} -> {why}; retrying in a browser tab")
    except PlaywrightError as exc:
        print(f"    GET {_short(url)} -> {_first_line(exc)}; retrying in a browser tab")
    return _download_in_tab(context, url, referer, fallback_name)


def _click_downloads(page: Page) -> list[tuple[bytes, str, str]]:
    """Click the page's SPAN/download controls (SPAN-looking ones first) and
    return (body, file name, source) for everything the browser downloads."""
    origin = page.url
    try:
        labels = page.eval_on_selector_all(CLICKABLE, CLICKABLE_LABELS_JS)
    except PlaywrightError:
        return []
    picks = [i for i, label in enumerate(labels) if LIKELY_SPAN_RE.search(label)]
    picks += [i for i, label in enumerate(labels) if i not in picks and DOWNLOAD_TEXT_RE.search(label)]
    out = []
    for i in picks[:MAX_CLICKS]:
        if page.url != origin:  # a postback or a link navigated away
            try:
                page.goto(origin, wait_until="domcontentloaded")
                _settle(page, 5_000)
            except PlaywrightError:
                break
        label = " ".join(labels[i].split())[:60]
        element = page.locator(CLICKABLE).nth(i)
        try:
            if not element.is_visible():
                continue
            with page.expect_download(timeout=20_000) as info:
                element.click(timeout=5_000)
            got = _read_download(info.value)
        except PlaywrightError:
            print(f"    click '{label}' -> no download")
            continue
        if got:
            out.append((got[0], got[1], f"click:{label}"))
    return out


# ---------------------------------------------------------------------------
# Per exchange
# ---------------------------------------------------------------------------


def _dump_debug(page: Page, debug_dir: Path, exchange: str, urls: set[str]) -> None:
    """Save the page as the script saw it, so a failing exchange can be wired up."""
    base = debug_dir / f"{exchange}-{datetime.now(IST):%Y%m%d-%H%M%S}"
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        Path(f"{base}-links.txt").write_text("".join(f"{u}\n" for u in sorted(urls)), encoding="utf-8")
        Path(f"{base}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=f"{base}.png", full_page=True)
    except (PlaywrightError, OSError) as exc:
        print(f"[{exchange.upper()}] could not save the debug files: {_first_line(exc)}")
        return
    print(f"[{exchange.upper()}] page saved for inspection: {base}.png / .html / -links.txt")


def _scrape_exchange(
    context: BrowserContext,
    exchange: str,
    day_str: str,
    dest: Path,
    *,
    list_only: bool,
    intraday_only: bool,
    debug_dir: Path,
    flat: bool = False,
    prune: bool = False,
) -> bool:
    tag = f"[{exchange.upper()}]"
    out_dir = dest if flat else dest / day_str / exchange.upper()

    def done(kept: Path) -> bool:
        # Prune only after a valid current file is on disk -- a failed run deletes nothing.
        if prune:
            _prune_older(out_dir, exchange, kept, tag)
        return True

    page = context.new_page()
    finished: list[Request] = []
    page.on("requestfinished", lambda r: finished.append(r))

    warmup = WARMUP_PAGES.get(exchange)
    if warmup:
        try:
            page.goto(warmup, wait_until="domcontentloaded")
            _settle(page, 5_000)
        except PlaywrightError as exc:
            print(f"{tag} warm-up {warmup} failed (continuing): {_first_line(exc)}")

    page_url = PAGES[exchange]
    print(f"{tag} opening {page_url}")
    try:
        page.goto(page_url, wait_until="domcontentloaded")
    except PlaywrightError as exc:
        print(f"{tag} FAILED: could not open the page: {_first_line(exc)}")
        _dump_debug(page, debug_dir, exchange, set())
        return False
    _settle(page)

    urls = _harvest(page, finished)
    cands, seen = _candidates(exchange, urls, day_str, intraday_only)
    print(f"{tag} {len(urls)} links found; SPAN files for {day_str}: {len(cands)}")
    for c in cands:
        more = f" (+{len(c.urls) - 1} more URLs)" if len(c.urls) > 1 else ""
        print(f"{tag}   {c.name}  <- {_short(c.urls[0])}{more}")
    if not cands and seen:
        print(f"{tag} SPAN files listed for other days: {', '.join(sorted(seen)[-8:])}")

    if list_only:
        if not cands:
            _dump_debug(page, debug_dir, exchange, urls)
        return bool(cands)

    for cand in cands:
        have = _existing(out_dir, cand, exchange, day_str, intraday_only)
        if have:
            print(f"{tag} already have {have}")
            return done(have)
        for url in cand.urls:
            got = _download(context, url, page.url)
            saved = got and _keep_valid(_unpack(got[0], got[1], url, exchange), day_str, intraday_only, out_dir, tag)
            if saved:
                return done(saved)
        print(f"{tag} no valid copy of {cand.name}; trying the next older file")

    if not cands:
        print(f"{tag} no direct link for {day_str}; clicking the page's download controls")
        found = [
            f
            for blob, name, source in _click_downloads(page)
            for f in _unpack(blob, name, source, exchange)
        ]
        saved = _keep_valid(found, day_str, intraday_only, out_dir, tag)
        if saved:
            return done(saved)

    print(f"{tag} FAILED: no valid SPAN file for {day_str}")
    _dump_debug(page, debug_dir, exchange, urls)
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _user_agent(browser: Browser) -> str:
    """The desktop Chrome UA, carrying the real Chromium major version (a
    headless UA says 'HeadlessChrome', which the exchange sites refuse)."""
    major = browser.version.split(".", 1)[0]
    return USER_AGENT.replace("Chrome/148.0.0.0", f"Chrome/{major}.0.0.0")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download the latest NSE/BSE/MCX SPAN risk-parameter file.")
    p.add_argument("--exchange", default="nse,bse,mcx", help="comma list of nse,bse,mcx (default: all three)")
    p.add_argument(
        "--date",
        help="trading day the file is for (default: today in IST); "
        "YYYY-MM-DD, YYYYMMDD, DD-MM-YYYY or DD/MM/YYYY",
    )
    p.add_argument("--dest", default=str(DEFAULT_DEST), help="output root (default: span_files/ next to this script)")
    p.add_argument("--list", action="store_true", help="only show the SPAN files the pages publish; download nothing")
    p.add_argument(
        "--flat",
        action="store_true",
        help="write files straight into --dest (no <date>/<EXCHANGE>/ folders), e.g. a SPAN loader's input dir",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="after a valid file is saved (or already there), delete that exchange's older .spn files "
        "from the same folder; a failed exchange deletes nothing",
    )
    p.add_argument(
        "--include-eod",
        action="store_true",
        help="also take NSE begin/end-of-day (.b/.s) and MCX end-of-day (-E) files; "
        "by default only the intraday files are kept (SPAN margin loaders reject .s / -E)",
    )
    p.add_argument("--headed", action="store_true", help="show the browser window (some sites block headless Chrome)")
    p.add_argument(
        "--channel",
        default="chromium",
        help="browser to drive: chromium (Playwright's, default) or chrome (the installed Google Chrome)",
    )
    p.add_argument("--timeout", type=float, default=60.0, help="page-load timeout in seconds (default: 60)")
    p.add_argument("--debug-dir", help="where a failing page is saved (default: <dest>/_debug)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        day = _target_date(args.date)
        exchanges = _exchange_list(args.exchange)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not exchanges:
        print("error: --exchange is empty", file=sys.stderr)
        return 2
    if sync_playwright is None:
        print(
            "error: playwright is not installed. Run:\n  pip install playwright\n  playwright install chromium",
            file=sys.stderr,
        )
        return 2

    day_str = day.strftime("%Y%m%d")
    dest = Path(args.dest).expanduser().resolve()
    # --flat targets someone else's directory (e.g. a loader's input dir): keep
    # debug dumps out of it.
    default_debug = (DEFAULT_DEST if args.flat else dest) / "_debug"
    debug_dir = Path(args.debug_dir).expanduser().resolve() if args.debug_dir else default_debug
    target = dest if args.flat else dest / day_str
    print(f"SPAN files for {day_str} -> {target}" + ("  (list only)" if args.list else ""))

    results: dict[str, bool] = {}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                # The default "chromium" channel is the full browser in its new headless
                # mode; Playwright's stripped headless shell is refused by NSE
                # (net::ERR_HTTP2_PROTOCOL_ERROR).
                channel=args.channel,
                headless=not args.headed,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as exc:
            print(f"error: could not start Chromium ({_first_line(exc)}). Run: playwright install chromium", file=sys.stderr)
            return 2
        try:
            for exchange in exchanges:
                context = browser.new_context(
                    user_agent=_user_agent(browser),
                    accept_downloads=True,
                    locale="en-IN",
                    timezone_id="Asia/Kolkata",
                    viewport={"width": 1366, "height": 900},
                )
                context.set_default_timeout(args.timeout * 1000)
                try:
                    results[exchange] = _scrape_exchange(
                        context,
                        exchange,
                        day_str,
                        dest,
                        list_only=args.list,
                        flat=args.flat,
                        prune=args.prune,
                        intraday_only=not args.include_eod,
                        debug_dir=debug_dir,
                    )
                except PlaywrightError as exc:
                    print(f"[{exchange.upper()}] FAILED: {_first_line(exc)}")
                    results[exchange] = False
                finally:
                    context.close()
        finally:
            browser.close()

    print("\nsummary: " + "  ".join(f"{ex.upper()}={'ok' if ok else 'FAILED'}" for ex, ok in results.items()))
    return 0 if all(results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
