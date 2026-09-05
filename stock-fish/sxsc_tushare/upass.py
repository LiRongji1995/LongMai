"""The legacy home-directory token store is disabled; credentials stay external."""
from stockfish_core.common import UnavailableError
def get_token():
    raise UnavailableError("SXSC_TOKEN_STORE_DISABLED", "data")
def set_token(token):
    raise UnavailableError("SXSC_TOKEN_STORE_DISABLED", "data")
