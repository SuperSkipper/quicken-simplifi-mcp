import json

import pytest

from simplifi_mcp.client import Client


@pytest.fixture
def token_env_file(tmp_path):
    """Path to a fresh, empty HTTP Client env file for a test to populate."""
    return tmp_path / "http-client.env.json"


@pytest.fixture
def write_env(token_env_file):
    """Factory that writes the given variables into the `local` environment
    of token_env_file, in the same shape as http-client.env.json."""
    def _write(**local_vars):
        token_env_file.write_text(json.dumps({"local": local_vars}))
    return _write


@pytest.fixture
def client(token_env_file):
    """A Client pointed at an isolated, throwaway env file instead of the
    real http-client.env.json."""
    return Client(token_env_file=str(token_env_file))
