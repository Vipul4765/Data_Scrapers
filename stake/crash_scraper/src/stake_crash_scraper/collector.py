from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from .challenge import challenge_visible
from .config import CollectorSettings
from .parser import normalize_payload, parse_json_frame, safe_shape
from .round_state import RoundState
from .storage import DatasetWriter, load_or_create_player_salt

log = logging.getLogger(__name__)
_PLAYING_RE = re.compile(r"(?P<count>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmM]?)\s+Playing\b")
_TRACKED_HTTP_TYPES = {"document", "xhr", "fetch"}


def _parse_compact_count(text: str) -> int | None:
    match = _PLAYING_RE.search(text)
    if not match:
        return None
    value = float(match.group("count"))
    suffix = match.group("suffix").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


async def _probe_online_count(page: Page) -> int | None:
    try:
        text = await page.locator("body").inner_text(timeout=3_000)
    except Exception:
        return None
    return _parse_compact_count(text)


async def collect(settings: CollectorSettings) -> None:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.profile_dir.mkdir(parents=True, exist_ok=True)

    player_salt = load_or_create_player_salt(settings.output_dir)
    writer = DatasetWriter(settings.output_dir)
    writer.open()

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=settings.queue_size)
    stop = asyncio.Event()
    collection_enabled = asyncio.Event()
    collection_enabled.set()

    round_state = RoundState()
    consecutive_403 = 0

    async def worker() -> None:
        while not stop.is_set() or not queue.empty():
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if not collection_enabled.is_set():
                continue

            payload = parse_json_frame(frame)
            if payload is None:
                continue

            if settings.debug_shapes:
                shape = safe_shape(payload)
                encoded = json.dumps(shape, ensure_ascii=False)
                if any(word in encoded.lower() for word in ("crash", "bet", "cash", "wager", "player", "round")):
                    print(encoded[:6_000])

            for event in normalize_payload(payload, player_salt=player_salt):
                writer.write_event(event)
                bet_row, round_row = round_state.ingest(event)
                if bet_row:
                    writer.write_bet(bet_row)
                if round_row:
                    writer.write_round(round_row)
                    log.info(
                        "round=%s crash=%.4fx players=%s bets=%s online=%s",
                        round_row.get("round_id") or round_row["local_round_uid"][:10],
                        float(round_row["crashpoint"]),
                        round_row.get("reported_player_count") or round_row.get("observed_unique_players"),
                        round_row.get("reported_bet_count") or round_row.get("observed_unique_bets"),
                        round_row.get("online_count"),
                    )

    async def online_probe(page: Page) -> None:
        while not stop.is_set():
            if collection_enabled.is_set():
                count = await _probe_online_count(page)
                if count is not None:
                    round_state.update_online_count(count)
            await asyncio.sleep(settings.online_count_interval_seconds)

    async def challenge_watcher(page: Page) -> None:
        challenge_active = False

        while not stop.is_set():
            visible = await challenge_visible(page)

            if visible:
                collection_enabled.clear()
                if not challenge_active:
                    challenge_active = True
                    log.warning("Browser security/CAPTCHA challenge detected; pausing collection")
                    print(
                        "\nSecurity/CAPTCHA challenge detected. "
                        "Please solve it manually in the browser. "
                        "The collector will resume automatically afterward.\n"
                    )
                    with suppress(Exception):
                        await page.bring_to_front()
            else:
                if challenge_active:
                    challenge_active = False
                    log.info("Challenge cleared; resuming collection")
                    print("\nChallenge cleared. Collection resumed.\n")
                collection_enabled.set()

            await asyncio.sleep(settings.challenge_check_interval_seconds)

    async with async_playwright() as pw:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=str(settings.profile_dir),
            headless=settings.headless,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_websocket(ws: Any) -> None:
            def on_frame(frame: Any) -> None:
                if not collection_enabled.is_set():
                    return
                try:
                    queue.put_nowait(frame)
                except asyncio.QueueFull:
                    log.warning("Frame queue full; dropping a frame rather than blocking browser events")
            ws.on("framereceived", on_frame)

        def on_response(response: Any) -> None:
            nonlocal consecutive_403

            resource_type = getattr(response.request, "resource_type", "")
            if resource_type not in _TRACKED_HTTP_TYPES:
                return

            if response.status == 429:
                log.error("HTTP 429 received; stopping instead of retrying aggressively")
                stop.set()
                return

            if response.status == 403:
                if not collection_enabled.is_set():
                    log.info("HTTP 403 observed while manual security challenge is active")
                    return

                consecutive_403 += 1
                log.warning("HTTP 403 observed (%d/%d)", consecutive_403, settings.max_consecutive_403)
                if consecutive_403 >= settings.max_consecutive_403:
                    log.error("Repeated 403 responses; stopping collector")
                    stop.set()
            elif response.status < 400:
                consecutive_403 = 0

        page.on("websocket", on_websocket)
        page.on("response", on_response)

        worker_task = asyncio.create_task(worker())
        online_task = asyncio.create_task(online_probe(page))
        challenge_task = asyncio.create_task(challenge_watcher(page))

        try:
            log.info("Opening %s", settings.url)
            try:
                await page.goto(settings.url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                log.warning("Initial navigation did not fully complete: %s", exc)

            print("Browser opened. Log in manually if needed and keep the Crash page open.")
            print("If a security/CAPTCHA challenge appears, solve it manually in this browser.")
            print("Read-only collector running. Press Ctrl+C to stop.")

            while not stop.is_set():
                if not context.pages:
                    break
                await asyncio.sleep(1)
        finally:
            stop.set()
            for task in (challenge_task, online_task):
                task.cancel()
            for task in (challenge_task, online_task):
                with suppress(asyncio.CancelledError):
                    await task
            await worker_task
            await context.close()
            writer.close()
