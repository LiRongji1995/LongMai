"""TEST_FIXTURE actual strategy functions in generated, isolated source trees."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest
from scripts import active_identity_v2 as identity
from scripts import active_runtime_v1 as builder

HOOKS = {
    'stockfish_core/yaogu/strategy_experiments.py': ('source_identity', 'EXPERIMENTS'),
    'stockfish_core/yaogu/short_horizon_strength.py': ('short_horizon_source_identity', 'R1'),
    'stockfish_core/yaogu/short_horizon_participation.py': ('short_horizon_source_identity', 'R2'),
}


def hook(raw, function, kind):
    tree = ast.parse(raw)
    matched = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            returns = [child for child in ast.walk(node) if isinstance(child, ast.Return)]
            assert len(returns) == 1
            child = returns[0]
            child.value = ast.Call(func=ast.Name(id='_bind_active_identity', ctx=ast.Load()),
                args=[child.value], keywords=[ast.keyword(arg='root', value=ast.Name(id='ROOT', ctx=ast.Load())),
                    ast.keyword(arg='kind', value=ast.Constant(value=kind))])
            matched += 1
    assert matched == 1
    index = 1 if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str) else 0
    while index < len(tree.body) and isinstance(tree.body[index], ast.ImportFrom) and tree.body[index].module == '__future__':
        index += 1
    tree.body.insert(index, ast.parse('from scripts.active_identity_v2 import bind_identity as _bind_active_identity').body[0])
    ast.fix_missing_locations(tree)
    return (ast.unparse(tree) + '\n').encode()


@pytest.fixture
def source(tmp_path):
    root = (tmp_path / 'TEST_FIXTURE_identity_generated_source').resolve()
    root.mkdir()
    manifest = json.loads(builder.BASE_MANIFEST.read_bytes())
    publication = builder.ROOT.parents[1]/'publish'/builder.ROOT.parent.name/'stock-fish'
    files = {}
    for row in manifest['files']:
        raw = None
        for base in (builder.ROOT, publication):
            path = base / row['path']
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']:
                raw = path.read_bytes()
                break
        assert raw is not None, 'TEST_FIXTURE exact frozen preimage unavailable: ' + row['path']
        if row['path'] in HOOKS:
            raw = hook(raw, *HOOKS[row['path']])
        files[row['path']] = raw
    files['scripts/active_identity_v2.py'] = Path(identity.__file__).read_bytes()
    for name, raw in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    update_manifest(root, files)
    return root, files


def update_manifest(root, files):
    (root/'ACTIVE_RUNTIME_MANIFEST.json').write_text(json.dumps({
        'schema': 'ACTIVE_RUNTIME_V1', 'TEST_FIXTURE': True,
        'output_sha256': {p: identity.sha(raw) for p, raw in files.items()},
        'component_identities': identity.component_identities(files)}, sort_keys=True))


def execute(root):
    script = """
import json, sys
sys.path.insert(0,sys.argv[1])
from stockfish_core.yaogu import short_horizon_strength as r1
from stockfish_core.yaogu import short_horizon_participation as r2
from stockfish_core.yaogu import strategy_experiments as experiments
result = {'R1': r1.short_horizon_source_identity(), 'R2': r2.short_horizon_source_identity(),
          'EXPERIMENTS': experiments.source_identity(),
          'numeric_result': r1.compare(1,1,'TEST_FIXTURE')['status'],
          'ret1_min': r1.CONFIG['stages']['STRONG_START']['ret1_min']}
