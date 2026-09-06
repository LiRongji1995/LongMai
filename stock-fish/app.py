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
    args = parser.parse_args()
    if args.with_bot:
        parser.error('Bot publishing is experimental and requires an explicit canonical recommendation integration.')
    if settings.HOST not in ('127.0.0.1', 'localhost', '::1') and not os.environ.get('STOCKFISH_API_KEY'):
        parser.error('Remote binding requires STOCKFISH_API_KEY and STOCKFISH_ALLOWED_HOSTS.')
    # A reloader would create competing workers for the same durable job database.
    from stockfish_core.yaogu.scheduler import CandidateScheduler
    core = app.extensions['stockfish_core']
    core.candidate_scheduler = CandidateScheduler(core.candidates)
    core.candidate_scheduler.start()
    app.run(host=settings.HOST, port=settings.PORT, debug=False, threaded=True, use_reloader=False)
