"""Exact build-time patches for fresh, full-window factor captures.

No historical source is edited and no provider is called by this module. The
generated producers contain the reviewed helpers; they do not import scripts.
"""
from __future__ import annotations

import difflib
import hashlib

WORKER = "stockfish_core/yaogu/provider_requests_worker.py"
PARENT = "stockfish_core/yaogu/provider_requests.py"
ORIGINAL_WORKER_SHA256 = "5300a98b9a9feec102c87364b19772e949644def477f24a47643f78c594683ec"
ORIGINAL_PARENT_SHA256 = "8d765a88db5187387064b551d87695bb9a7e3be849bd14818933c8681b0c11f1"
FACTOR_CHECKPOINT_CONTRACT = "FACTOR_FULL_WINDOW_CHECKPOINT_V3_NO_RESUME"

# Shared, embedded byte-for-byte in both producers. Only stdlib dependencies.
HELPER = r'''
FACTOR_CHECKPOINT_CONTRACT = 'FACTOR_FULL_WINDOW_CHECKPOINT_V3_NO_RESUME'
_FACTOR_V3_MAX_BYTES = 16 * 1024 ** 2
_FACTOR_V3_FIELDS = {'code', 'dividOperateDate', 'foreAdjustFactor', 'backAdjustFactor', 'adjustFactor'}
_FACTOR_V3_FP = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')


def _factor_v3_need(value, reason):
    if not value:
        raise ValueError('FACTOR_V3_' + reason)


def _factor_v3_clock(value):
    _factor_v3_need(type(value) is str, 'AWARE_CLOCK_REQUIRED')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _factor_v3_need(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'AWARE_CLOCK_REQUIRED')
    return parsed


def _factor_v3_canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode('utf-8')


def _factor_v3_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _factor_v3_need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('FACTOR_V3_NONFINITE_JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _factor_v3_fp(value):
    return tuple(getattr(value, key, 0) for key in _FACTOR_V3_FP)


def _factor_v3_directory(path):
    import stat
    path = Path(path).absolute()
    _factor_v3_need('..' not in path.parts, 'CANONICAL_DIRECTORY_REQUIRED')
    _factor_v3_need(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'SAFE_DIRECTORY_API_REQUIRED')
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identities = []
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            _factor_v3_need(stat.S_ISDIR(info.st_mode), 'DIRECTORY_REQUIRED')
            identities.append((info.st_dev, info.st_ino, info.st_mode))
        return fd, tuple(identities)
    except BaseException:
        os.close(fd)
        raise


def _factor_v3_check_directory(path, identities):
    fd, current = _factor_v3_directory(path)
    try:
        _factor_v3_need(current == identities, 'DIRECTORY_CHANGED')
    finally:
        os.close(fd)


def _factor_v3_read(path):
    import stat
    path = Path(path).absolute()
    directory, identities = _factor_v3_directory(path.parent)
    try:
        initial = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        _factor_v3_need(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1 and
            not getattr(initial, 'st_flags', 0) & 0x40000000, 'CHECKPOINT_REGULAR_RESIDENT_SINGLE_LINK_REQUIRED')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            _factor_v3_need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                not getattr(before, 'st_flags', 0) & 0x40000000 and
                _factor_v3_fp(initial) == _factor_v3_fp(before) and before.st_size <= _FACTOR_V3_MAX_BYTES,
                            'CHECKPOINT_REGULAR_BOUNDED_FILE_REQUIRED')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(_FACTOR_V3_MAX_BYTES + 1)
            _factor_v3_need(len(raw) == before.st_size and _factor_v3_fp(os.fstat(fd)) == _factor_v3_fp(before),
                            'CHECKPOINT_CHANGED_DURING_READ')
            _factor_v3_need(_factor_v3_fp(os.stat(path.name, dir_fd=directory, follow_symlinks=False)) == _factor_v3_fp(before),
                            'CHECKPOINT_PATH_CHANGED')
        finally:
            os.close(fd)
        _factor_v3_check_directory(path.parent, identities)
        return raw, dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), fp=list(_factor_v3_fp(before)))
    finally:
        os.close(directory)


def _factor_v3_binding(binding, params=None):
    import re
    _factor_v3_need(type(binding) is dict and set(binding) == {
        'version', 'attempt_id', 'request_started_at', 'endpoint', 'parameters', 'producer_hashes'}, 'BINDING_FIELDS')
    _factor_v3_need(binding['version'] == FACTOR_CHECKPOINT_CONTRACT and binding['endpoint'] == 'adjustment_factors',
                    'BINDING_PROTOCOL')
    _factor_v3_need(type(binding['attempt_id']) is str and re.fullmatch(r'[0-9a-f]{32}', binding['attempt_id']),
                    'ATTEMPT_ID')
    parameters = binding['parameters']
    _factor_v3_need(type(parameters) is dict and set(parameters) == {'codes', 'start_date', 'end_date'}, 'PARAMETER_FIELDS')
    _factor_v3_need(params is None or parameters == params, 'BINDING_PARAMETERS_DIFFER')
    codes = parameters['codes']
    _factor_v3_need(type(codes) is list and 1 <= len(codes) <= 100 and all(type(c) is str and
        re.fullmatch(r'(?:sh|sz|bj)\.[0-9]{6}', c) for c in codes) and len(set(codes)) == len(codes), 'REQUEST_CODES')
    _factor_v3_need(parameters['start_date'] == '1990-01-01' and type(parameters['end_date']) is str and
        date.fromisoformat(parameters['end_date']).isoformat() == parameters['end_date'] and
        parameters['end_date'] >= parameters['start_date'], 'FULL_1990_WINDOW_REQUIRED')
    hashes = binding['producer_hashes']
    _factor_v3_need(type(hashes) is dict and set(hashes) == {
        'provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py'} and
        all(type(v) is str and re.fullmatch(r'[0-9a-f]{64}', v) for v in hashes.values()), 'PRODUCER_HASHES')
    _factor_v3_need(_factor_v3_clock(binding['request_started_at']) <= datetime.now(timezone.utc), 'START_CLOCK_IN_FUTURE')
    return parameters


def _factor_v3_producers(binding):
    references = {}
    for name, expected in binding['producer_hashes'].items():
        raw, reference = _factor_v3_read(Path(__file__).with_name(name))
        _factor_v3_need(hashlib.sha256(raw).hexdigest() == expected, 'PRODUCER_SOURCE_CHANGED:' + name)
        references[name] = reference
    return references


def _factor_v3_claim_value(path, binding):
    return dict(contract=FACTOR_CHECKPOINT_CONTRACT, checkpoint_name=Path(path).name, binding=binding)


def _factor_v3_check_claim(path, binding):
    raw, reference = _factor_v3_read(Path(path).parent / '.factor-v3-attempt.json')
    _factor_v3_need(_factor_v3_json(raw) == _factor_v3_claim_value(path, binding), 'ATTEMPT_CLAIM_BINDING_MISMATCH')
    return reference


def _factor_v3_rows(rows, params, code=None):
    from decimal import Decimal, InvalidOperation
    _factor_v3_need(type(rows) is list and len(rows) <= 30000 * len(params['codes']), 'ROW_BOUND')
    seen, counts = set(), {}
    for row in rows:
        _factor_v3_need(type(row) is dict and set(row) == _FACTOR_V3_FIELDS, 'ROW_FIELDS')
        current = row['code']
        _factor_v3_need(current in params['codes'] and (code is None or current == code), 'ROW_SECURITY_MISMATCH')
        day = row['dividOperateDate']
        _factor_v3_need(type(day) is str and date.fromisoformat(day).isoformat() == day and
            params['start_date'] <= day <= params['end_date'], 'ROW_DATE_OUTSIDE_WINDOW')
        _factor_v3_need((current, day) not in seen, 'DUPLICATE_EVENT_DATE')
        seen.add((current, day))
        for field in ('backAdjustFactor', 'foreAdjustFactor', 'adjustFactor'):
            _factor_v3_need(type(row[field]) is str and 0 < len(row[field]) <= 128, 'NUMERIC_STRING_REQUIRED')
            try:
                value = Decimal(row[field])
            except InvalidOperation:
                raise ValueError('FACTOR_V3_INVALID_FACTOR') from None
            _factor_v3_need(value.is_finite() and value > 0, 'POSITIVE_FINITE_FACTOR_REQUIRED')
        counts[current] = counts.get(current, 0) + 1
        _factor_v3_need(counts[current] <= 30000, 'PER_SECURITY_ROW_BOUND')
    return counts


def _factor_v3_validate(value, binding, require_hash=False):
    params = _factor_v3_binding(binding)
    _factor_v3_need(type(value) is dict, 'SNAPSHOT_OBJECT_REQUIRED')
    if require_hash:
        declared = value.get('checkpoint_hash')
        body = {k: v for k, v in value.items() if k != 'checkpoint_hash'}
        _factor_v3_need(type(declared) is str and hashlib.sha256(_factor_v3_canonical(body)).hexdigest() == declared,
                        'CHECKPOINT_HASH_MISMATCH')
    _factor_v3_need(value.get('checkpoint_contract') == FACTOR_CHECKPOINT_CONTRACT and
        value.get('checkpoint_binding') == binding, 'SNAPSHOT_BINDING_MISMATCH')
    _factor_v3_need(value.get('status') in ('PARTIAL', 'COMPLETE') and type(value.get('pagination_complete')) is bool,
                    'SNAPSHOT_STATUS')
    _factor_v3_need(type(value.get('field_version')) is str and value['field_version'].startswith('BAOSTOCK_') and
        value.get('source_url') == 'https://baostock.com/' and value.get('http_responses') == [], 'SNAPSHOT_SOURCE')
    start = _factor_v3_clock(binding['request_started_at'])
    observed = _factor_v3_clock(value.get('first_seen_at'))
    _factor_v3_need(start <= observed <= datetime.now(timezone.utc), 'SNAPSHOT_CLOCK_ORDER')
    counts = _factor_v3_rows(value.get('rows'), params)
    coverage = value.get('coverage')
    _factor_v3_need(type(coverage) is dict and coverage.get('requested_codes') == params['codes'] and
        type(coverage.get('returned_rows')) is int and coverage['returned_rows'] == len(value['rows']) and
        coverage.get('returned_codes') == sorted(counts), 'SNAPSHOT_COVERAGE')
    statuses = coverage.get('security_statuses')
    _factor_v3_need(type(statuses) is list and len(statuses) == len(params['codes']) and
        all(type(s) is dict for s in statuses) and [s.get('code') for s in statuses] == params['codes'], 'ONE_STATUS_PER_CODE')
    completed = []
    for status in statuses:
        name, current = status.get('status'), status['code']
        _factor_v3_need(name in ('COMPLETE', 'FETCH_FAILED', 'UNKNOWN'), 'SECURITY_STATUS')
        _factor_v3_need(type(status.get('row_count')) is int and status['row_count'] == counts.get(current, 0), 'SECURITY_ROW_COUNT')
        if name == 'COMPLETE':
            _factor_v3_need(set(status) == {'code', 'status', 'row_count', 'cursor_exhausted', 'captured_at'} and
                status.get('cursor_exhausted') is True and
                start <= _factor_v3_clock(status.get('captured_at')) <= observed, 'COMPLETE_CURSOR_OR_CLOCK')
            completed.append(current)
        else:
            _factor_v3_need(status.get('cursor_exhausted') is False and status['row_count'] == 0 and
                'captured_at' not in status, 'INCOMPLETE_HAS_ROWS_OR_COMPLETION_CLOCK')
            if name == 'UNKNOWN':
                _factor_v3_need(set(status) == {'code', 'status', 'row_count', 'cursor_exhausted', 'reason'} and
                    status['reason'] == 'NOT_COMPLETED_BEFORE_CHECKPOINT', 'UNKNOWN_HAS_FALSE_EVIDENCE')
            else:
                _factor_v3_need(start <= _factor_v3_clock(status.get('failed_at')) <= observed and
                    type(status.get('error_code')) is str, 'FAILURE_CLOCK_OR_CODE')
    full = len(completed) == len(params['codes'])
    _factor_v3_need(value['pagination_complete'] is (value['status'] == 'COMPLETE') and
        coverage.get('cursor_exhausted') is value['pagination_complete'] and
        (value['status'] != 'COMPLETE' or full), 'INCOMPLETE_PARENT_CANNOT_BE_COMPLETE')
    if value['status'] == 'COMPLETE':
        _factor_v3_need(not value.get('gaps') and value.get('error_code') is None and
            value.get('failure_category') is None, 'COMPLETE_PARENT_HAS_FAILURE')
    return value

'''

