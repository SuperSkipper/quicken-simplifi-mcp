import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from simplifi_mcp import server

QUICKEN = "https://services.quicken.com"


@pytest.fixture(autouse=True)
def fresh_server(monkeypatch, client):
    """Point the server at the throwaway-env-file client and reset its
    module-level login state for every test."""
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setattr(server, "_authenticated", False)
    monkeypatch.setattr(server, "_dataset_id", None)
    monkeypatch.setattr(server, "_cache", {})


@pytest.fixture
def valid_token(requests_mock):
    requests_mock.get(f"{QUICKEN}/userprofiles/me", json={"id": "user-1"})
    requests_mock.get(f"{QUICKEN}/datasets", json={
        "resources": [{"id": "ds-1"}], "metaData": {}})


def test_session_raises_when_no_token_is_cached():
    with pytest.raises(ToolError, match="Call the login tool"):
        server._session()


def test_session_raises_when_cached_token_is_rejected(write_env, requests_mock):
    write_env(token="expired")
    requests_mock.get(f"{QUICKEN}/userprofiles/me", status_code=401, json={})

    with pytest.raises(ToolError, match="Call the login tool"):
        server._session()


def test_session_uses_cached_token_and_first_dataset(write_env, valid_token, requests_mock):
    write_env(token="cached")

    client, dataset_id = server._session()

    assert dataset_id == "ds-1"
    assert client.session.headers["Authorization"] == "Bearer cached"


def test_login_without_mfa_saves_the_token(client, valid_token, requests_mock):
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"code": "auth-code"})
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "new-token"})

    assert server.login(email="a@b.com", password="secret") == "Logged in to Simplifi."
    assert client.load_token() == "new-token"


def test_login_with_mfa_then_submit_mfa_code(client, valid_token, requests_mock):
    requests_mock.post(
        f"{QUICKEN}/oauth/authorize",
        [
            {"json": {"status": "MFA code sent", "mfaChannel": "email"}},
            {"json": {"status": "User passed MFA", "code": "auth-code"}},
        ],
    )
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "new-token"})

    message = server.login(email="a@b.com", password="secret")
    assert "via email" in message and "submit_mfa_code" in message
    assert client.load_token() is None

    assert server.submit_mfa_code("123456") == "Logged in to Simplifi."
    assert client.load_token() == "new-token"


def test_login_failure_becomes_a_tool_error(requests_mock):
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"status": "Invalid credentials"})

    with pytest.raises(ToolError, match="Invalid credentials"):
        server.login(email="a@b.com", password="wrong")


def test_submit_mfa_code_without_login_is_a_tool_error():
    with pytest.raises(ToolError, match="No login in progress"):
        server.submit_mfa_code("123456")


def test_logout_clears_token_and_session_state(client, token_env_file, write_env, valid_token):
    write_env(token="cached")
    server._session()

    server.logout()

    assert json.loads(token_env_file.read_text()) == {"local": {}}
    assert "Authorization" not in client.session.headers
    with pytest.raises(ToolError):
        server._session()


def test_auth_status_reports_logged_in_and_out(write_env, valid_token):
    assert "Not logged in" in server.auth_status()

    write_env(token="cached")

    assert server.auth_status() == "Logged in to Simplifi (dataset ds-1)."


# --- search_transactions / retrieve_all ---------------------------------------

def _page(resources):
    return {"resources": resources, "metaData": {}}


TRANSACTIONS = [
    {"id": "t1", "postedOn": "2026-08-10", "payee": "Sam's Club", "amount": -58.9,
     "accountId": "acc-chk", "coa": {"type": "CATEGORY", "id": "cat-groceries"},
     "state": "CLEARED", "source": "QCS_REFRESH"},
    {"id": "t2", "postedOn": "2026-08-15", "payee": "ACME Payroll", "amount": 2500.0,
     "accountId": "acc-chk", "coa": {"type": "CATEGORY", "id": "cat-income"},
     "state": "CLEARED", "source": "QCS_REFRESH", "memo": "August salary"},
    {"id": "t3", "postedOn": "2026-08-20", "payee": "Card payment", "amount": -400.0,
     "accountId": "acc-chk", "coa": {"type": "ACCOUNT", "id": "acc-card"},
     "state": "CLEARED", "source": "QCS_REFRESH"},
    {"id": "t4", "postedOn": "2026-09-01", "payee": "Corner Shop", "amount": -12.5,
     "accountId": "acc-card", "coa": {"type": "UNCATEGORIZED", "id": "0"},
     "state": "PENDING", "source": "QCS_REFRESH_BANK_PENDING"},
    {"id": "t5", "postedOn": "2026-11-01", "payee": "Rent", "amount": -1800.0,
     "accountId": "acc-chk", "coa": {"type": "CATEGORY", "id": "cat-rent"},
     "state": "PENDING", "source": "SCHEDULED_TRANSACTION"},
]


