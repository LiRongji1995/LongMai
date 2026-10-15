"""Standalone local StockFish terminal entry. No Codex application required."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from stockfish_core.yaogu.premarket_workflow_v1 import main

if __name__ == '__main__':
    raise SystemExit(main())