WORKER_HELPER = r'''
def _factor_v3_write(path, value, previous, claim):
    import uuid
    path = Path(path).absolute()
    directory, identities = _factor_v3_directory(path.parent)
    temporary = path.name + '.' + uuid.uuid4().hex + '.tmp'
    fd = None
    try:
        _factor_v3_need(_factor_v3_check_claim(path, value['checkpoint_binding']) == claim, 'ATTEMPT_CLAIM_CHANGED')
        if previous is None:
            try:
                os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('FACTOR_V3_EXISTING_CHECKPOINT_FORBIDDEN')
        else:
            _factor_v3_need(_factor_v3_read(path)[1] == previous, 'OWNED_CHECKPOINT_CHANGED')
        body = dict(value)
        body['checkpoint_hash'] = hashlib.sha256(_factor_v3_canonical(value)).hexdigest()
        raw = _factor_v3_canonical(body)
        _factor_v3_need(len(raw) <= _FACTOR_V3_MAX_BYTES, 'CHECKPOINT_BYTE_BOUND')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        _factor_v3_check_directory(path.parent, identities)
        if previous is not None:
            _factor_v3_need(_factor_v3_read(path)[1] == previous, 'OWNED_CHECKPOINT_CHANGED')
        else:
            try:
                os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('FACTOR_V3_EXISTING_CHECKPOINT_FORBIDDEN')
        _factor_v3_need(_factor_v3_check_claim(path, value['checkpoint_binding']) == claim, 'ATTEMPT_CLAIM_CHANGED')
        if previous is None:
            # Atomic no-overwrite first publication; never clobber another writer.
            os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.unlink(temporary, dir_fd=directory)
        else:
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
        written, reference = _factor_v3_read(path)
        _factor_v3_need(written == raw and reference['fp'] == list(_factor_v3_fp(os.fstat(fd))), 'CHECKPOINT_WRITE_CHANGED')
        return reference
    finally:
        if fd is not None:
            os.close(fd)
        # Retain any unfinished temporary file as failure evidence.
        os.close(directory)


def _factor_v3_cursor(cursor, code, params):
    _factor_v3_need(cursor.error_code == '0', 'SDK_QUERY_ERROR:' + str(cursor.error_code))
    fields = list(cursor.fields)
    _factor_v3_need(len(fields) == len(_FACTOR_V3_FIELDS) and set(fields) == _FACTOR_V3_FIELDS, 'CURSOR_FIELDS')
    rows = []
    while cursor.next():
        _factor_v3_need(cursor.error_code == '0', 'SDK_CURSOR_ERROR:' + str(cursor.error_code))
        _factor_v3_need(len(rows) < 30000, 'PER_SECURITY_ROW_BOUND')
        data = cursor.get_row_data()
        _factor_v3_need(type(data) in (list, tuple) and len(data) == len(fields), 'CURSOR_ROW_WIDTH')
        rows.append(dict(zip(fields, data)))
    _factor_v3_need(cursor.error_code == '0', 'SDK_CURSOR_NOT_EXHAUSTED:' + str(cursor.error_code))
    _factor_v3_rows(rows, params, code)
    return rows


def _factor_v3_request(params, partial_path):
    # Guard before importing SDK, login, or any provider call. No resume path.
    _factor_v3_need(partial_path is not None, 'FRESH_CHECKPOINT_PATH_REQUIRED')
    _factor_v3_need(not os.path.lexists(partial_path), 'EXISTING_CHECKPOINT_FORBIDDEN')
    binding = _transport.get('factor_capture_binding')
    _factor_v3_binding(binding, params)
    producers = _factor_v3_producers(binding)
    claim = _factor_v3_check_claim(partial_path, binding)
    directory, _identities = _factor_v3_directory(Path(partial_path).absolute().parent)
    os.close(directory)
    import baostock as bs
    rows, statuses, previous, snapshot = [], {}, None, None

    def checkpoint():
        nonlocal previous, snapshot
        all_statuses = [statuses.get(code, dict(code=code, status='UNKNOWN', row_count=0,
            cursor_exhausted=False, reason='NOT_COMPLETED_BEFORE_CHECKPOINT')) for code in params['codes']]
        value = dict(status='PARTIAL', rows=list(rows), pagination_complete=False,
            checkpoint_contract=FACTOR_CHECKPOINT_CONTRACT,
            checkpoint_binding=_factor_v3_json(_factor_v3_canonical(binding)),
            field_version=baostock_field_version(bs), source_url='https://baostock.com/',
            first_seen_at=datetime.now(timezone.utc).isoformat(), http_responses=[],
            coverage=dict(requested_codes=list(params['codes']), returned_rows=len(rows),
                returned_codes=sorted({row['code'] for row in rows}), cursor_exhausted=False,
                security_statuses=all_statuses, sdk_operations_at_checkpoint=_attempt_counts['SDK']))
        _factor_v3_validate(value, binding)
        previous = _factor_v3_write(partial_path, value, previous, claim)
        snapshot = value

    reserve_attempt('SDK')
    _factor_v3_need(bs.login().error_code == '0', 'SDK_LOGIN_FAILED')
    try:
        for code in params['codes']:
            # Admission and checkpoint I/O failures stop; no SDK operation is retried.
            reserve_attempt('SDK')
            try:
                part = _factor_v3_cursor(bs.query_adjust_factor(code=code,
                    start_date=params['start_date'], end_date=params['end_date']), code, params)
                captured = datetime.now(timezone.utc).isoformat()
                rows.extend(part)
                statuses[code] = dict(code=code, status='COMPLETE', row_count=len(part),
                                      cursor_exhausted=True, captured_at=captured)
            except OSError:
                raise
            except Exception as exc:
                # Preserve actual SDK error code; malformed cursors are failed sources.
                category, error, classes = classify_error(exc)
                if category == 'BUDGET':
                    raise
                if str(exc).startswith('FACTOR_V3_SDK_QUERY_ERROR:'):
                    category, error = 'PROVIDER', 'BAOSTOCK_ERROR_' + str(exc).split(':', 1)[1]
                statuses[code] = dict(code=code, status='FETCH_FAILED', row_count=0,
                    cursor_exhausted=False, failed_at=datetime.now(timezone.utc).isoformat(),
                    error_code=error, failure_category=category, exception_classes=classes)
            checkpoint()
    finally:
        # A cleanup failure propagates. Parent can recover the last durable snapshot.
        reserve_attempt('SDK')
        cleanup = bs.logout()
        _factor_v3_need(getattr(cleanup, 'error_code', '0') == '0', 'SDK_LOGOUT_FAILED')
    _factor_v3_need(_factor_v3_producers(binding) == producers, 'PRODUCER_TERMINAL_CHANGED')
    _factor_v3_need(_factor_v3_check_claim(partial_path, binding) == claim, 'ATTEMPT_CLAIM_CHANGED')
    _factor_v3_need(snapshot is not None, 'SNAPSHOT_MISSING')
    result = _factor_v3_json(_factor_v3_canonical(snapshot))
    failed = [s for s in result['coverage']['security_statuses'] if s['status'] != 'COMPLETE']
    result.update(status='PARTIAL' if failed else 'COMPLETE', pagination_complete=not failed,
        gaps=[dict(code=s['code'], status=s['status'], error_code=s.get('error_code')) for s in failed],
        failure_category=failed[0].get('failure_category') if failed else None,
        error_code='BATCH_SECURITY_FAILURES' if failed else None,
        diagnostics=dict(public_sdk_session=True, factor_capture_contract=FACTOR_CHECKPOINT_CONTRACT))
    result['coverage']['cursor_exhausted'] = not failed
    _factor_v3_validate(result, binding)
    return result

'''

