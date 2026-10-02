"""Build only: never used by the Go research executable."""
import argparse, hashlib, pathlib, subprocess
p=argparse.ArgumentParser();p.add_argument('--go',default='go');p.add_argument('--output',required=True);a=p.parse_args()
root=pathlib.Path(__file__).resolve().parents[1]
paths=sorted([*root.rglob('*.go'),root/'go.mod',root/'go.sum'])
h=hashlib.sha256()
for path in paths:
 h.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
identity=h.hexdigest()
subprocess.run([a.go,'build','-trimpath','-ldflags',f'-X main.sourceHash={identity}','-o',str(pathlib.Path(a.output).resolve()),'./cmd/stockfish-r1'],cwd=root,check=True)
print(identity)
