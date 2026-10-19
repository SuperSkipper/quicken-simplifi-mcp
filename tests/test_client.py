import json

import pytest

from simplifi_mcp.client import LoginError, _redact

QUICKEN = "https://services.quicken.com"


# --- _redact -----------------------------------------------------------

def test_redact_keeps_first_and_last_four_characters_of_a_long_token():
    assert _redact("abcdefghij") == "abcd...ghij"


def test_redact_fully_masks_a_short_token():
    assert _redact("short") == "***"


def test_redact_passes_through_falsy_values_unchanged():
    assert _redact(None) is None
    assert _redact("") == ""


# --- load_token / save_token / clear_token ------------------------------

def test_load_token_returns_none_when_env_file_is_missing(client):
    assert client.load_token() is None


def test_load_token_returns_none_when_env_file_is_not_valid_json(client, token_env_file):
    token_env_file.write_text("not json")

    assert client.load_token() is None


def test_save_token_then_load_token_round_trips(client):
    client.save_token("abc123")

    assert client.load_token() == "abc123"


def test_save_token_preserves_other_environments_and_variables(client, token_env_file):
    token_env_file.write_text(json.dumps({
        "local": {"host": QUICKEN},
        "prod": {"token": "prod-token"},
    }))

    client.save_token("new-token")

    saved = json.loads(token_env_file.read_text())
    assert saved["local"] == {"host": QUICKEN, "token": "new-token"}
    assert saved["prod"] == {"token": "prod-token"}


def test_clear_token_removes_only_the_token(client, token_env_file):
    token_env_file.write_text(json.dumps({"local": {"host": QUICKEN, "token": "abc"}}))

    client.clear_token()

    assert json.loads(token_env_file.read_text()) == {"local": {"host": QUICKEN}}


def test_clear_token_is_a_noop_when_no_token_is_cached(client):
    client.clear_token()  # must not raise

    assert client.load_token() is None


# --- start_login / complete_mfa ---------------------------------------------

def test_start_login_logs_in_with_explicit_credentials(client, requests_mock):
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"code": "auth-code"})
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "the-token"})

    result = client.start_login(email="a@b.com", password="secret")

    assert result == {"token": "the-token"}
    authorize_request = requests_mock.request_history[0].json()
    assert authorize_request["username"] == "a@b.com"
    assert authorize_request["password"] == "secret"
    assert requests_mock.request_history[1].json()["code"] == "auth-code"


def test_start_login_falls_back_to_env_file_credentials_when_not_passed(client, write_env, requests_mock):
    write_env(email="env@example.com", password="env-secret")
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"code": "auth-code"})
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "the-token"})

    client.start_login()

    authorize_request = requests_mock.request_history[0].json()
    assert authorize_request["username"] == "env@example.com"
    assert authorize_request["password"] == "env-secret"


def test_start_login_prefers_explicit_credentials_over_env_file(client, write_env, requests_mock):
    write_env(email="env@example.com", password="env-secret")
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"code": "auth-code"})
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "the-token"})

    client.start_login(email="explicit@example.com", password="explicit-secret")

    authorize_request = requests_mock.request_history[0].json()
    assert authorize_request["username"] == "explicit@example.com"
    assert authorize_request["password"] == "explicit-secret"


def test_start_login_raises_when_no_code_is_returned(client, requests_mock):
    requests_mock.post(f"{QUICKEN}/oauth/authorize", json={"status": "Invalid credentials"})

    with pytest.raises(LoginError, match="Invalid credentials"):
        client.start_login(email="a@b.com", password="wrong")


def test_start_login_reports_mfa_then_complete_mfa_returns_the_token(client, requests_mock):
    requests_mock.post(
        f"{QUICKEN}/oauth/authorize",
        [
            {"json": {"status": "MFA code sent", "mfaChannel": "sms"}},
            {"json": {"status": "User passed MFA", "code": "auth-code"}},
        ],
    )
    requests_mock.post(f"{QUICKEN}/oauth/token", json={"accessToken": "the-token"})

    assert client.start_login(email="a@b.com", password="secret") == {
        "mfa_required": True, "mfa_channel": "sms"}
    token = client.complete_mfa("123456")

    assert token == "the-token"
    first, mfa_request = (r.json() for r in requests_mock.request_history[:2])
    assert mfa_request["mfaChannel"] == "sms"
    assert mfa_request["mfaCode"] == "123456"
    assert mfa_request["threatMetrixSessionId"] == first["threatMetrixSessionId"]


def test_complete_mfa_raises_when_mfa_check_fails(client, requests_mock):
    requests_mock.post(
        f"{QUICKEN}/oauth/authorize",
        [
            {"json": {"status": "MFA code sent", "mfaChannel": "sms"}},
            {"json": {"status": "MFA failed"}},
        ],
    )
    client.start_login(email="a@b.com", password="secret")

    with pytest.raises(LoginError, match="MFA failed"):
        client.complete_mfa("wrong-code")