PARENT_HELPER = r'''
def _factor_v3_new_binding(params, hashes, started):
    import uuid
    binding = dict(version=FACTOR_CHECKPOINT_CONTRACT, attempt_id=uuid.uuid4().hex,
        request_started_at=started.isoformat(), endpoint='adjustment_factors',
        parameters=_factor_v3_json(_factor_v3_canonical(params)), producer_hashes=dict(hashes))
    _factor_v3_binding(binding, params)
    return binding


def _factor_v3_claim(path, binding):
    path = Path(path).absolute()
    directory, identities = _factor_v3_directory(path.parent)
    name = '.factor-v3-attempt.json'
    fd = None
    try:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        except FileExistsError:
            raise ValueError('FACTOR_V3_EXISTING_ATTEMPT_CLAIM_NO_RETRY') from None
        raw = _factor_v3_canonical(_factor_v3_claim_value(path, binding))
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
        os.fsync(directory)
        _factor_v3_check_directory(path.parent, identities)
        reference = _factor_v3_check_claim(path, binding)
        _factor_v3_need(reference['fp'] == list(_factor_v3_fp(os.fstat(fd))), 'ATTEMPT_CLAIM_WRITE_CHANGED')
        return reference
    finally:
        if fd is not None:
            os.close(fd)
        # Failed or interrupted claims are intentionally retained.
        os.close(directory)


def _factor_v3_recover(path, binding, error_code):
    claim = _factor_v3_check_claim(path, binding)
    try:
        raw, reference = _factor_v3_read(path)
    except FileNotFoundError:
        return None
    value = _factor_v3_json(raw)
    _factor_v3_validate(value, binding, require_hash=True)
    _factor_v3_need(value['status'] == 'PARTIAL' and value['pagination_complete'] is False,
                    'CHECKPOINT_CANNOT_DECLARE_PARENT_COMPLETE')
    _factor_v3_need(_factor_v3_read(path) == (raw, reference), 'CHECKPOINT_TERMINAL_CHANGED')
    _factor_v3_need(_factor_v3_check_claim(path, binding) == claim, 'ATTEMPT_CLAIM_CHANGED')
    result = {k: v for k, v in value.items() if k != 'checkpoint_hash'}
    result.update(status='PARTIAL', failure_category='NETWORK' if error_code.startswith('REQUEST_TIMEOUT') else 'IMPLEMENTATION',
        error_code=error_code, pagination_complete=False, gaps=value.get('gaps', []) + [error_code],
        diagnostics=dict(factor_capture_contract=FACTOR_CHECKPOINT_CONTRACT,
            recovered_current_attempt_only=True, checkpoint_reference=reference,
            complete_parent_asserted=False, actual_request_attempt_counts='UNKNOWN_AFTER_INTERRUPTION'))
    return result


def _factor_v3_accept(value, path, binding):
    claim = _factor_v3_check_claim(path, binding)
    _factor_v3_validate(value, binding)
    raw, reference = _factor_v3_read(path)
    snapshot = _factor_v3_json(raw)
    _factor_v3_validate(snapshot, binding, require_hash=True)
    for key in ('rows', 'first_seen_at', 'checkpoint_binding', 'field_version'):
        _factor_v3_need(value[key] == snapshot[key], 'FINAL_RESULT_CHECKPOINT_DIFFERS:' + key)
    _factor_v3_need(value['coverage']['security_statuses'] == snapshot['coverage']['security_statuses'],
                    'FINAL_RESULT_SECURITY_STATUS_DIFFERS')
    _factor_v3_need(snapshot['status'] == 'PARTIAL' and snapshot['pagination_complete'] is False,
                    'CHECKPOINT_CANNOT_DECLARE_PARENT_COMPLETE')
    _factor_v3_need(_factor_v3_read(path) == (raw, reference), 'CHECKPOINT_TERMINAL_CHANGED')
    _factor_v3_need(_factor_v3_check_claim(path, binding) == claim, 'ATTEMPT_CLAIM_CHANGED')
    value['diagnostics'] = dict(value.get('diagnostics', {}), checkpoint_reference=reference)
    return value


def _factor_v3_terminal(path, binding, producers, claim, result):
    _factor_v3_need(_factor_v3_producers(binding) == producers, 'PRODUCER_TERMINAL_CHANGED')
    _factor_v3_need(_factor_v3_check_claim(path, binding) == claim, 'ATTEMPT_CLAIM_CHANGED')
    reference = result.get('diagnostics', {}).get('checkpoint_reference')
    if reference is None:
        _factor_v3_need(not os.path.lexists(path), 'UNVERIFIED_CHECKPOINT_APPEARED')
    else:
        _factor_v3_need(_factor_v3_read(path)[1] == reference, 'CHECKPOINT_TERMINAL_CHANGED')

'''


