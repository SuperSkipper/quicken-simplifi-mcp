import getpass
import json
import logging
import sys

import configargparse
from pandas import json_normalize

from simplifiapi.client import Client, DEFAULT_TOKEN_ENV_FILE

logger = logging.getLogger("simplifiapi")

JSON_FORMAT = "json"
CSV_FORMAT = "csv"


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
                        default=DEFAULT_TOKEN_ENV_FILE,
                        help="Where to cache the access token so future runs can skip "
                             "login. Defaults to the 'local' environment in "
                             "http-client.env.json, so the token doubles as the "
                             "{{token}} variable for api-requests.http.")
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

    client = Client(token_env_file=options.token_cache_file)

    if options.logout:
        client.clear_token()
        logger.info("Cleared cached token in {}".format(options.token_cache_file))
        return

    # Prefer an explicitly-passed token, then fall back to the cached one from
    # a previous run, before asking the user to log in again.
    token = options.token
    if not token and not options.no_cache:
        token = client.load_token()
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
        client.save_token(token)

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
