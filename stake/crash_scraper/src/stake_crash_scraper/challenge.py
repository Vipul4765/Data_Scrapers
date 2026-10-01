from __future__ import annotations

from typing import Iterable

from playwright.async_api import Page

CHALLENGE_TEXT_MARKERS = (
    "verify you are human",
    "checking your browser",
    "security verification",
    "complete the security check",
    "captcha",
    "are you a human",
)

CHALLENGE_URL_MARKERS = (
    "/cdn-cgi/challenge-platform/",
    "challenges.cloudflare.com",
)

CHALLENGE_SELECTORS = (
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[title*="challenge" i]',
    '.cf-turnstile',
    '[data-sitekey]',
    'input[name="cf-turnstile-response"]',
)


def contains_challenge_marker(values: Iterable[str]) -> bool:
    haystack = "\n".join(value.lower() for value in values if value)
    return any(marker in haystack for marker in (*CHALLENGE_TEXT_MARKERS, *CHALLENGE_URL_MARKERS))


async def challenge_visible(page: Page) -> bool:
    values: list[str] = [page.url]

    try:
        values.append(await page.title())
    except Exception:
        pass

    try:
        body_text = await page.locator("body").inner_text(timeout=2_000)
        values.append(body_text[:5_000])
    except Exception:
        pass

    if contains_challenge_marker(values):
        return True

    for selector in CHALLENGE_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            continue

    return False