def _replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError("FACTOR_V3_EXACT_PATCH_FRAGMENT_MISMATCH")
    return text.replace(old, new, 1)


def _patch(raw, expected, filename, replacements):
    if type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("FACTOR_V3_ORIGINAL_PRODUCER_SHA_MISMATCH:" + filename)
    source = raw.decode("utf-8")
    changed = source
    for old, new in replacements:
        changed = _replace(changed, old, new)
    compile(changed, filename, "exec")
    patch = "".join(difflib.unified_diff(source.splitlines(True), changed.splitlines(True),
        fromfile="original/" + filename, tofile="v3/" + filename)).encode("utf-8")
    return changed.encode("utf-8"), patch


def patch_worker(raw):
    return _patch(raw, ORIGINAL_WORKER_SHA256, WORKER, (
        ("\ndef _baostock_session_request(endpoint,params,partial_path=None):\n    import baostock as bs\n",
         HELPER + WORKER_HELPER + "\ndef _baostock_session_request(endpoint,params,partial_path=None):\n"
         "    if endpoint == 'adjustment_factors':\n"
         "        return _factor_v3_request(params, partial_path)\n"
         "    import baostock as bs\n"),
        ("    result['http_responses']=captured;result['first_seen_at']=datetime.now(timezone.utc).isoformat()\n",
         "    result['http_responses']=captured\n"
         "    if request['endpoint'] == 'adjustment_factors':\n"
         "        result.setdefault('first_seen_at', datetime.now(timezone.utc).isoformat())\n"
         "    else:\n"
         "        result['first_seen_at'] = datetime.now(timezone.utc).isoformat()\n"),
    ))


