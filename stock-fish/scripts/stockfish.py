"""Standalone local StockFish terminal entry. No Codex application required."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    from stockfish_core.yaogu.r2_closure_cli import COMMANDS
    if args in (['--help'], ['-h']):
        from stockfish_core.yaogu.premarket_workflow_v1 import parser
        command_parser = parser()
        command_parser.epilog = ('Explicit R2 archive and daily observation commands: '
            + ', '.join(sorted(COMMANDS)) + '. Use COMMAND --help for required database and evidence arguments.')
        command_parser.print_help()
        return 0
    if args and args[0] in COMMANDS:
        from stockfish_core.yaogu.r2_closure_cli import main as closure_main
        return closure_main(args)
    from stockfish_core.yaogu.premarket_workflow_v1 import main as premarket_main
    return premarket_main(args)

if __name__ == '__main__':
    raise SystemExit(main())
