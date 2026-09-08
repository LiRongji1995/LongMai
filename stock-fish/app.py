"""StockFish product shell; all final decisions use stockfish_core.

The original deployment entry (app.py, HOST/PORT/DEBUG) is preserved. Legacy
research modules remain importable, but never own an API final ranking.
"""
from config import settings
from stockfish_core.api import create_app

app = create_app()

if __name__ == '__main__':
    import argparse
    import os

    parser = argparse.ArgumentParser(description='StockFish reliable research baseline')
    parser.add_argument('--with-bot', action='store_true')
    parser.add_argument('--port', type=int, default=settings.PORT)
    args = parser.parse_args()
    if args.with_bot:
        parser.error('Bot publishing is experimental and requires an explicit canonical recommendation integration.')
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if settings.HOST not in ('127.0.0.1', 'localhost', '::1') and not os.environ.get('STOCKFISH_API_KEY'):
        parser.error('Remote binding requires STOCKFISH_API_KEY and STOCKFISH_ALLOWED_HOSTS.')
    # A reloader would create competing workers for the same durable job database.
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    core = app.extensions['stockfish_core']
    core.candidate_scheduler = CandidateScheduler(core.candidates)
    core.candidate_scheduler.start()
    # One durable JobService OS lock owns this database across all ports.
    # Waitress is already pinned in requirements-windows.lock.txt.
    from waitress import serve
    core.candidates.runtime_port = args.port
    try:
        serve(app, host=settings.HOST, port=args.port, threads=4)
    finally:
        core.close()