print(json.dumps(result,sort_keys=True))
"""
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(root)],
        cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('name', ['scripts/codex_research.py', 'stockfish_core/storage.py',
    'stockfish_core/yaogu/premarket_presentation_v1.py'])
def test_actual_return_hooks_separate_display_and_registration_from_strategy(source, name):
    root, files = source
    before = execute(root)
    files[name] += b'\n# TEST_FIXTURE display/registration-only change\n'
    (root/name).write_bytes(files[name])
    update_manifest(root, files)
    after = execute(root)
    for kind in ('EXPERIMENTS', 'R1', 'R2'):
        assert before[kind]['source_hash'] == after[kind]['source_hash']
        assert before[kind]['execution_identity'] != after[kind]['execution_identity']
        assert after[kind]['identity_contract'] == identity.VERSION
    if name != 'stockfish_core/yaogu/premarket_presentation_v1.py':
        assert before['R1']['legacy_execution_identity']['source_hash'] != after['R1']['legacy_execution_identity']['source_hash']
    assert after['numeric_result'] == before['numeric_result'] == 'PASS'


@pytest.mark.parametrize('mutation', ['numeric', 'config', 'visibility'])
def test_real_numeric_config_and_visibility_changes_alter_semantic_identity(source, mutation):
    root, files = source
    before = execute(root)
    if mutation == 'numeric':
        name = 'stockfish_core/yaogu/short_horizon_strength.py'
        assert b'value >= threshold' in files[name]
        files[name] = files[name].replace(b'value >= threshold', b'value > threshold', 1)
    elif mutation == 'config':
        name = 'config/short_horizon_strength_r1.yaml'
        assert b"ret1_min: '0.06'" in files[name]
        files[name] = files[name].replace(b"ret1_min: '0.06'", b"ret1_min: '0.07'", 1)
    else:
        name = 'stockfish_core/yaogu/visibility.py'
        files[name] += b'\n# TEST_FIXTURE evidence semantics change\n'
    (root/name).write_bytes(files[name])
    update_manifest(root, files)
    after = execute(root)
    assert after['R1']['source_hash'] != before['R1']['source_hash']
    assert after['R2']['source_hash'] != before['R2']['source_hash']
    if mutation == 'numeric':
        assert before['numeric_result'] == 'PASS' and after['numeric_result'] == 'FAIL'
    if mutation == 'config':
        assert before['ret1_min'] == '0.06' and after['ret1_min'] == '0.07'


def test_r2_keeps_r1_semantic_subset_and_actual_full_legacy_receipt(source):
    root, _ = source
    result = execute(root)
    r1, r2 = result['R1'], result['R2']
    assert r1['source_file_hashes'].items() <= r2['source_file_hashes'].items()
    for item in (r1, r2):
        assert item['source_hash'] == identity.digest(item['source_file_hashes'])
        legacy = item['legacy_execution_identity']
        assert legacy['source_hash'] == identity.digest(legacy['source_file_hashes'])
        # R2 consumes the already wrapped R1 return. Its unchanged function
        # retains R1's complete pre-split return in the nested base identity.
        preserved = legacy if item['semantic_identity_kind'] == 'R1' else legacy['base_r1_source_identity']['legacy_execution_identity']
        assert 'stockfish_core/storage.py' in preserved['source_file_hashes']
        assert 'stockfish_core/storage.py' not in item['source_file_hashes']
        assert 'scripts/codex_research.py' not in item['source_file_hashes']


def test_unclassified_dependency_and_changed_unsealed_semantics_are_rejected(source):
    root, files = source
    changed = dict(files)
    changed['stockfish_core/yaogu/TEST_FIXTURE_new_factor.py'] = b'FACTOR=2\n'
    with pytest.raises(ValueError, match='UNCLASSIFIED_DEPENDENCY'):
        identity.component_identities(changed)
    original = execute(root)['R1']['legacy_execution_identity']
    target = root/'stockfish_core/yaogu/visibility.py'
    target.write_bytes(target.read_bytes()+b'\n# TEST_FIXTURE unsealed change\n')
    with pytest.raises(ValueError, match='SEMANTIC_BYTES_CHANGED'):
        identity.bind_identity(original, root=root, kind='R1')


def test_missing_formal_spec_cannot_shrink_identity(source):
    root, files = source
    original = execute(root)['R1']['legacy_execution_identity']
    del files['docs/SHORT_HORIZON_STRENGTH_R1.md']
    update_manifest(root, files)
    with pytest.raises(ValueError, match='SEMANTIC_DEPENDENCY_MISSING'):
        identity.bind_identity(original, root=root, kind='R1')


def test_declared_new_extensions_are_execution_only():
    files = {name: b'# TEST_FIXTURE\n' for name in identity.EXTENSIONS}
    parts = identity.component_identities(files)
    assert parts['strategy']['files'] == []
    assert set(parts['execution']['files']) == identity.EXTENSIONS
