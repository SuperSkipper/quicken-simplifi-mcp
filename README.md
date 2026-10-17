# simplifiapi
An unofficial API for Quicken Simplifi

## Install

PyPI is temporarily down. Install with pip from GitHub directly

```shell
pip3 install git+https://github.com/rijn/simplifiapi
```

## Local Development

Clone the repo and work from a virtual environment:

```shell
git clone https://github.com/rijn/simplifiapi
cd simplifiapi
python3 -m venv venv
source venv/bin/activate
```

### Editable install

For day-to-day development, install the package in editable mode so changes under
`simplifiapi/` take effect immediately without reinstalling:

```shell
pip3 install -e .
```

This also installs the `simplifiapi` console script and its dependencies
(`configargparse`, `pandas`, `requests`), so you can run `simplifiapi --help` right away.

### Building a distributable package

To build the sdist/wheel the same way [`deploy.sh`](./deploy.sh) does for releases:

```shell
pip3 install build
python3 -m build
```

This produces `dist/*.whl` and `dist/*.tar.gz`, which you can install locally with:

```shell
pip3 install dist/*.whl
```

## CLI

This package provides a command-line tool that could access and save data to local files.

```shell
usage: simplifiapi [-h] [--email [EMAIL]] [--password [PASSWORD]] [--token [TOKEN]] [--token-cache-file TOKEN_CACHE_FILE] [--no-cache] [--logout] [--accounts] [--transactions] [--tags] [--categories] [--filename FILENAME] [--format {json,csv}]

optional arguments:
  -h, --help            show this help message and exit
  --email [EMAIL]       The e-mail address for your Quicken Simplifi account
  --password [PASSWORD]
                        The password for your Quicken Simplifi account
  --token [TOKEN]       Use existing token to bypass MFA check
  --token-cache-file TOKEN_CACHE_FILE
                        Where to cache the access token between runs (default:
                        resources/http-client.env.json)
  --no-cache            Don't read or write the token cache; always prompt for credentials
  --logout              Delete the cached token and exit
  --accounts            Retrieve accounts
  --transactions        Retrieve transactions
  --tags                Retrieve tags
  --categories          Retrieve categories
  --filename FILENAME   Write results to file this prefix
  --format {json,csv}   The format used to return data.

examples:
> simplifiapi --token="..." --transactions
> simplifiapi --token="..." --transactions --filename=20231125 --format=csv
```

### Token persistence

The first time you log in (via `--email`/`--password` or `--token`), the resulting access
token is cached as the `token` variable of the `local` environment in
`resources/http-client.env.json` — the JetBrains HTTP Client env file used by
`resources/api-requests.http`, so the same token works as `{{token}}` there. Other
variables in that file are preserved. If `email`/`password` are set in the `local`
environment, they're used as login defaults. Subsequent runs reuse the cached token
automatically and skip the login/MFA flow entirely — you'll only be prompted again once the
cached token is rejected by the server (expired/revoked).

- `--token-cache-file PATH` — use a different cache location.
- `--no-cache` — never read or write the cache; always authenticate fresh.
- `--logout` — delete the cached token.

> **Security note:** avoid passing `--password` or `--token` directly on the command line —
> they end up in your shell history and are visible to other local users via `ps`. If you
> omit `--email`/`--password`, the CLI will prompt for them interactively (password input
> is hidden). The env file is a bearer credential too (and may hold your password) — it's
> git-ignored, but back it up and share it with the same care as a password.

## Python API

The `Client` class allows accessing from python script and making custom analysis.

```python
from simplifiapi.client import Client

client = Client()

# Provide either token or email/password
token = "..."
token = client.get_token(email=options.email, password=options.password)

assert client.verify_token(token)

# Datasets own transactions and accounts
datasets = client.get_datasets()
datasetId = datasets[0]["id"]

# Access transactions
transactions = client.get_transactions(datasetId)
```

## Thanks

This library is heavily inspired by [mintapi](https://github.com/mintapi/mintapi).