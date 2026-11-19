"""OAuth 2.0 Device Authorization Grant (RFC 8628) against Google's endpoints -- lets a
headless/no-browser host obtain a refresh token without a local browser. The
user completes consent on any device's own browser (phone, laptop -- doesn't need to be
on the same machine or network) by visiting a short verification URL and entering a code
this module requests from Google.

Requires a Google Cloud OAuth client of type **"TVs and Limited Input devices"** -- NOT
"Desktop app" or "Web application" (those aren't accepted for this grant type). See
README.md's "Generic Google Calendar sync" section for the one-time Cloud Console setup.
Each deployment brings its own client (CLAUDE.md "Distributable design"), same
never-commit convention as google_calendar_sync.py's existing OAuth client -- this is
just read for its client_id/client_secret rather than used with InstalledAppFlow.

Plain stdlib urllib -- no new dependency for a two-endpoint protocol this small.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"


class DeviceFlowError(Exception):
    """A terminal (non-retryable) device-flow failure: expired_token, access_denied, or
    any other error the token endpoint reports that isn't authorization_pending/
    slow_down."""


@dataclass
class DeviceCodeResponse:
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


def _post_form(url: str, fields: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Google's device/token endpoints put the error detail in the body even on a
        # non-2xx status (e.g. "authorization_pending" is a 428) -- read it rather than
        # just raising the HTTPError, since callers need to distinguish "still pending"
        # from a real failure.
        return json.loads(exc.read().decode("utf-8"))


def request_device_code(client_id: str, scopes: list[str]) -> DeviceCodeResponse:
    """Step 1: ask Google for a device_code + user_code to show the user."""
    body = _post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": " ".join(scopes)})
    if "error" in body:
        raise DeviceFlowError(f"Device code request failed: {body}")
    # Google's actual implementation returns `verification_url`; the RFC spells it
    # `verification_uri` -- accept either so this doesn't break if Google ever aligns.
    verification_url = body.get("verification_url") or body.get("verification_uri")
    return DeviceCodeResponse(
        device_code=body["device_code"],
        user_code=body["user_code"],
        verification_url=verification_url,
        expires_in=body["expires_in"],
        interval=body.get("interval", 5),
    )


def poll_for_token(client_id: str, client_secret: str, device_code: str) -> dict[str, Any] | None:
    """One poll attempt against the token endpoint.

    Returns the token response dict (access_token/refresh_token/...) once the user has
    completed consent, or None if authorization is still pending (caller should wait and
    poll again, respecting `slow_down` by increasing its own interval -- see
    DeviceCodeResponse.interval and the caller-side backoff in calendar_link_ui.py).
    Raises DeviceFlowError for any other terminal error (access_denied, expired_token,
    ...).
    """
    body = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "device_code": device_code,
        "grant_type": GRANT_TYPE,
    })
    error = body.get("error")
    if error is None:
        return body
    if error == "authorization_pending":
        return None
    if error == "slow_down":
        return {"_slow_down": True}
    raise DeviceFlowError(f"Device token request failed: {error}")


def wait_for_authorization(
    client_id: str,
    client_secret: str,
    device: DeviceCodeResponse,
    *,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    """Blocking convenience wrapper: polls until authorized or the device code expires.

    Mainly useful for a CLI/script context. calendar_link_ui.py's web UI instead calls
    poll_for_token() directly from its own request-driven loop, so it can report live
    status to the browser instead of blocking a request thread for minutes.
    """
    interval = device.interval
    elapsed = 0.0
    while elapsed < device.expires_in:
        result = poll_for_token(client_id, client_secret, device.device_code)
        if result is None:
            sleep_fn(interval)
            elapsed += interval
            continue
        if result.get("_slow_down"):
            interval += 5
            sleep_fn(interval)
            elapsed += interval
            continue
        return result
    raise DeviceFlowError("Device code expired before the user completed authorization")
