import getpass
import json
import logging
import os
import sys

import configargparse
from pandas import json_normalize

from simplifiapi.client import Client

logger = logging.getLogger("simplifiapi")

JSON_FORMAT = "json"
CSV_FORMAT = "csv"

DEFAULT_TOKEN_CACHE_FILE = os.path.join(
    os.path.expanduser("~"), ".simplifi-mcp", "token.json")


def _load_cached_token(path):
    try:
        with open(path, "r") as f:
            return json.load(f).get("token")
    except (OSError, ValueError):
        return None


def _save_cached_token(path, token):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True, mode=0o700)
    # Open with an explicit mode instead of writing then chmod'ing, so the
    # token is never briefly readable with default (world-readable) permissions.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": token}, f)


def _clear_cached_token(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def parse_arguments(args):
    parser = configargparse.ArgumentParser()

    # Credential
    parser.add_argument('--email',
                        nargs="?",
                        default=None,
                        help="The e-mail address for your Quicken Simplifi account")
    parser.add_argument('--password',
                        nargs="?",
                        default=None,
                        help="The password for your Quicken Simplifi account. "
                             "Passing this on the command line exposes it in your shell "
                             "history and process list (`ps`); omit it and you will be "
                             "prompted for it securely instead.")
    parser.add_argument('--token',
                        nargs="?",
                        default=None,
                        help="Use existing token to bypass MFA check. Treat this like a "
                             "password: avoid shell history/logs and prefer piping it in "
                             "over typing it inline.")

    # Token cache
    parser.add_argument('--token-cache-file',
                        default=DEFAULT_TOKEN_CACHE_FILE,
                        help="Where to cache the access token so future runs can skip "
                             "login. Defaults to ~/.simplifi-mcp/token.json, written with "
                             "0600 permissions.")
    parser.add_argument('--no-cache',
                        action="store_true",
                        default=False,
                        help="Don't read or write the token cache; always prompt for "
                             "credentials.")
    parser.add_argument('--logout',
                        action="store_true",
                        default=False,
                        help="Delete the cached token and exit.")

    # Datasets
    parser.add_argument('--accounts',
                        action="store_true",
                        default=False,
                        help="Retrieve accounts")
    parser.add_argument('--transactions',
                        action="store_true",
                        default=False,
                        help="Retrieve transactions")
    parser.add_argument('--tags',
                        action="store_true",
                        default=False,
                        help="Retrieve tags")
    parser.add_argument('--categories',
                        action="store_true",
                        default=False,
                        help="Retrieve categories")

    # Export
    parser.add_argument('--filename',
                        default="output",
                        help="Write results to file this prefix")
    parser.add_argument('--format',
                        choices=[JSON_FORMAT, CSV_FORMAT],
                        default=JSON_FORMAT,
                        help="The format used to return data.")

    return parser.parse_args(args)


def write_data(options, data, name):
    filename = "{}_{}.{}".format(options.filename, name, options.format)
    logger.warn("Saving {} to {}".format(name, filename))
    if options.format == CSV_FORMAT:
        json_normalize(data).to_csv(filename, index=False)
    elif options.format == JSON_FORMAT:
        with open(filename, "w+") as f:
            json.dump(data, f, indent=2)


def main():
    options = parse_arguments(sys.argv[1:])

    if options.logout:
        _clear_cached_token(options.token_cache_file)
        logger.info("Cleared cached token at {}".format(options.token_cache_file))
        return

    client = Client()

    # Prefer an explicitly-passed token, then fall back to the cached one from
    # a previous run, before asking the user to log in again.
    token = options.token
    if not token and not options.no_cache:
        token = _load_cached_token(options.token_cache_file)
        if token:
            logger.info("Using cached token from {}".format(options.token_cache_file))

    authenticated = bool(token) and client.verify_token(token)

    if not authenticated:
        if token:
            logger.warning("Cached/provided token is invalid or expired; reauthenticating.")
        if not options.email:
            options.email = input("Email: ")
        if not options.password:
            options.password = getpass.getpass("Password: ")
        token = client.get_token(
            email=options.email, password=options.password)
        authenticated = bool(token) and client.verify_token(token)

    if not authenticated:
        logger.error("Unable to log in simplifi.")
        return

    if not options.no_cache:
        _save_cached_token(options.token_cache_file, token)

    # Retrieve first dataset
    # TODO: Support multiple datasets
    datasets = client.get_datasets()
    dataset_id = datasets[0]["id"]

    if (options.accounts):
        accounts = client.get_accounts(dataset_id)
        write_data(options, accounts, "accounts")

    if options.transactions:
        transactions = client.get_transactions(dataset_id)
        write_data(options, transactions, "transactions")

    if options.tags:
        tags = client.get_tags(dataset_id)
        write_data(options, tags, "tags")

    if options.categories:
        categories = client.get_categories(dataset_id)
        write_data(options, categories, "categories")
