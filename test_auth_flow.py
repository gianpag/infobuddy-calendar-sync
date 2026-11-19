import asyncio

import pytest

import infomentor_extract as im
from infomentor_extract import BASE_URL, _login_with_credentials, _start_login_flow, _wait_for_authentication

_real_sleep = asyncio.sleep


class FakePage:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def goto(self, url, wait_until=None):
        self.calls.append((url, wait_until))
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_login_flow_tolerates_infomentor_redirect_race():
    page = FakePage(Exception('Page.goto: Navigation is interrupted by another navigation'))
    await _start_login_flow(page)
    assert page.calls[0][1] == 'commit'


@pytest.mark.asyncio
async def test_login_flow_raises_unrelated_navigation_error():
    page = FakePage(Exception('Page.goto: DNS failure'))
    with pytest.raises(Exception, match='DNS failure'):
        await _start_login_flow(page)


class FakeLoginPage:
    """Fake Playwright page for testing the credential login form, no real network access."""

    def __init__(self, url_sequence, click_error=None):
        self._url_sequence = list(url_sequence)
        self.url = self._url_sequence.pop(0)
        self.filled = {}
        self.clicked = []
        self._click_error = click_error

    async def goto(self, url, wait_until=None):
        if self._url_sequence:
            self.url = self._url_sequence.pop(0)

    async def wait_for_load_state(self, state=None, timeout=None):
        pass

    async def query_selector(self, selector):
        return object()  # the login form is present on this fake's login page

    async def content(self):
        return ""

    async def fill(self, selector, value):
        self.filled[selector] = value

    async def click(self, selector):
        self.clicked.append(selector)
        if self._click_error:
            raise self._click_error
        if self._url_sequence:
            self.url = self._url_sequence.pop(0)


@pytest.mark.asyncio
async def test_login_with_credentials_fills_form_and_submits():
    # Never touches BankID/SSO: only the two visible username/password fields InfoMentor's
    # own login page exposes, confirmed against the real login page's form field IDs.
    page = FakeLoginPage([BASE_URL])
    await _login_with_credentials(page, "alice", "s3cret")
    assert page.filled["#login_ascx_txtNotandanafn"] == "alice"
    assert page.filled["#login_ascx_txtLykilord"] == "s3cret"
    assert page.clicked == ["#login_ascx_btnLogin"]


@pytest.mark.asyncio
async def test_login_with_credentials_tolerates_redirect_race():
    page = FakeLoginPage([BASE_URL], click_error=Exception("Page.click: net::ERR_ABORTED"))
    await _login_with_credentials(page, "alice", "s3cret")  # must not raise


@pytest.mark.asyncio
async def test_login_with_credentials_raises_unrelated_error():
    page = FakeLoginPage([BASE_URL], click_error=Exception("Page.click: DNS failure"))
    with pytest.raises(Exception, match="DNS failure"):
        await _login_with_credentials(page, "alice", "s3cret")


@pytest.mark.asyncio
async def test_wait_for_authentication_times_out_without_hanging(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda _: _real_sleep(0))

    async def never_authenticated(page):
        return False

    monkeypatch.setattr(im, "is_authenticated", never_authenticated)
    page = FakeLoginPage([BASE_URL] * 5)
    result = await _wait_for_authentication(page, timeout_seconds=0.05)
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_authentication_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda _: _real_sleep(0))

    async def already_authenticated(page):
        return True

    monkeypatch.setattr(im, "is_authenticated", already_authenticated)
    page = FakeLoginPage([BASE_URL, BASE_URL])
    result = await _wait_for_authentication(page, timeout_seconds=5)
    assert result is True


def test_load_credentials_returns_none_without_username():
    assert im.load_credentials(None) is None
    assert im.load_credentials("") is None


def test_load_credentials_raises_when_not_stored(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, user: None)
    with pytest.raises(SystemExit):
        im.load_credentials("alice")


def test_load_credentials_returns_stored_password(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, user: "s3cret")
    assert im.load_credentials("alice") == ("alice", "s3cret")
