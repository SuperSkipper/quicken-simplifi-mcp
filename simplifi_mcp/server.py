"""MCP server for Quicken Simplifi.

Runs over stdio, so stdin/stdout carry the MCP protocol: never print() or
input() anywhere in this package. Logging goes to stderr (see main()).
"""
import logging
import re
import sys
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from simplifi_mcp.client import Client, LoginError

logger = logging.getLogger("simplifi_mcp")

mcp = MCPServer(
    "simplifi",
    instructions=(
        "Tools for reading and updating the user's Quicken Simplifi data. "
        "If a tool reports the user isn't logged in, call login, and if it "
        "says an MFA code was sent, ask the user for the code and call "
        "submit_mfa_code."
    ),
)

# One Client per server process. It holds the authenticated requests
# session and any login waiting on an MFA code.
_client = None
_authenticated = False
_dataset_id = None

# Fetching every transaction takes several pages, so API results are cached
# for a few minutes: repeated searches in one conversation stay fast.
CACHE_TTL = 300
_cache = {}


def _cached(key, fetch):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = fetch()
    _cache[key] = (time.monotonic() + CACHE_TTL, value)
    return value


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client()
    return _client


def _session():
    """Return (client, dataset_id), authenticating from the cached token.

    Data tools should start with this. It raises a ToolError telling Claude
    to call login when there's no valid cached token.
    """
    global _authenticated, _dataset_id
    client = _get_client()
    if not _authenticated:
        token = client.load_token()
        if not token or not client.verify_token(token):
            raise ToolError("Not logged in to Simplifi. Call the login tool.")
        _authenticated = True
    if _dataset_id is None:
        # TODO: Support multiple datasets
        _dataset_id = client.get_datasets()[0]["id"]
    return client, _dataset_id


def _finish_login(client: Client, token: str) -> str:
    global _authenticated, _dataset_id
    if not token or not client.verify_token(token):
        raise ToolError("Simplifi returned a token that doesn't work.")
    client.save_token(token)
    _authenticated, _dataset_id = True, None
    _cache.clear()
    return "Logged in to Simplifi."


# --- Auth tools ---------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(openWorldHint=True))
def login(email: str | None = None, password: str | None = None) -> str:
    """Log in to Quicken Simplifi. Only needed when another tool says the
    user isn't logged in.

    Leave email and password empty: they're read from the `local`
    environment in resources/http-client.env.json, so credentials never
    pass through the chat. Only pass them if the user explicitly gives
    them. If an MFA code is sent, ask the user for it and call
    submit_mfa_code.
    """
    client = _get_client()
    try:
        result = client.start_login(email=email, password=password)
    except LoginError as e:
        raise ToolError(str(e))
    if result.get("mfa_required"):
        return ("Simplifi sent an MFA code via {}. Ask the user for it, then "
                "call submit_mfa_code.".format(result.get("mfa_channel")))
    return _finish_login(client, result.get("token"))


@mcp.tool(annotations=ToolAnnotations(openWorldHint=True))
def submit_mfa_code(code: str) -> str:
    """Finish a login by submitting the MFA code the user received."""
    client = _get_client()
    try:
        token = client.complete_mfa(code)
    except LoginError as e:
        raise ToolError(str(e))
    return _finish_login(client, token)


@mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=True))
def logout() -> str:
    """Forget the cached Simplifi token."""
    global _authenticated, _dataset_id
    client = _get_client()
    client.clear_token()
    client.session.headers.pop("Authorization", None)
    _authenticated, _dataset_id = False, None
    _cache.clear()
    return "Logged out of Simplifi."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def auth_status() -> str:
    """Check whether the server is logged in to Simplifi."""
    try:
        _, dataset_id = _session()
    except ToolError as e:
        return str(e)
    return "Logged in to Simplifi (dataset {}).".format(dataset_id)


# --- Data tools ---------------------------------------------------------------
# Pattern for the data tools: get (client, dataset_id) from _session(), call
# the matching Client method, and return only the fields Claude needs.
#
# @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
# def list_accounts() -> list[dict]:
#     """List the user's accounts with their type and balance."""
#     client, dataset_id = _session()
#     return [
#         {"id": a.get("id"), "name": a.get("name"), "type": a.get("type"),
#          "balance": a.get("onlineBalance")}
#         for a in client.get_accounts(dataset_id)
#     ]


MAX_LIMIT = 500
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COA_LABELS = {
    "UNCATEGORIZED": "Uncategorized",
    "BALANCE_ADJUSTMENT": "Balance adjustment",
}


