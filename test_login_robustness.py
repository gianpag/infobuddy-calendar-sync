import json
import sys

import pytest

import infomentor_extract as im
import run_pipeline
from infomentor_extract import BASE_URL, LOGIN_URL, ManualLoginRequired, _login_with_credentials, ensure_authenticated
from sync_status import EXIT_MANUAL_LOGIN_REQUIRED, describe_status, read_status

RACE = Exception('Page.goto: Navigation to "x" is interrupted by another navigation to "https://infomentor.se/swedish/production/mentor/"')


class Page:
    """Mocked Playwright page. `script` is a list of (goto_error, landed_url, has_form, content)
    consumed one per goto()."""

    def __init__(self, script, authenticated=lambda page: False):
        self.script = list(script)
        self.url = "about:blank"
        self.has_form = False
        self._content = ""
        self.gotos = []
        self.load_waits = 0
        self.filled = {}
        self.clicked = []
        self._auth = authenticated

    async def goto(self, url, wait_until=None):
        self.gotos.append(url)
        err, self.url, self.has_form, self._content = self.script.pop(0) if self.script else (None, url, False, "")
        if err:
            raise err

    async def wait_for_load_state(self, state=None, timeout=None):
        self.load_waits += 1

    async def query_selector(self, sel):
        return object() if self.has_form else None

    async def content(self):
        return self._content

    async def fill(self, sel, value):
        self.filled[sel] = value

    async def click(self, sel):
        self.clicked.append(sel)


@pytest.fixture(autouse=True)
def fake_auth(monkeypatch):
    async def is_auth(page):
        return page._auth(page)
    monkeypatch.setattr(im, "is_authenticated", is_auth)


@pytest.mark.asyncio
async def test_interrupted_navigation_settles_then_fills_login_form():
    page = Page([(RACE, LOGIN_URL, True, "")])
    await _login_with_credentials(page, "u", "p")
    assert page.load_waits == 1
    assert page.clicked == ["#login_ascx_btnLogin"]


@pytest.mark.asyncio
async def test_redirect_to_unknown_page_retries_once_then_finds_form():
    page = Page([(RACE, "https://infomentor.se/swedish/production/mentor/", False, "<html/>"),
                 (None, LOGIN_URL, True, "")])
    await _login_with_credentials(page, "u", "p")
    assert len(page.gotos) == 2
    assert page.filled


@pytest.mark.asyncio
async def test_no_form_after_retry_raises_manual_login_required():
    page = Page([(RACE, "https://infomentor.se/x", False, ""), (None, "https://infomentor.se/x", False, "")])
    with pytest.raises(ManualLoginRequired):
        await _login_with_credentials(page, "u", "p")
    assert page.filled == {}


@pytest.mark.asyncio
async def test_bankid_page_is_never_automated():
    page = Page([(None, "https://idp.example.se/saml/bankid", False, "Logga in med BankID")])
    with pytest.raises(ManualLoginRequired, match="BankID"):
        await _login_with_credentials(page, "u", "p")
    assert page.filled == {} and page.clicked == []


@pytest.mark.asyncio
async def test_already_authenticated_after_redirect_skips_form():
    page = Page([(RACE, BASE_URL + "/", False, "")], authenticated=lambda p: p.url.startswith(BASE_URL))
    await _login_with_credentials(page, "u", "p")
    assert page.filled == {}


@pytest.mark.asyncio
async def test_headless_without_credentials_fails_fast_instead_of_waiting():
    page = Page([(None, "https://infomentor.se/login", False, "")])
    with pytest.raises(ManualLoginRequired):
        await ensure_authenticated(page, credentials=None, headless=True)


@pytest.mark.asyncio
async def test_headless_bankid_with_credentials_raises(capsys):
    page = Page([(None, "https://infomentor.se/x", False, ""),
                 (None, "https://idp.example.se/bankid", False, "BankID")])
    with pytest.raises(ManualLoginRequired):
        await ensure_authenticated(page, credentials=("secret-user", "secret-pass"), headless=True)
    out = capsys.readouterr()
    assert "secret-user" not in out.out + out.err and "secret-pass" not in out.out + out.err


def test_main_exits_with_manual_login_code(monkeypatch):
    async def boom(args):
        raise ManualLoginRequired("x")
    monkeypatch.setattr(im, "async_main", boom)
    monkeypatch.setattr(sys, "argv", ["x", "--headless"])
    with pytest.raises(SystemExit) as exc:
        im.main()
    assert exc.value.code == EXIT_MANUAL_LOGIN_REQUIRED


def test_pipeline_records_needs_manual_login_and_skips_downstream(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(run_pipeline, "run_step", lambda cmd: calls.append(cmd) or EXIT_MANUAL_LOGIN_REQUIRED)
    monkeypatch.setattr(run_pipeline, "run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(sys, "argv", ["x", "--headless", "--output-dir", str(tmp_path / "o"), "--state-dir", str(tmp_path / "s")])
    assert run_pipeline.main() == 1
    assert len(calls) == 1
    status = read_status(tmp_path / "s")
    assert status["needs_manual_login"] is True
    assert "manual (BankID) login" in describe_status(status)
