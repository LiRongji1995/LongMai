"""Disabled unsafe credential relay. Use the official HTTPS client explicitly."""
from stockfish_core.common import UnavailableError
class DataApi:
    def __init__(self, token=None):
        raise UnavailableError("SXSC_RELAY_DISABLED", "data")
def get_api(env="prd", token=None):
    raise UnavailableError("SXSC_RELAY_DISABLED", "data")
