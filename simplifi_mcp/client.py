import json
import logging
import os
import requests
import uuid
from urllib.parse import urljoin

logger = logging.getLogger("simplifi_mcp")

SIMPLIFI_ENDPOINT = "https://services.quicken.com"
DEFAULT_TIMEOUT = 30

# The JetBrains HTTP Client environment file in resources/, next to
# api-requests.http. Reusing it (rather than a separate cache file) means a
# token fetched here is immediately usable as {{token}} in
# api-requests.http, and vice versa.
DEFAULT_TOKEN_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources", "http-client.env.json")
DEFAULT_TOKEN_ENV_NAME = "local"


def _redact(token):
    if not token:
        return token
    return "{}...{}".format(token[:4], token[-4:]) if len(token) > 8 else "***"


class LoginError(Exception):
    pass


class Client():

    def __init__(self, token_env_file=DEFAULT_TOKEN_ENV_FILE,
                 token_env_name=DEFAULT_TOKEN_ENV_NAME):
        self.session = requests.Session()
        self.token_env_file = token_env_file
        self.token_env_name = token_env_name
        self._pending_login = None

    def _read_token_env(self):
        try:
            with open(self.token_env_file, "r") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def load_token(self):
        """Read a previously cached token from the HTTP Client env file, if any."""
        env = self._read_token_env()
        return env.get(self.token_env_name, {}).get("token")

    def save_token(self, token):
        """Persist the token into the HTTP Client env file, preserving any
        other environments/variables already stored there."""
        env = self._read_token_env()
        env.setdefault(self.token_env_name, {})["token"] = token
        with open(self.token_env_file, "w") as f:
            json.dump(env, f, indent=2)
            f.write("\n")

    def clear_token(self):
        """Remove the cached token from the HTTP Client env file, leaving
        any other environments/variables stored there untouched."""
        env = self._read_token_env()
        if env.get(self.token_env_name, {}).pop("token", None) is not None:
            with open(self.token_env_file, "w") as f:
                json.dump(env, f, indent=2)
                f.write("\n")

    def start_login(self, email=None, password=None) -> dict:
        """Begin the OAuth login.

        Returns {"token": ...} if the login completed, or
        {"mfa_required": True, "mfa_channel": ...} if Simplifi sent an MFA
        code, in which case call complete_mfa() with it. Never prompts on
        stdin, so it's safe to call from the MCP server.
        """
        # Fall back to the email/password stored in the HTTP Client env
        # file when the caller doesn't pass them explicitly. If the file
        # doesn't have them either, fall through to the existing flow
        # (whatever email/password the caller did pass, including None).
        if not email or not password:
            env = self._read_token_env().get(self.token_env_name, {})
            used_env = (not email and env.get("email")) or (not password and env.get("password"))
            email = email or env.get("email")
            password = password or env.get("password")
            if used_env:
                logger.info(
                    "Using email/password from {}".format(self.token_env_file))

        # Step 1: Oauth authorize
        body = {
            "clientId": "acme_web",
            "mfaChannel": None,
            "mfaCode": None,
            "password": password,
            "redirectUri": "https://app.simplifimoney.com/login",
            "responseType": "code",
            "threatMetrixRequestId": None,
            "threatMetrixSessionId": str(uuid.uuid4()),
            "username": email,
        }
        r = self.session.post(
            url="https://services.quicken.com/oauth/authorize", json=body,
            timeout=DEFAULT_TIMEOUT)
        data = r.json()
        if data.get("status") == "MFA code sent":
            mfaChannel = data.get("mfaChannel")
            logger.warning("MFA Channel: {}".format(mfaChannel))
            # Keep the request body (same threatMetrixSessionId) so the MFA
            # follow-up is recognised as the same login attempt.
            body["mfaChannel"] = mfaChannel
            self._pending_login = body
            return {"mfa_required": True, "mfa_channel": mfaChannel}

        return {"token": self._exchange_code(self._auth_code(data))}

    def complete_mfa(self, mfa_code: str) -> str:
        """Finish a login started by start_login() and return the token."""
        body = self._pending_login
        if body is None:
            raise LoginError("No login in progress; call start_login() first.")
        body["mfaCode"] = mfa_code
        r = self.session.post(
            url="https://services.quicken.com/oauth/authorize", json=body,
            timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "User passed MFA":
            logger.error("MFA check failed: {}".format(data))
            raise LoginError("MFA check failed: {}".format(data.get("status")))
        self._pending_login = None
        return self._exchange_code(self._auth_code(data))

    @staticmethod
    def _auth_code(data: dict) -> str:
        code = data.get("code")
        if not code:
            raise LoginError("Login failed: {}".format(data.get("status") or data))
        return code

    def _exchange_code(self, code: str) -> str:
        # Step 2: Get token
        r = self.session.post(url="https://services.quicken.com/oauth/token",
                              json={
                                  "clientId": "acme_web",
                                  "clientSecret": "BCDCxXwdWYcj@bK6",
                                  "grantType": "authorization_code",
                                  "code": code,
                                  "redirectUri": "https://app.simplifimoney.com/login"
                              },
                              timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        token = r.json().get("accessToken")

        logger.warning("Retrieved token {}".format(_redact(token)))

        return token

    def verify_token(self, token) -> bool:
        headers = {"Authorization": "Bearer {}".format(token)}

        r = self.session.get(url="https://services.quicken.com/userprofiles/me",
                             headers=headers, timeout=DEFAULT_TIMEOUT)
        if (r.status_code != 200):
            logger.error("Error code: {}".format(r.status_code))
            logger.error(r.json())
            return False
        data = r.json()
        userId = data.get("id")
        logger.warning("User {} logged in.".format(userId))

        # Update session
        self.session.headers.update(headers)

        return True

    def _unpaginate(self, path: str, **kargs):
        nextLink = path
        data = []
        while nextLink:
            logger.warning("Fetching {}".format(nextLink))
            kargs.setdefault("timeout", DEFAULT_TIMEOUT)
            r = self.session.get(url=urljoin(
                SIMPLIFI_ENDPOINT, nextLink), **kargs)
            r.raise_for_status()
            data.extend(r.json()["resources"])
            nextLink = r.json().get("metaData").get("nextLink")
        return data

    def get_datasets(self, limit: int = 1000):
        return self._unpaginate(path="/datasets",
                                params={
                                    "limit": limit,
                                })

    def get_accounts(self, datasetId: str):
        return self._unpaginate(path="/accounts",
                                headers={
                                     "Qcs-Dataset-Id": datasetId,
                                },
                                params={
                                    "limit": 1000,
                                })

    def get_transactions(self, datasetId: str):
        return self._unpaginate(path="/transactions",
                                headers={
                                     "Qcs-Dataset-Id": datasetId,
                                },
                                params={
                                    "limit": 1000,
                                })

    def get_transaction(self, transactionId: str, datasetId: str = "") -> dict:
        """Get a single transaction by id."""
        headers = dict(self.session.headers)
        if datasetId:
            headers["Qcs-Dataset-Id"] = datasetId
        r = self.session.get(
            url=urljoin(SIMPLIFI_ENDPOINT, f"/transactions/{transactionId}"),
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def update_transaction(self, transactionId: str, transaction: dict, datasetId: str = "") -> dict:
        """Update a transaction.

        `transaction` should be the full transaction body (the same shape
        returned by get_transactions() / get_account_details() siblings --
        see tests/fixtures/transaction_after.json for a worked example), since
        the API replaces the transaction wholesale rather than patching
        individual fields.
        """
        headers = dict(self.session.headers)
        headers.update({
            "Accept": "application/json, text/plain, */*",
            "App-Client-Id": "acme_web",
            "Content-Type": "application/json;charset=UTF-8",
        })
        if datasetId:
            headers["Qcs-Dataset-Id"] = datasetId
        r = self.session.put(
            url=urljoin(SIMPLIFI_ENDPOINT, f"/transactions/{transactionId}"),
            headers=headers,
            json=transaction,
            timeout=DEFAULT_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def get_tags(self, datasetId: str):
        return self._unpaginate(path="/tags",
                                headers={
                                     "Qcs-Dataset-Id": datasetId,
                                },
                                params={
                                    "limit": 1000,
                                })

    def get_categories(self, datasetId: str):
        return self._unpaginate(path="/categories",
                                headers={
                                     "Qcs-Dataset-Id": datasetId,
                                },
                                params={
                                    "limit": 1000,
                                })

    def get_account_details(self, accountId: str, datasetId: str = "") -> dict:
        """Get detailed account information including balance."""
        headers = dict(self.session.headers)
        if datasetId:
            headers["Qcs-Dataset-Id"] = datasetId
        r = self.session.get(
            url=urljoin(SIMPLIFI_ENDPOINT, f"/accounts/{accountId}"),
            headers=headers
        )
        r.raise_for_status()
        return r.json()

    def get_investment_accounts(self, datasetId: str) -> list:
        """Get all investment accounts with their balances."""
        accounts = self.get_accounts(datasetId)
        investment_accounts = []

        for acc in accounts:
            if acc.get("type") == "INVESTMENT":
                # Get full details for balance
                details = self.get_account_details(acc["id"], datasetId)
                investment_accounts.append({
                    "id": details.get("id"),
                    "name": details.get("name"),
                    "type": details.get("type"),
                    "subtype": details.get("subType"),
                    "balance": details.get("onlineBalance", 0) or 0,
                    "currency": details.get("currency", "USD"),
                    "last_synced": details.get("lastSuccessfulRefreshAt"),
                })

        return investment_accounts

    def get_holdings(self, datasetId: str, accountId: str = "") -> list:
        """Get investment holdings.

        Note: This endpoint may return empty results if holdings data
        is not available through the API.
        """
        headers = dict(self.session.headers)
        headers["Qcs-Dataset-Id"] = datasetId

        params = {"limit": 1000}
        if accountId:
            params["accountId"] = accountId

        try:
            r = self.session.get(
                url=urljoin(SIMPLIFI_ENDPOINT, "/investments/holdings"),
                headers=headers,
                params=params
            )
            r.raise_for_status()
            return r.json().get("resources", [])
        except Exception as e:
            logger.warning(f"Failed to fetch holdings: {e}")
            return []

    def get_net_worth(self, datasetId: str) -> dict:
        """Calculate net worth from all accounts."""
        accounts = self.get_accounts(datasetId)

        total_assets = 0
        total_liabilities = 0
        account_summary = []

        asset_types = {"INVESTMENT", "BANK", "REAL_ESTATE", "VEHICLE"}
        liability_types = {"LOAN", "CREDIT"}

        for acc in accounts:
            details = self.get_account_details(acc["id"], datasetId)
            balance = details.get("onlineBalance", 0) or 0
            acc_type = details.get("type", "")
            name = details.get("name", "")

            account_summary.append({
                "name": name,
                "type": acc_type,
                "balance": balance,
            })

            if acc_type in asset_types:
                total_assets += balance
            elif acc_type in liability_types:
                total_liabilities += abs(balance)

        return {
            "total_assets": total_assets,
            "total_liabilities": total_liabilities,
            "net_worth": total_assets - total_liabilities,
            "accounts": account_summary,
        }

    def get_total_debt(self, datasetId: str) -> dict:
        """Calculate total debt from liability accounts (loans and credit
        cards).

        Unlike get_net_worth(), this only uses the balances already
        returned by get_accounts() -- no per-account detail calls needed.
        """
        accounts = self.get_accounts(datasetId)

        liability_types = {"LOAN", "CREDIT"}
        debt_accounts = []
        total_debt = 0

        for acc in accounts:
            if acc.get("type") not in liability_types:
                continue
            balance = abs(acc.get("onlineBalance", 0) or 0)
            debt_accounts.append({
                "id": acc.get("id"),
                "name": acc.get("name"),
                "type": acc.get("type"),
                "balance": balance,
            })
            total_debt += balance

        return {
            "total_debt": total_debt,
            "accounts": debt_accounts,
        }