def patch_parent(raw):
    return _patch(raw, ORIGINAL_PARENT_SHA256, PARENT, (
        ("\ndef _partial_timeout_result(partial_path):\n", HELPER + PARENT_HELPER + "\ndef _partial_timeout_result(partial_path):\n"),
        ("    partial_path.parent.mkdir(parents=True,exist_ok=True)\n",
         "    if endpoint == 'adjustment_factors' and os.path.lexists(partial_path):\n"
         "        raise ValueError('FACTOR_V3_EXISTING_CHECKPOINT_FORBIDDEN')\n"
         "    partial_path.parent.mkdir(parents=True,exist_ok=True)\n"),
        ("    project_root=str(Path(__file__).resolve().parents[2])\n",
         "    if endpoint == 'adjustment_factors':\n"
         "        request['factor_capture_binding'] = _factor_v3_new_binding(params, source_hashes, started)\n"
         "        factor_producers = _factor_v3_producers(request['factor_capture_binding'])\n"
         "        factor_claim = _factor_v3_claim(partial_path, request['factor_capture_binding'])\n"
         "    project_root=str(Path(__file__).resolve().parents[2])\n"),
        ("        if process.returncode or not isinstance(result,dict):raise ValueError('WORKER_INVALID_RESULT')\n",
         "        if process.returncode or not isinstance(result,dict):raise ValueError('WORKER_INVALID_RESULT')\n"
         "        if endpoint == 'adjustment_factors':\n"
         "            result = _factor_v3_accept(result, partial_path, request['factor_capture_binding'])\n"),
        ("        result=_partial_timeout_result(partial_path) or {'status':'FETCH_FAILED','failure_category':'NETWORK','error_code':'REQUEST_TIMEOUT','rows':[],'pagination_complete':False,'http_responses':[]}\n",
         "        result=(_factor_v3_recover(partial_path, request['factor_capture_binding'], 'REQUEST_TIMEOUT_AFTER_PARTIAL') if endpoint == 'adjustment_factors' else _partial_timeout_result(partial_path)) or {'status':'FETCH_FAILED','failure_category':'NETWORK','error_code':'REQUEST_TIMEOUT','rows':[],'pagination_complete':False,'http_responses':[]}\n"),
        ("        result=_partial_timeout_result(partial_path) or {'status':'FETCH_FAILED','failure_category':'IMPLEMENTATION','error_code':'WORKER_INVALID_RESULT','rows':[],'pagination_complete':False,'http_responses':[]}\n",
         "        result=(_factor_v3_recover(partial_path, request['factor_capture_binding'], 'WORKER_FAILED_AFTER_PARTIAL') if endpoint == 'adjustment_factors' else _partial_timeout_result(partial_path)) or {'status':'FETCH_FAILED','failure_category':'IMPLEMENTATION','error_code':'WORKER_INVALID_RESULT','rows':[],'pagination_complete':False,'http_responses':[]}\n"),
        ("    receipts=[]\n",
         "    if endpoint == 'adjustment_factors':\n"
         "        _factor_v3_terminal(partial_path, request['factor_capture_binding'], factor_producers, factor_claim, result)\n"
         "    receipts=[]\n"),
        ("    return answer\n",
         "    if endpoint == 'adjustment_factors':\n"
         "        _factor_v3_terminal(partial_path, request['factor_capture_binding'], factor_producers, factor_claim, result)\n"
         "    return answer\n"),
    ))
