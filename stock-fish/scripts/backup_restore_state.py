"""Explicit private local backup/restore, never overwrites an existing path."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stockfish_core.common import canonical_json
from stockfish_core.snapshot_backup import backup_state, restore_state, verify_backup

parser = argparse.ArgumentParser(description=__doc__)
commands = parser.add_subparsers(dest='command', required=True)
backup = commands.add_parser('backup')
backup.add_argument('--database', required=True)
backup.add_argument('--destination', required=True)
backup.add_argument('--artifact-root', action='append', default=[])
restore = commands.add_parser('restore')
restore.add_argument('--backup', required=True)
restore.add_argument('--destination', required=True)
verify = commands.add_parser('verify')
verify.add_argument('--backup', required=True)
args = parser.parse_args()
if args.command == 'backup':
    result = backup_state(args.database, args.destination, artifact_roots=args.artifact_root)
elif args.command == 'restore':
    result = restore_state(args.backup, args.destination)
else:
    result = verify_backup(args.backup)
print(canonical_json(result))
