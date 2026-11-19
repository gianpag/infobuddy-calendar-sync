import calendar_accounts


def _use_tmp_path(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")


def test_load_accounts_returns_empty_dict_when_file_missing(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert calendar_accounts.load_accounts() == {}


def test_load_accounts_returns_empty_dict_on_corrupt_json(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.ACCOUNTS_PATH.write_text("not json", encoding="utf-8")
    assert calendar_accounts.load_accounts() == {}


def test_add_account_is_idempotent_and_preserves_existing_calendars(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.add_account("a@example.com")
    calendar_accounts.set_selected_calendars("a@example.com", ["primary"])

    calendar_accounts.add_account("a@example.com")  # re-link -- must not reset selection

    assert calendar_accounts.selected_calendars("a@example.com") == ["primary"]


def test_remove_account_deletes_entry(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.add_account("a@example.com")
    calendar_accounts.remove_account("a@example.com")
    assert calendar_accounts.list_account_emails() == []


def test_remove_account_on_unknown_email_does_not_raise(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.remove_account("nope@example.com")  # must not raise


def test_set_selected_calendars_creates_account_entry_if_missing(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.set_selected_calendars("a@example.com", ["cal1", "cal2"])
    assert calendar_accounts.selected_calendars("a@example.com") == ["cal1", "cal2"]


def test_list_account_emails_is_sorted(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    calendar_accounts.add_account("z@example.com")
    calendar_accounts.add_account("a@example.com")
    assert calendar_accounts.list_account_emails() == ["a@example.com", "z@example.com"]


def test_selected_calendars_returns_empty_list_for_unknown_account(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert calendar_accounts.selected_calendars("nope@example.com") == []