def test_complete_mfa_raises_when_no_login_is_pending(client):
    with pytest.raises(LoginError, match="No login in progress"):
        client.complete_mfa("123456")


# --- verify_token ----------------------------------------------------------

def test_verify_token_returns_true_and_sets_the_session_auth_header(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/userprofiles/me", json={"id": "user-1"})

    assert client.verify_token("the-token") is True
    assert client.session.headers["Authorization"] == "Bearer the-token"


def test_verify_token_returns_false_on_a_non_200_response(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/userprofiles/me", status_code=401, json={"error": "invalid"})

    assert client.verify_token("bad-token") is False


# --- _unpaginate -------------------------------------------------------------

def test_unpaginate_follows_next_link_until_exhausted(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/things",
        json={"resources": [{"id": 1}], "metaData": {"nextLink": "/things?page=2"}},
    )
    requests_mock.get(
        f"{QUICKEN}/things?page=2",
        json={"resources": [{"id": 2}], "metaData": {"nextLink": None}},
    )

    assert client._unpaginate(path="/things") == [{"id": 1}, {"id": 2}]


# --- get_datasets ------------------------------------------------------------

def test_get_datasets_sends_the_limit_as_a_query_param(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/datasets",
        json={"resources": [{"id": "dataset-1"}], "metaData": {"nextLink": None}},
    )

    result = client.get_datasets(limit=50)

    assert result == [{"id": "dataset-1"}]
    assert requests_mock.last_request.qs["limit"] == ["50"]


# --- get_accounts / get_transactions / get_tags / get_categories -----------

@pytest.mark.parametrize("method_name, path", [
    ("get_accounts", "/accounts"),
    ("get_transactions", "/transactions"),
    ("get_tags", "/tags"),
    ("get_categories", "/categories"),
])
def test_dataset_scoped_list_methods_send_the_dataset_id_header(client, requests_mock, method_name, path):
    requests_mock.get(
        f"{QUICKEN}{path}",
        json={"resources": [{"id": "1"}], "metaData": {"nextLink": None}},
    )

    result = getattr(client, method_name)(datasetId="dataset-1")

    assert result == [{"id": "1"}]
    assert requests_mock.last_request.headers["Qcs-Dataset-Id"] == "dataset-1"


# --- get_transaction ----------------------------------------------------------

def test_get_transaction_returns_the_parsed_transaction(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/transactions/txn-1", json={"id": "txn-1", "payee": "Sam's Club"})

    assert client.get_transaction("txn-1") == {"id": "txn-1", "payee": "Sam's Club"}


def test_get_transaction_sends_dataset_id_header_when_given(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/transactions/txn-1", json={})

    client.get_transaction("txn-1", datasetId="dataset-1")

    assert requests_mock.last_request.headers["Qcs-Dataset-Id"] == "dataset-1"


def test_get_transaction_omits_dataset_id_header_when_not_given(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/transactions/txn-1", json={})

    client.get_transaction("txn-1")

    assert "Qcs-Dataset-Id" not in requests_mock.last_request.headers


# --- update_transaction -------------------------------------------------------

def test_update_transaction_puts_the_transaction_body_and_returns_the_response(client, requests_mock):
    transaction = {"id": "txn-1", "amount": -49.2, "payee": "Sam's Club"}
    requests_mock.put(
        f"{QUICKEN}/transactions/txn-1",
        json={**transaction, "modifiedAt": "2026-08-12T00:00:00Z"},
    )

    result = client.update_transaction("txn-1", transaction)

    assert result == {**transaction, "modifiedAt": "2026-08-12T00:00:00Z"}
    assert requests_mock.last_request.method == "PUT"
    assert requests_mock.last_request.json() == transaction


def test_update_transaction_sends_dataset_id_header_when_given(client, requests_mock):
    requests_mock.put(f"{QUICKEN}/transactions/txn-1", json={})

    client.update_transaction("txn-1", {"id": "txn-1"}, datasetId="dataset-1")

    assert requests_mock.last_request.headers["Qcs-Dataset-Id"] == "dataset-1"


def test_update_transaction_omits_dataset_id_header_when_not_given(client, requests_mock):
    requests_mock.put(f"{QUICKEN}/transactions/txn-1", json={})

    client.update_transaction("txn-1", {"id": "txn-1"})

    assert "Qcs-Dataset-Id" not in requests_mock.last_request.headers


# --- get_account_details -----------------------------------------------------

def test_get_account_details_returns_the_parsed_account(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/accounts/acct-1", json={"id": "acct-1", "name": "Checking"})

    assert client.get_account_details("acct-1") == {"id": "acct-1", "name": "Checking"}


def test_get_account_details_sends_dataset_id_header_when_given(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/accounts/acct-1", json={})

    client.get_account_details("acct-1", datasetId="dataset-1")

    assert requests_mock.last_request.headers["Qcs-Dataset-Id"] == "dataset-1"


# --- get_investment_accounts --------------------------------------------------

def test_get_investment_accounts_filters_out_non_investment_accounts(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/accounts",
        json={
            "resources": [
                {"id": "1", "type": "INVESTMENT"},
                {"id": "2", "type": "BANK"},
            ],
            "metaData": {"nextLink": None},
        },
    )
    requests_mock.get(f"{QUICKEN}/accounts/1", json={
        "id": "1",
        "name": "Brokerage",
        "type": "INVESTMENT",
        "subType": "BROKERAGE",
        "onlineBalance": 1000.5,
        "currency": "USD",
        "lastSuccessfulRefreshAt": "2026-01-01T00:00:00Z",
    })

    result = client.get_investment_accounts("dataset-1")

    assert result == [{
        "id": "1",
        "name": "Brokerage",
        "type": "INVESTMENT",
        "subtype": "BROKERAGE",
        "balance": 1000.5,
        "currency": "USD",
        "last_synced": "2026-01-01T00:00:00Z",
    }]


# --- get_holdings ---------------------------------------------------------

def test_get_holdings_returns_resources_and_filters_by_account(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/investments/holdings", json={"resources": [{"id": "h1"}]})

    result = client.get_holdings("dataset-1", accountId="acct-1")

    assert result == [{"id": "h1"}]
    assert "accountId=acct-1" in requests_mock.last_request.url


def test_get_holdings_returns_an_empty_list_when_the_request_fails(client, requests_mock):
    requests_mock.get(f"{QUICKEN}/investments/holdings", status_code=500)

    assert client.get_holdings("dataset-1") == []


# --- get_net_worth ---------------------------------------------------------

def test_get_net_worth_sums_assets_and_liabilities_by_account_type(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/accounts",
        json={"resources": [{"id": "1"}, {"id": "2"}], "metaData": {"nextLink": None}},
    )
    requests_mock.get(f"{QUICKEN}/accounts/1", json={
        "id": "1", "name": "Checking", "type": "BANK", "onlineBalance": 500,
    })
    requests_mock.get(f"{QUICKEN}/accounts/2", json={
        "id": "2", "name": "Credit Card", "type": "CREDIT", "onlineBalance": -200,
    })

    net_worth = client.get_net_worth("dataset-1")

    assert net_worth == {
        "total_assets": 500,
        "total_liabilities": 200,
        "net_worth": 300,
        "accounts": [
            {"name": "Checking", "type": "BANK", "balance": 500},
            {"name": "Credit Card", "type": "CREDIT", "balance": -200},
        ],
    }


# --- get_total_debt ---------------------------------------------------------

def test_get_total_debt_sums_loan_and_credit_balances_from_the_account_list(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/accounts",
        json={
            "resources": [
                {"id": "1", "name": "Checking", "type": "BANK", "onlineBalance": 500},
                {"id": "2", "name": "Credit Card", "type": "CREDIT", "onlineBalance": -200},
                {"id": "3", "name": "Car Loan", "type": "LOAN", "onlineBalance": -15000},
            ],
            "metaData": {"nextLink": None},
        },
    )

    total_debt = client.get_total_debt("dataset-1")

    assert total_debt == {
        "total_debt": 15200,
        "accounts": [
            {"id": "2", "name": "Credit Card", "type": "CREDIT", "balance": 200},
            {"id": "3", "name": "Car Loan", "type": "LOAN", "balance": 15000},
        ],
    }


def test_get_total_debt_uses_only_the_account_list_and_does_not_fetch_account_details(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/accounts",
        json={
            "resources": [{"id": "1", "name": "Credit Card", "type": "CREDIT", "onlineBalance": -50}],
            "metaData": {"nextLink": None},
        },
    )

    client.get_total_debt("dataset-1")

    # get_net_worth()/get_investment_accounts() fetch details per account;
    # get_total_debt() should need only the single /accounts list call.
    assert len(requests_mock.request_history) == 1


def test_get_total_debt_is_zero_when_there_are_no_liability_accounts(client, requests_mock):
    requests_mock.get(
        f"{QUICKEN}/accounts",
        json={
            "resources": [{"id": "1", "name": "Checking", "type": "BANK", "onlineBalance": 500}],
            "metaData": {"nextLink": None},
        },
    )

    assert client.get_total_debt("dataset-1") == {"total_debt": 0, "accounts": []}
