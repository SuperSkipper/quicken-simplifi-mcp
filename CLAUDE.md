# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`simplifi-mcp` is an unofficial MCP server for Quicken Simplifi (`https://services.quicken.com`). It's built on the official `mcp` Python SDK, **v2.x**, where `FastMCP` was renamed to `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Examples online that use `mcp.server.fastmcp` are written for v1. The HTTP client started as a fork of rijn/simplifiapi.

## Commands

Dependencies are managed with Pipenv (`Pipfile`, Python 3.14, venv in `.venv/`). Packaging and pytest config live in `pyproject.toml`.

```shell
PIPENV_VENV_IN_PROJECT=1 pipenv install --dev
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_server.py::test_login_with_mfa_then_submit_mfa_code
.venv/bin/python -m simplifi_mcp            # run the server on stdio
.venv/bin/mcp dev simplifi_mcp/server.py    # MCP Inspector
```

There is no linter or formatter configured.

## Architecture

- `simplifi_mcp/server.py`: the `MCPServer("simplifi")` instance and its tools. It holds module-level state: one `Client`, an `_authenticated` flag and a cached `_dataset_id`.
  - **Data tools start with `_session()`**, which returns `(client, dataset_id)`. It authenticates from the cached token and raises `ToolError("... Call the login tool.")` when the token is missing or invalid. The dataset is always `get_datasets()[0]`.
  - Auth tools: `login` → (`submit_mfa_code`) → token saved; `logout`; `auth_status`. Login takes two tool calls because Simplifi may send an MFA code, and Claude asks the user for it.
  - **Never `print()` or `input()`.** stdin and stdout are the MCP protocol channel. `main()` sends logging to stderr.
  - Transaction tools (`search_transactions`, `retrieve_all`) share `_transactions()`. API results (transactions, account names and category names) go through `_cached()` with a 5-minute TTL, which login and logout clear. `_summarize()` trims each record to a few fields and turns IDs into names. Scheduled/projected transactions (`source` `SCHEDULED_TRANSACTION*`) are excluded by default.
  - Annotate tools with `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, and so on). The docstring and type hints become the tool description and schema Claude sees.
- `simplifi_mcp/client.py`: the `Client` class wraps a single `requests.Session`.
  - **Login** has two steps: `start_login()` returns either `{"token": ...}` or `{"mfa_required": True, ...}`. In the second case the request body is kept in `_pending_login` (the MFA follow-up must reuse its `threatMetrixSessionId`), and `complete_mfa(code)` finishes it. Failures raise `LoginError`. `verify_token` calls `/userprofiles/me`, and **on success it sets the `Authorization` header on the session**. Later calls depend on that side effect.
  - Almost every resource is scoped by the `Qcs-Dataset-Id` header. List endpoints use `_unpaginate`, which follows `metaData.nextLink` and collects `resources`.
  - `update_transaction` does a PUT of the **full** transaction body (the API replaces the whole record). See `tests/fixtures/transaction_before.json` and `transaction_after.json`.
  - Aggregate helpers (`get_net_worth`, `get_investment_accounts`) make one detail call per account. `get_total_debt` uses only the account list.

### Token / credential storage

The token cache is the **JetBrains HTTP Client env file** `resources/http-client.env.json` (`DEFAULT_TOKEN_ENV_FILE`). It's git-ignored. The `token` and an optional `email`/`password` live under the `"local"` environment, so a token saved by the server works as `{{token}}` in `resources/api-requests.http`, and the reverse is also true. `save_token`/`clear_token` change only the `token` key. `start_login` falls back to the `email`/`password` stored there.

## Tests

- `tests/conftest.py` provides a `client` fixture pointed at a throwaway `tmp_path` env file, plus a `write_env(**vars)` factory for its `local` environment. Never touch the real env file in tests.
- HTTP is mocked with the `requests_mock` fixture. Tests assert on `requests_mock.request_history`.
- Server tests call the tool functions directly (`@mcp.tool()` returns the original function). The autouse `fresh_server` fixture in `tests/test_server.py` injects the `client` fixture and resets the module state.
