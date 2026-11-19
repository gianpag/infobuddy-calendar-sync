import json
from unittest.mock import patch

import pytest

from google_device_flow import (
    DeviceCodeResponse,
    DeviceFlowError,
    poll_for_token,
    request_device_code,
    wait_for_authorization,
)


def _fake_post_form_sequence(responses):
    """Returns a callable to patch google_device_flow._post_form with, yielding each of
    `responses` in turn on successive calls."""
    it = iter(responses)

    def _fake(url, fields):
        return next(it)

    return _fake


def test_request_device_code_parses_response():
    response = {
        "device_code": "devcode123",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "expires_in": 1800,
        "interval": 5,
    }
    with patch("google_device_flow._post_form", return_value=response):
        device = request_device_code("client-id", ["scope-a", "scope-b"])

    assert device == DeviceCodeResponse(
        device_code="devcode123",
        user_code="ABCD-EFGH",
        verification_url="https://www.google.com/device",
        expires_in=1800,
        interval=5,
    )


def test_request_device_code_accepts_verification_uri_spelling():
    # RFC 8628 spells it verification_uri; Google's real implementation returns
    # verification_url -- accept either.
    response = {
        "device_code": "d", "user_code": "u",
        "verification_uri": "https://www.google.com/device",
        "expires_in": 1800, "interval": 5,
    }
    with patch("google_device_flow._post_form", return_value=response):
        device = request_device_code("client-id", ["scope"])
    assert device.verification_url == "https://www.google.com/device"


def test_request_device_code_raises_on_error():
    with patch("google_device_flow._post_form", return_value={"error": "invalid_client"}):
        with pytest.raises(DeviceFlowError):
            request_device_code("bad-client", ["scope"])


def test_poll_for_token_returns_none_when_pending():
    with patch("google_device_flow._post_form", return_value={"error": "authorization_pending"}):
        assert poll_for_token("cid", "csecret", "devcode") is None


def test_poll_for_token_signals_slow_down():
    with patch("google_device_flow._post_form", return_value={"error": "slow_down"}):
        result = poll_for_token("cid", "csecret", "devcode")
    assert result == {"_slow_down": True}


def test_poll_for_token_raises_on_terminal_error():
    with patch("google_device_flow._post_form", return_value={"error": "access_denied"}):
        with pytest.raises(DeviceFlowError):
            poll_for_token("cid", "csecret", "devcode")


def test_poll_for_token_returns_tokens_on_success():
    response = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
    with patch("google_device_flow._post_form", return_value=response):
        result = poll_for_token("cid", "csecret", "devcode")
    assert result == response


def test_wait_for_authorization_retries_until_success():
    device = DeviceCodeResponse("devcode", "ABCD", "https://x", expires_in=60, interval=1)
    sleeps = []

    with patch(
        "google_device_flow.poll_for_token",
        side_effect=[None, None, {"access_token": "at", "refresh_token": "rt"}],
    ):
        result = wait_for_authorization("cid", "csecret", device, sleep_fn=sleeps.append)

    assert result == {"access_token": "at", "refresh_token": "rt"}
    assert sleeps == [1, 1]  # two pending polls before success, each waiting `interval`


def test_wait_for_authorization_backs_off_on_slow_down():
    device = DeviceCodeResponse("devcode", "ABCD", "https://x", expires_in=60, interval=1)
    sleeps = []

    with patch(
        "google_device_flow.poll_for_token",
        side_effect=[{"_slow_down": True}, {"access_token": "at"}],
    ):
        wait_for_authorization("cid", "csecret", device, sleep_fn=sleeps.append)

    assert sleeps == [6]  # interval bumped by 5 (1 -> 6) before the next poll


def test_wait_for_authorization_raises_on_expiry():
    device = DeviceCodeResponse("devcode", "ABCD", "https://x", expires_in=2, interval=1)

    with patch("google_device_flow.poll_for_token", return_value=None):
        with pytest.raises(DeviceFlowError):
            wait_for_authorization("cid", "csecret", device, sleep_fn=lambda _: None)


def test_post_form_reads_body_on_http_error(monkeypatch):
    import urllib.error

    import google_device_flow as gdf

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self, body):
            super().__init__("url", 428, "msg", {}, None)
            self._body = body

        def read(self):
            return self._body

    def fake_urlopen(req, timeout=None):
        raise FakeHTTPError(json.dumps({"error": "authorization_pending"}).encode())

    monkeypatch.setattr(gdf.urllib.request, "urlopen", fake_urlopen)
    result = gdf._post_form("https://x", {"a": "b"})
    assert result == {"error": "authorization_pending"}
