"""Explicit fresh dated-identity input and late outcome consumer.

This input is a new observation source, never an amendment to a saved run or
an archive certificate. Every invocation authenticates all original archives.
Serialized manifests describe provenance; only this invocation's private,
store/session-bound proofs can supply a typed input to the worker.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import stat
import weakref

from ..common import utc_now, canonical_json
from ..jobs import JobService
from . import short_horizon_archive_bridge_v1 as bridge
from .contracts import MarketInput, UniverseSnapshot
from .dated_identity_composite_input_v1 import project_dated_identity
from .short_horizon_daily_closure_v1 import (
    LocalCopyLateCompletedSessionClosure, _InputCalendar, _credential_identity)
from .visibility import stamp

VERSION = 'DATED_IDENTITY_OUTCOME_INPUT_V1'
REGISTRY = 'dated_identity_outcome_inputs_v1'
SEALS = 'dated_identity_outcome_input_seals_v1'
_PROOFS = weakref.WeakKeyDictionary()


def _need(value, reason):
    if not value:
        raise ValueError('DATED_OUTCOME_' + reason)


def source_identity():
    directory = Path(__file__).parent
    names = ('dated_identity_outcome_closure_v1.py', 'dated_identity_composite_input_v1.py')
    return dict(version=VERSION, files={name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                      for name in names})


def _file_reference(path):
    path = Path(path)
    before = bridge._fp(path)
    _need(path.is_absolute() and path.resolve(strict=True) == path and not path.is_symlink()
          and stat.S_ISREG(before['st_mode']), 'REGULAR_PINNED_SEAL_OR_CODE_REQUIRED')
    checksum, total = hashlib.sha256(), 0
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                           | getattr(os, 'O_NONBLOCK', 0)), 'rb') as handle:
        _need({key: getattr(os.fstat(handle.fileno()), key, 0) for key in bridge._FP} == before,
              'OPEN_PIN_CHANGED')
        while block := handle.read(1024 * 1024):
            checksum.update(block)
            total += len(block)
        _need({key: getattr(os.fstat(handle.fileno()), key, 0) for key in bridge._FP} == before,
              'OPEN_PIN_CHANGED')
    _need(bridge._fp(path) == before and total == before['st_size'], 'PIN_CHANGED_DURING_FULL_READ')
    return dict(path=str(path), sha256=checksum.hexdigest(), bytes=total, fp=before)


def _cutoff_ns(cutoff):
    delta = stamp(cutoff).astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000


def _check_seal(service, descriptor, session, cutoff):
    saved = service.store.get_document(SEALS, descriptor['input_id'])
    _need(type(saved) is dict and set(saved) == {'schema_version', 'input_id', 'seal_reference'}
          and saved['schema_version'] == VERSION and saved['input_id'] == descriptor['input_id'],
          'COMPLETED_REGISTRATION_SEAL_REQUIRED')
    ref = bridge._ref(saved['seal_reference'])
    expected_path = service.root / 'dated_identity_inputs' / descriptor['input_id'] / 'registration-seal.json'
    _need(Path(ref['path']) == expected_path.absolute(), 'OWNED_REGISTRATION_SEAL_PATH_REQUIRED')
    _need(ref['fp']['st_ctime_ns'] <= _cutoff_ns(cutoff), 'REGISTRATION_PHYSICAL_PIN_AFTER_CUTOFF')
    seal = bridge._json(session.read(ref['path'], ref, limit=1024 * 1024))
    _need(set(seal) == {'schema_version', 'input_id', 'input_hash', 'descriptor_hash',
                       'registration_completed_at', 'source_identity', 'source_references'}
          and seal['schema_version'] == VERSION and seal['input_id'] == descriptor['input_id']
          and seal['input_hash'] == descriptor['input_hash']
          and seal['descriptor_hash'] == bridge.digest_json_stream(descriptor)
          and seal['source_identity'] == source_identity(), 'REGISTRATION_SEAL_BINDING_CHANGED')
    _need(stamp(descriptor['registered_at']) <= stamp(seal['registration_completed_at']) <= stamp(cutoff),
          'COMPLETED_REGISTRATION_AFTER_CUTOFF')
    expected_names = set(source_identity()['files'])
    _need(type(seal['source_references']) is dict and set(seal['source_references']) == expected_names,
          'COMPLETE_DERIVATION_SOURCE_REFERENCES_REQUIRED')
    for name, source in seal['source_references'].items():
        _need(Path(source['path']) == Path(__file__).with_name(name)
              and source['sha256'] == source_identity()['files'][name]
              and source['fp']['st_ctime_ns'] <= _cutoff_ns(cutoff), 'DERIVATION_SOURCE_PIN_AFTER_CUTOFF')
        session.read(source['path'], source, limit=1024 * 1024)
    return deepcopy(saved)


def _proof_key(service, session, input_id):
    _need(isinstance(session, bridge.ArchiveVerificationSession), 'GENUINE_SESSION_REQUIRED')
    return (id(service.store), canonical_json(bridge._view_store_identity(service)), input_id,
            canonical_json(session.source_protocol()))


def _guard_value(session, value, content_hash, *, input_id, parent_id, kind):
    session._borrowed_values[id(value)] = (value, content_hash, True)
    session._borrowed_identities[id(value)] = dict(kind=kind, document_id=input_id,
        content_hash=content_hash, bridge_id=parent_id, run_id='', input_id=input_id,
        source_protocol_context=session.source_protocol())


def _take_private_input(service, input_id, session):
    """Consume an unborrowed freshly normalized input without lending it.

    The old object remains internal until it is replaced by the derived value.
    No public caller receives an unchecked mutable parent. The session still
    guards every original file, database BLOB and source registration, and the
    new value gets its own complete canonical terminal guard.
    """
    view = bridge.authenticated_bridge_view(service, input_id=input_id, session=session,
                                            retain_input=True, include_archive_seal=True)
    key = bridge._verified_view_key(service, bridge.INPUT_BINDINGS, input_id, session)
    proof = session._verified_views[key]
    bridge._check_verified_view(service, proof, session)
    _need(proof.get('borrowed_input') is None, 'PARENT_ALREADY_BORROWED')
    value = proof.get('retained_input')
    bridge._inline_typed_input(value)
    _need(bridge.digest_market_input(value, checkpoint=session.check,
        encoding_profile=session.encoding_profile) == view['credential']['input_hash'], 'PARENT_TYPED_HASH')
    # Both binding aliases can point to separate small proof dicts sharing the
    # same typed value. Clear all such private references before releasing the
    # archive cache; never clear a public borrowed value or its terminal guard.
    for stored in session._verified_views.values():
        if stored.get('retained_input') is value:
            _need(stored.get('borrowed_input') is None, 'PARENT_ALREADY_BORROWED')
            stored['retained_input'] = None
    session.release_cached_archives()
    return value, view


def _identity_projections(service, input_ids, session):
    _need(type(input_ids) is list and input_ids and input_ids == sorted(set(input_ids))
          and all(type(value) is str and value for value in input_ids), 'EXPLICIT_IDENTITY_INPUTS')
    projections, credentials, seals = [], [], {}
    for input_id in input_ids:
        full, view = _take_private_input(service, input_id, session)
        day = full.observation_date
        securities = tuple(row for row in full.universe.securities if row.effective_from == day)
        # This is a private field projection of an already authenticated full
        # input, not a standalone source capability or an original input hash.
        universe = UniverseSnapshot.model_validate({
            **full.universe.model_dump(mode='python', exclude={'securities'}), 'securities': securities})
        projection = MarketInput(observation_date=day, sessions=full.sessions, universe=universe,
            bars=(), receipts=full.receipts, coverage_type=full.coverage_type)
        projections.append(projection)
        credentials.append(_credential_identity(view['credential']))
        seals[view['credential']['bridge_id']] = view['archive_sealed_at']
        # Protect every surviving projected field, including mutable receipt
        # parameters, until this invocation's final write transaction completes.
        digest = bridge.digest_market_input(projection, checkpoint=session.check,
                                            encoding_profile=session.encoding_profile)
        _guard_value(session, projection, digest, input_id='private_identity_' + digest,
                     parent_id=view['credential']['bridge_id'], kind='dated_identity_projection')
        del full, universe
        gc.collect()
        session.check()
    return tuple(projections), credentials, seals


def _reconstruct(service, descriptor, session, *, projections=None):
    _need(descriptor['source_identity'] == source_identity(), 'SOURCE_VERSION_CHANGED')
    if projections is None:
        projections, credentials, seals = _identity_projections(service, descriptor['identity_input_ids'], session)
    else:
        projections, credentials, seals = projections
    _need(credentials == descriptor['identity_credentials'], 'IDENTITY_PARENTS_CHANGED')
    base, view = _take_private_input(service, descriptor['base_input_id'], session)
    _need(_credential_identity(view['credential']) == descriptor['base_credential'], 'BASE_PARENT_CHANGED')
    bundle, manifest = project_dated_identity(base, projections,
        derived_at=stamp(descriptor['derived_at']), checkpoint=session.check)
    del base
    gc.collect()
    input_hash = bridge.digest_market_input(bundle, checkpoint=session.check,
                                            encoding_profile=session.encoding_profile)
    _need(input_hash == descriptor['input_hash'] and descriptor['input_id'] == 'market_' + input_hash,
          'FULL_DERIVED_INPUT_CHANGED')
    _need(manifest == descriptor['projection'], 'FIELD_LINEAGE_CHANGED')
    seals[view['credential']['bridge_id']] = view['archive_sealed_at']
    with service.store.transaction(write=False) as connection:
        _need(bridge._stored_matches(connection, 'candidate_inputs', descriptor['input_id'],
                                     input_hash, session), 'DERIVED_DATABASE_INPUT_CHANGED')
    _guard_value(session, bundle, input_hash, input_id=descriptor['input_id'],
                 parent_id=bridge.digest_json_stream(descriptor), kind='derived_input')
    return bundle, seals


def register_input(service, *, base_input_id, identity_input_ids, local_copy_manifest_ref,
                   verification_profile=None):
    """Append a newly derived input under the actual clock and existing job lock.

    There is deliberately no caller-supplied derived timestamp. Unchanged old
    inputs, run documents and bridge certificates remain in their namespaces.
    """
    from .short_horizon_resource_profiles_v1 import session_options
    _need(isinstance(service.jobs, JobService) and service.jobs.store.path == service.store.path
          and not service.jobs._closed and not service.jobs._service_lock.file.closed,
          'ACTIVE_MATCHING_JOB_LOCK_REQUIRED')
    protocol = bridge.configure_local_copy_source_protocol(service, local_copy_manifest_ref)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=deepcopy(local_copy_manifest_ref),
        encoding_profile=bridge.CLOSURE_ENCODING_PROFILE, **session_options(verification_profile))
    ids = sorted(set(identity_input_ids))
    _need(len(ids) == len(identity_input_ids), 'DUPLICATE_IDENTITY_INPUT')
    projections, identity_credentials, identity_seals = _identity_projections(service, ids, session)
    base, view = _take_private_input(service, base_input_id, session)
    derived_at = utc_now()
    bundle, manifest = project_dated_identity(base, projections, derived_at=derived_at, checkpoint=session.check)
    del base
    gc.collect()
    input_hash = bridge.digest_market_input(bundle, checkpoint=session.check,
                                            encoding_profile=session.encoding_profile)
    input_id = 'market_' + input_hash
    descriptor = dict(schema_version=VERSION, source_identity=source_identity(), input_id=input_id,
        input_hash=input_hash, base_input_id=base_input_id, identity_input_ids=ids,
        base_credential=_credential_identity(view['credential']), identity_credentials=identity_credentials,
        derived_at=derived_at.isoformat(), registered_at=utc_now().isoformat(),
        source_protocol=protocol, projection=manifest, original_input_mutated=False,
        original_run_mutated=False, historical_visibility_upgraded=False,
        market_edge='NOT_ESTABLISHED')
    _guard_value(session, bundle, input_hash, input_id=input_id,
                 parent_id=bridge.digest_json_stream(descriptor), kind='derived_input')
    session.terminal()
    with service.store.transaction() as connection:
        bridge._put_archived_document(connection, 'candidate_inputs', input_id, input_hash, session,
            typed_document=bundle, temporary_dir=service.store.path.parent)
        service.store._put_document(connection, 'candidate_input_indexes', input_id,
            dict(input_id=input_id, observation_date=str(bundle.observation_date),
                 coverage_type=bundle.coverage_type, max_session=str(max(bundle.sessions))))
        service.store._put_document(connection, REGISTRY, input_id, descriptor)
        from ..storage import checked_payload
        row = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                                 (REGISTRY, input_id)).fetchone()
        _need(row is not None and checked_payload(row) == descriptor, 'REGISTRATION_CHANGED')
        session.terminal()
    # A descriptor timestamp alone cannot prove the newly derived source was
    # available at a historical cutoff. Seal only AFTER the input transaction
    # committed. A failure between these stages leaves an unusable incomplete
    # registration, with both its files and database evidence preserved.
    seal_root = service.root / 'dated_identity_inputs' / input_id
    _need(seal_root.absolute().resolve(strict=False) == seal_root.absolute(), 'OWNED_UNALIASED_SEAL_ROOT_REQUIRED')
    seal_root.parent.mkdir(parents=True, exist_ok=True)
    seal_root.mkdir(exist_ok=False)
    path = seal_root / 'registration-seal.json'
    source_references = {name: _file_reference(Path(__file__).with_name(name))
                         for name in source_identity()['files']}
    _need(source_identity() == descriptor['source_identity'], 'SOURCE_CHANGED_DURING_REGISTRATION')
    for source in source_references.values():
        session.read(source['path'], source, limit=1024 * 1024)
    seal = dict(schema_version=VERSION, input_id=input_id, input_hash=input_hash,
        descriptor_hash=bridge.digest_json_stream(descriptor), source_identity=source_identity(),
        source_references=source_references, registration_completed_at=utc_now().isoformat())
    with path.open('x', encoding='utf-8') as handle:
        json.dump(seal, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name == 'posix':
        descriptor_fd = os.open(seal_root, os.O_RDONLY)
        try:
            os.fsync(descriptor_fd)
        finally:
            os.close(descriptor_fd)
    seal_document = dict(schema_version=VERSION, input_id=input_id, seal_reference=_file_reference(path))
    with service.store.transaction() as connection:
        service.store._put_document(connection, SEALS, input_id, seal_document)
        seal_row = connection.execute(
            'SELECT * FROM documents WHERE namespace=? AND document_id=?', (SEALS, input_id)).fetchone()
        _need(checked_payload(seal_row) == seal_document, 'REGISTRATION_SEAL_WRITE_CHANGED')
        session.read(str(path), seal_document['seal_reference'], limit=1024 * 1024)
        session.terminal()
    return dict(input_id=input_id, input_hash=input_hash, descriptor=descriptor,
                registration_seal=seal_document,
                archive_verification=session.audit())


class DatedIdentityLateClosure(LocalCopyLateCompletedSessionClosure):
    """Separate opt-in consumer; default daily/archive input paths stay strict."""
    version = 'DATED_IDENTITY_LATE_OUTCOME_CLOSURE_V1'
    job_kind = 'dated_identity_late_outcome_closure_v1'
    ledgers = 'dated_identity_late_outcome_ledgers_v1'
    job_prefix = 'dated_identity_late_'

    def _policy_identity(self):
        return {**super()._policy_identity(), 'version': self.version,
                'dated_input_source_identity': source_identity()}

    def _descriptor(self, input_id):
        value = self.service.store.get_document(REGISTRY, input_id)
        _need(type(value) is dict and value.get('schema_version') == VERSION
              and value.get('input_id') == input_id and value.get('source_identity') == source_identity()
              and value.get('source_protocol') == self.source_protocol,
              'EXPLICIT_REGISTERED_DERIVED_INPUT_REQUIRED')
        _need(stamp(value['derived_at']) <= stamp(value['registered_at']), 'DERIVATION_CLOCK_ORDER')
        return value

    def _authenticated(self, run_ids, input_id, session, checkpoint=lambda: None, *,
                       borrow_run=False, retain_input=False, sealed_cutoff=None):
        _need(sealed_cutoff is not None, 'EXPLICIT_ACTUAL_CUTOFF_REQUIRED')
        descriptor = self._descriptor(input_id)
        _need(stamp(descriptor['registered_at']) <= stamp(sealed_cutoff) <= stamp(self.clock()),
              'DERIVED_INPUT_NOT_VISIBLE_AT_CUTOFF')
        registration_seal = _check_seal(self.service, descriptor, session, sealed_cutoff)
        identities = _identity_projections(self.service, descriptor['identity_input_ids'], session)
        _need(identities[1] == descriptor['identity_credentials'], 'IDENTITY_PARENTS_CHANGED')
        runs, calendar, bindings = super()._authenticated(run_ids, descriptor['base_input_id'], session,
            checkpoint=checkpoint, borrow_run=borrow_run, retain_input=True, sealed_cutoff=sealed_cutoff)
        _need(bindings['outcome_input'] == descriptor['base_credential'], 'BASE_PARENT_CHANGED')
        for seal in identities[2].values():
            _need(stamp(seal) <= stamp(sealed_cutoff), 'IDENTITY_ARCHIVE_NOT_SEALED_AT_CUTOFF')
        bindings['archive_seals'].update(identities[2])
        # The proof is private to this store, invocation and source protocol.
        # Loading always reconstructs from freshly authenticated parents.
        bundle, reconstructed_seals = _reconstruct(self.service, descriptor, session, projections=identities)
        _need(all(stamp(seal) <= stamp(sealed_cutoff) for seal in reconstructed_seals.values()),
              'PARENT_ARCHIVE_NOT_SEALED_AT_CUTOFF')
        _PROOFS.setdefault(session, {})[_proof_key(self.service, session, input_id)] = (
            deepcopy(descriptor), identities, bundle, registration_seal, sealed_cutoff)
        bindings['base_outcome_input'] = bindings['outcome_input']
        bindings['outcome_input'] = dict(input_id=input_id, input_hash=descriptor['input_hash'],
            version=VERSION, descriptor_hash=bridge.digest_json_stream(descriptor),
            base_input_id=descriptor['base_input_id'], source_protocol=deepcopy(self.source_protocol))
        return runs, _InputCalendar(input_id, descriptor['input_hash'], calendar.observation_date,
                                    calendar.sessions), bindings

    def _load_outcome_input(self, input_id, verification):
        proof = _PROOFS.get(verification, {}).get(_proof_key(self.service, verification, input_id))
        _need(proof is not None, 'PRIVATE_INVOCATION_PROOF_REQUIRED')
        descriptor, identities, bundle, registration_seal, cutoff = proof
        _need(self._descriptor(input_id) == descriptor, 'REGISTERED_DESCRIPTOR_CHANGED')
        _need(bridge.digest_market_input(bundle, checkpoint=verification.check,
            encoding_profile=verification.encoding_profile) == descriptor['input_hash'],
            'BORROWED_DERIVED_INPUT_CHANGED')
        return bundle

    def _terminal(self, verification):
        for descriptor, identities, bundle, registration_seal, cutoff in _PROOFS.get(verification, {}).values():
            _need(self._descriptor(descriptor['input_id']) == descriptor, 'REGISTERED_DESCRIPTOR_CHANGED')
            _need(_check_seal(self.service, descriptor, verification, cutoff) == registration_seal,
                  'REGISTRATION_SEAL_CHANGED')
            with self.service.store.transaction(write=False) as connection:
                _need(bridge._stored_matches(connection, 'candidate_inputs', descriptor['input_id'],
                    descriptor['input_hash'], verification), 'DERIVED_DATABASE_INPUT_CHANGED')
        return super()._terminal(verification)

    def _execution_context(self, request, started_at):
        return {**super()._execution_context(request, started_at),
                'derived_input_version': VERSION,
                'derived_input_descriptor_hash': request['bindings']['outcome_input']['descriptor_hash']}
