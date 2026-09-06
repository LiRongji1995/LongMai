"""E02 regression: TEST_FIXTURE files only, no active dataset writes."""
import importlib.util
import struct
from pathlib import Path
import pytest

def runner():
    spec = importlib.util.spec_from_file_location('r02_data_runner', Path(__file__).parents[2]/'qlib-zh/data_runner.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def dataset(root, values=(10., 11.), missing_symbol=False):
    for name in ['calendars','instruments','features/sz000001']:
        (root/name).mkdir(parents=True, exist_ok=True)
    (root/'calendars/day.txt').write_text('2026-09-03\n2026-09-04\n')
    universe='SZ000001 2026-09-03 2026-09-04\n'
    if missing_symbol: universe+='SH600000 2026-09-03 2026-09-04\n'
    for name in ['all','csi300']:
        (root/f'instruments/{name}.txt').write_text(universe)
    (root/'features/sz000001/close.day.bin').write_bytes(struct.pack('<fff',0.,*values))
    return root

@pytest.mark.parametrize('kind',['all_nan','missing_symbol'])
def test_e02_invalid_consumer_data_is_rejected(tmp_path,kind):
    root=dataset(tmp_path, values=(float('nan'),float('nan')) if kind=='all_nan' else (10.,11.), missing_symbol=kind=='missing_symbol')
    with pytest.raises(ValueError): runner()._verify_data_dir(data_dir=root)

def test_declared_fields_and_legitimate_missing(tmp_path):
    from stockfish_core.data_validation import ConsumerDataContract,validate_qlib_consumer
    root=dataset(tmp_path,values=(float('nan'),11.))
    c=ConsumerDataContract(consumer='TEST_FIXTURE',instruments=('SZ000001',),start='2026-09-03',end='2026-09-04',required_fields=('close',),allowed_missing={'SZ000001':['2026-09-03']})
    assert validate_qlib_consumer(root,c)['activation_allowed']
    c=c.model_copy(update={'required_fields':('close','open')})
    assert not validate_qlib_consumer(root,c)['activation_allowed']

def test_consumer_unknown_fields_rejected():
    from stockfish_core.data_validation import ConsumerDataContract
    with pytest.raises(ValueError):ConsumerDataContract(consumer='TEST_FIXTURE',instruments=('SZ000001',),start='2026-09-03',end='2026-09-04',fake=True)
