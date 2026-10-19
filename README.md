# simplifi-mcp

An unofficial [MCP](https://modelcontextprotocol.io) server for Quicken Simplifi. It lets Claude (or any MCP client) read your Simplifi data and update transactions.

## Install

Requires Python 3.10+.

```shell
git clone https://github.com/SuperSkipper/simplifi-mcp
cd simplifi-mcp
PIPENV_VENV_IN_PROJECT=1 pipenv install --dev    # or: python3 -m venv .venv && .venv/bin/pip install -e .
```

## Add it to Claude Code

```shell
claude mcp add simplifi -- /path/to/simplifi-mcp/.venv/bin/python -m simplifi_mcp
```

Run `/mcp` in Claude Code to check that it's connected. For other MCP clients, the command is the same: run `python -m simplifi_mcp` (or the `simplifi-mcp` script) from the project's virtualenv, over stdio.

## Logging in

The server caches your access token in `resources/http-client.env.json`, under the `token` variable of the `local` environment. This is the JetBrains HTTP Client env file used by `resources/api-requests.http`, so the same token also works as `{{token}}` there.

When the token is missing or expired, the tools tell Claude to log in:

1. Claude calls the `login` tool.
2. If Simplifi sends an MFA code, Claude asks you for it and calls `submit_mfa_code`.
3. The new token is saved, and future sessions reuse it until it expires.

To keep your password out of the chat, put your credentials in the env file and let `login` read them:

```json
{
  "local": {
    "host": "https://services.quicken.com",
    "email": "you@example.com",
    "password": "..."
  }
}
```

The env file is git-ignored. It holds a bearer token, and possibly your password, so treat it like a password.

`logout` deletes the cached token. `auth_status` checks whether the server is logged in.

## Development

```shell
pipenv run pytest                       # or .venv/bin/pytest
pipenv run mcp dev simplifi_mcp/server.py   # interactive MCP Inspector
```

`resources/api-requests.http` has the raw Simplifi API requests for exploring endpoints in a JetBrains IDE.

## Thanks

Based on [rijn/simplifiapi](https://github.com/rijn/simplifiapi), which was inspired by [mintapi](https://github.com/mintapi/mintapi).
