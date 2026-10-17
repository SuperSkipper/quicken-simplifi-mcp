# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`simplifiapi` is an unofficial Python client and CLI for the Quicken Simplifi API (`https://services.quicken.com`), forked from rijn/simplifiapi. The repo is named `simplifi-mcp`, but there is no MCP server code yet; today it's the client library, the CLI, and a JetBrains HTTP Client request collection.

## Commands

Dependencies are managed with Pipenv (`Pipfile`, Python 3.14). Pytest and requests-mock are dev dependencies.

```shell
pipenv install --dev            # or: pip install -e . && pip install pytest requests-mock
pytest                          # testpaths = tests (pytest.ini)
pytest tests/test_client.py::test_get_token_logs_in_with_explicit_credentials
pytest -k verify_token
python -m simplifiapi --help    # or the `simplifiapi` console script after an editable install
./deploy.sh                     # python -m build + twine upload
```

There is no linter or formatter configured.

## Architecture

- `simplifiapi/client.py`: the `Client` class wraps a single `requests.Session`. Every request goes through `SIMPLIFI_ENDPOINT` with `DEFAULT_TIMEOUT`.
  - **Auth** (`get_token`) is a two-step OAuth flow: POST `/oauth/authorize` returns a `code`, with an interactive `input()` MFA prompt if the status is `"MFA code sent"`. Then POST `/oauth/token` exchanges the code for an `accessToken`. `verify_token` calls `/userprofiles/me`, and **on success it sets the `Authorization` header on the session**. Later calls depend on that side effect.
  - **Datasets**: almost every resource is scoped to a dataset through the `Qcs-Dataset-Id` header. Call `get_datasets()` first. The CLI only uses `datasets[0]`.
  - **List endpoints** use `_unpaginate`, which follows `metaData.nextLink` and collects `resources`. Single-resource methods (`get_transaction`, `get_account_details`, `update_transaction`) copy `session.headers` and add the dataset header.
  - `update_transaction` does a PUT of the **full** transaction body. The API replaces the whole record, so it doesn't patch individual fields. See `transaction_update_example.json` and `unupdated_transaction.json` for before/after shapes.
  - Aggregate helpers (`get_net_worth`, `get_investment_accounts`, `get_total_debt`) are built on top of the raw endpoints. `get_net_worth` and `get_investment_accounts` make one detail call per account. `get_total_debt` only uses the list response.
- `simplifiapi/cli.py`: `configargparse` CLI. Token resolution order: `--token`, then the cached token, then `--email`/`--password`, then an interactive prompt. A verified token is written back to the cache unless `--no-cache` is set. Output is written to `<filename>_<name>.<json|csv>`, and CSV goes through `pandas.json_normalize`.

### Token / credential storage (shared with the HTTP Client)

The token cache is a **JetBrains HTTP Client env file** (`http-client.env.json`), not a separate cache. Tokens and an optional `email`/`password` live under the `"local"` environment. That way a token fetched by the CLI works as `{{token}}` in `api-requests.http`, and the reverse is also true. `save_token`/`clear_token` change only the `token` key and keep other environments and variables. `get_token` falls back to the `email`/`password` stored there.

- `DEFAULT_TOKEN_ENV_FILE` resolves to `resources/http-client.env.json`, next to `resources/api-requests.http`. The file is git-ignored because it holds real credentials.

## Tests

- `tests/conftest.py` provides a `client` fixture pointed at a throwaway `tmp_path` env file, plus a `write_env(**vars)` factory for filling in its `local` environment. Always use these fixtures so tests never touch the real `http-client.env.json`.
- HTTP is mocked with the `requests_mock` pytest fixture against `https://services.quicken.com/...`. Tests assert on `requests_mock.request_history` to check request bodies and headers.
- CLI tests call `cli.parse_arguments([...])` directly and patch `cli.Client`/`sys.argv` for `main()`.
