"""Build only: never used by the Go research executable."""
import argparse, hashlib, pathlib, subprocess, json
from build_identity import manifest, runtime_python_paths, worker_manifest
p=argparse.ArgumentParser();p.add_argument('--go',default='go');p.add_argument('--output',required=True);p.add_argument('--command',choices=['stockfish','stockfish-r1','stockfish-migrate'],default='stockfish-r1');a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
paths=sorted(set([*root.rglob('*.go'),*root.rglob('*.sql'),*root.glob('infrastructure/calendar/*.json'),*root.glob('infrastructure/provider/*.py'),*runtime_python_paths(root),root/'go.mod',root/'go.sum',root/'tools/build.py',root/'tools/build_identity.py']))
h=hashlib.sha256()
for path in paths:
 relative=path.relative_to(root).as_posix() if path.is_relative_to(root) else '../'+path.relative_to(root.parent).as_posix()
 h.update(relative.encode()+b'\0'+path.read_bytes()+b'\0')
identity=h.hexdigest()
toolchain=subprocess.check_output([a.go,'version'],text=True).strip()
business, facts = manifest(root,toolchain=toolchain), manifest(root, facts=True,toolchain=toolchain)
pkg='github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/buildidentity'
flags=f'-X main.sourceHash={identity} -X {pkg}.businessHash={business["sha256"]} -X {pkg}.factsHash={facts["sha256"]}'
subprocess.run([a.go,'build','-trimpath','-ldflags',flags,'-o',str(pathlib.Path(a.output).resolve()),'./cmd/'+a.command],cwd=root,check=True)
pathlib.Path(str(pathlib.Path(a.output).resolve())+'.identity.json').write_text(json.dumps({'go_source_hash':identity,'business':business,'facts':facts,'fixed_worker':worker_manifest(root)},indent=2)+'\n')
print(identity)
