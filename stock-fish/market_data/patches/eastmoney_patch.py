"""Unsafe process-wide HTTP/user-agent spoofing patch is intentionally disabled."""
from stockfish_core.common import UnavailableError
def eastmoney_patch(*args, **kwargs):
    raise UnavailableError("EASTMONEY_GLOBAL_PATCH_DISABLED", "data")