@pytest.fixture
def logged_in(write_env, valid_token, requests_mock):
    write_env(token="cached")
    requests_mock.get(f"{QUICKEN}/accounts", json=_page([
        {"id": "acc-chk", "name": "Everyday Checking"},
        {"id": "acc-card", "name": "Rewards Visa"},
    ]))
    requests_mock.get(f"{QUICKEN}/categories", json=_page([
        {"id": "cat-food", "name": "Food & Dining"},
        {"id": "cat-groceries", "name": "Groceries", "parentId": "cat-food"},
        {"id": "cat-income", "name": "Paycheck"},
        {"id": "cat-rent", "name": "Rent"},
    ]))
    return requests_mock.get(f"{QUICKEN}/transactions", json=_page(TRANSACTIONS))


def _ids(result):
    return [t["id"] for t in result["transactions"]]


def test_search_returns_trimmed_named_transactions_newest_first(logged_in):
    result = server.search_transactions()

    assert _ids(result) == ["t4", "t3", "t2", "t1"]
    by_id = {t["id"]: t for t in result["transactions"]}
    assert by_id["t1"] == {
        "id": "t1", "date": "2026-08-10", "payee": "Sam's Club", "amount": -58.9,
        "account": "Everyday Checking", "category": "Food & Dining: Groceries",
        "category_id": "cat-groceries", "state": "CLEARED",
    }
    assert by_id["t2"]["memo"] == "August salary"
    assert by_id["t3"]["category"] == "Transfer: Rewards Visa"
    assert by_id["t4"]["category"] == "Uncategorized"


def test_search_excludes_scheduled_transactions_unless_asked(logged_in):
    assert "t5" not in _ids(server.search_transactions())

    result = server.search_transactions(include_scheduled=True)

    assert _ids(result)[0] == "t5"
    assert result["transactions"][0]["scheduled"] is True


@pytest.mark.parametrize("filters, expected", [
    ({"start_date": "2026-08-15", "end_date": "2026-08-20"}, ["t3", "t2"]),
    ({"account": "visa"}, ["t4"]),
    ({"account": "acc-card"}, ["t4"]),
    ({"category": "groceries"}, ["t1"]),
    ({"category": "food & dining"}, ["t1"]),
    ({"category": "cat-income"}, ["t2"]),
    ({"payee_contains": "SAM"}, ["t1"]),
    ({"max_amount": 0}, ["t4", "t3", "t1"]),
    ({"min_amount": -100, "max_amount": 0}, ["t4", "t1"]),
])
def test_search_filters(logged_in, filters, expected):
    assert _ids(server.search_transactions(**filters)) == expected


def test_search_limit_truncates_but_reports_total_matches(logged_in):
    result = server.search_transactions(limit=2)

    assert result["total_matches"] == 4
    assert result["returned"] == 2
    assert _ids(result) == ["t4", "t3"]


def test_search_rejects_a_badly_formatted_date(logged_in):
    with pytest.raises(ToolError, match="start_date must be YYYY-MM-DD"):
        server.search_transactions(start_date="08/10/2026")


def test_retrieve_all_pages_through_transactions(logged_in):
    first = server.retrieve_all(limit=3)
    second = server.retrieve_all(offset=3, limit=3)

    assert (first["total"], first["offset"], first["returned"]) == (4, 0, 3)
    assert _ids(first) == ["t4", "t3", "t2"]
    assert (second["offset"], second["returned"], _ids(second)) == (3, 1, ["t1"])


def test_transactions_are_cached_between_calls_and_cleared_on_logout(logged_in):
    server.search_transactions()
    server.retrieve_all()
    assert logged_in.call_count == 1

    server.logout()
    server._client.save_token("cached")
    server.search_transactions()

    assert logged_in.call_count == 2


@pytest.mark.parametrize("tool", [server.search_transactions, server.retrieve_all])
def test_transaction_tools_require_login(tool):
    with pytest.raises(ToolError, match="Call the login tool"):
        tool()