def _lookups(client, dataset_id):
    """Return ({account id: name}, {category id: name}), cached."""
    accounts = _cached("accounts", lambda: {
        a["id"]: a.get("name") or a["id"]
        for a in client.get_accounts(dataset_id)})

    def build_categories():
        raw = {c["id"]: c for c in client.get_categories(dataset_id)}
        names = {}
        for cid, c in raw.items():
            name = c.get("name") or cid
            parent = raw.get(c.get("parentId"))
            if parent and parent.get("name"):
                name = "{}: {}".format(parent["name"], name)
            names[cid] = name
        return names

    return accounts, _cached("categories", build_categories)


def _summarize(t, accounts, categories):
    coa = t.get("coa") or {}
    coa_type, coa_id = coa.get("type"), coa.get("id")
    if coa_type == "CATEGORY":
        category = categories.get(coa_id, coa_id)
    elif coa_type == "ACCOUNT":
        category = "Transfer: {}".format(accounts.get(coa_id, coa_id))
    else:
        category = _COA_LABELS.get(coa_type, coa_type)

    summary = {
        "id": t.get("id"),
        "date": t.get("postedOn"),
        "payee": t.get("payee"),
        "amount": t.get("amount"),
        "account": accounts.get(t.get("accountId"), t.get("accountId")),
        "category": category,
        "category_id": coa_id,
        "state": t.get("state"),
    }
    if t.get("memo"):
        summary["memo"] = t["memo"]
    if _is_scheduled(t):
        summary["scheduled"] = True
    return summary


def _is_scheduled(t):
    return (t.get("source") or "").startswith("SCHEDULED_TRANSACTION")


def _transactions(include_scheduled):
    """Session-authenticated, newest-first transactions plus name lookups."""
    client, dataset_id = _session()
    transactions = _cached(
        "transactions", lambda: client.get_transactions(dataset_id))
    if not include_scheduled:
        transactions = [t for t in transactions if not _is_scheduled(t)]
    transactions = sorted(
        transactions, key=lambda t: (t.get("postedOn") or "", t.get("id") or ""),
        reverse=True)
    accounts, categories = _lookups(client, dataset_id)
    return transactions, accounts, categories


def _clamp_limit(limit):
    return max(1, min(int(limit), MAX_LIMIT))


def _check_date(name, value):
    if value is not None and not _DATE.match(value):
        raise ToolError("{} must be YYYY-MM-DD, got {!r}.".format(name, value))


def _matches(text, needle):
    return needle.lower() in (text or "").lower()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def search_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    account: str | None = None,
    category: str | None = None,
    payee_contains: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    include_scheduled: bool = False,
    limit: int = 50,
) -> dict:
    """Search the user's Simplifi transactions, newest first.

    Amounts are negative for money out (spending) and positive for money in.
    Dates are YYYY-MM-DD and inclusive. `account` and `category` match an
    exact id or a case-insensitive part of the name (categories look like
    "Food & Dining: Groceries"; transfers are "Transfer: <account>").
    Scheduled/projected transactions are excluded unless include_scheduled
    is true. If total_matches is more than returned, narrow the filters
    rather than raising the limit (max 500).
    """
    _check_date("start_date", start_date)
    _check_date("end_date", end_date)
    transactions, accounts, categories = _transactions(include_scheduled)

    results = []
    for t in transactions:
        date = t.get("postedOn") or ""
        amount = t.get("amount") or 0
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        if min_amount is not None and amount < min_amount:
            continue
        if max_amount is not None and amount > max_amount:
            continue
        if payee_contains and not _matches(t.get("payee"), payee_contains):
            continue
        summary = _summarize(t, accounts, categories)
        if account and not (t.get("accountId") == account
                            or _matches(summary["account"], account)):
            continue
        if category and not (summary["category_id"] == category
                             or _matches(summary["category"], category)):
            continue
        results.append(summary)

    shown = results[:_clamp_limit(limit)]
    return {"total_matches": len(results), "returned": len(shown),
            "transactions": shown}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def retrieve_all(offset: int = 0, limit: int = 200,
                 include_scheduled: bool = False) -> dict:
    """Page through every transaction, newest first.

    Only use this to scan the full history; for anything targeted (a date
    range, payee, account or category) use search_transactions instead.
    Amounts are negative for money out. Continue with offset + returned
    until offset reaches total. limit max is 500.
    """
    transactions, accounts, categories = _transactions(include_scheduled)
    offset = max(0, int(offset))
    page = transactions[offset:offset + _clamp_limit(limit)]
    return {
        "total": len(transactions),
        "offset": offset,
        "returned": len(page),
        "transactions": [_summarize(t, accounts, categories) for t in page],
    }


def main():
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
