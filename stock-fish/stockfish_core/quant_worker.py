"""Isolated, bounded native Qlib implementation; emits only a business artifact."""
from __future__ import annotations

import json
import hashlib
import os
import pickle
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import digest
from .model_registry import ModelArtifact, ModelRegistry, write_once
from .quant import QuantRequest


def run(path: str) -> None:
    envelope = json.loads(Path(path).read_text(encoding='utf-8'))
    request = QuantRequest.model_validate(envelope['request'])
    model_record = ModelArtifact.model_validate(envelope['model'])
    ModelRegistry.verify(model_record)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'qlib-zh'))
    import numpy as np
    import qlib
    from qlib.contrib.data.handler import Alpha158
    from qlib.data import D
    if qlib.__version__ != model_record.qlib_version:
        raise ValueError('QLIB_RUNTIME_VERSION_MISMATCH')
    data_path = Path(envelope['data_path'])
    qlib.init(provider_uri=str(data_path), region='cn', kernels=1,
              local_cache_path=str(Path(envelope['output']).parent / 'qlib-cache'))
    day = request.as_of.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    members = set(D.list_instruments(D.instruments('csi300'), start_time=day, end_time=day, as_list=True))
    requested = {i[-2:] + i[:6]: i for i in request.instruments}
    rejected = [{'instrument_id': i, 'reason_codes': ['NOT_IN_AS_OF_UNIVERSE']}
                for q, i in requested.items() if q not in members]
    selected = sorted(set(requested) & members)
    if not selected:
        write_once(envelope['output'], {'feature_schema_hash': model_record.feature_schema_hash,
                                       'scores': [], 'rejected': rejected})
        return
    model_bytes = Path(model_record.artifact_path).read_bytes()
    dataset_bytes = Path(model_record.dataset_path).read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != model_record.checkpoint_hash or hashlib.sha256(dataset_bytes).hexdigest() != model_record.dataset_hash:
        raise ValueError('MODEL_BYTES_HASH_MISMATCH')
    model = pickle.loads(model_bytes)
    saved = pickle.loads(dataset_bytes)
    if saved.handler.infer_processors or saved.handler.shared_processors:
        raise ValueError('UNSUPPORTED_FEATURE_PROCESSORS')
    old_expr, old_names = saved.handler.data_loader.fields['feature']
    # Always build the registered as-of universe before selecting caller rows.
    # This preserves a fixed cross-section if feature processing ever changes.
    handler = Alpha158(instruments=sorted(members), start_time=day, end_time=day, learn_processors=[])
    if handler.infer_processors or handler.shared_processors:
        raise ValueError('UNSUPPORTED_CURRENT_FEATURE_PROCESSORS')
    expressions, names = handler.data_loader.fields['feature']
    schema_hash = digest({'expressions': list(expressions), 'names': list(names)})
    if list(expressions) != list(old_expr) or list(names) != list(old_names) or schema_hash != model_record.feature_schema_hash:
        raise ValueError('FEATURE_SCHEMA_MISMATCH')
    features = handler.fetch(col_set='feature')
    if tuple(features.columns) != model_record.feature_schema or features.shape[1] != model.model.num_feature():
        raise ValueError('FEATURE_SHAPE_MISMATCH')
    features = features[features.index.get_level_values('instrument').isin(selected)]
    kept = []
    for ix, row in features.iterrows():
        instrument = ix[features.index.names.index('instrument')]
        values = row.to_numpy(dtype=float)
        if np.isinf(values).any() or np.isfinite(values).mean() < .8:
            rejected.append({'instrument_id': requested[instrument], 'reason_codes': ['FEATURE_COVERAGE_INSUFFICIENT']})
        else:
            kept.append(ix)
    features = features.loc[kept] if kept else features.iloc[0:0]
    if len(features):
        values = model.model.predict(features, num_threads=1, predict_disable_shape_check=False)
        if not np.isfinite(values).all():
            raise ValueError('NONFINITE_PREDICTIONS')
        rows = [{'instrument_id': requested[ix[features.index.names.index('instrument')]], 'raw_score': float(value)}
                for ix, value in zip(features.index, values)]
    else:
        rows = []
    write_once(envelope['output'], {'feature_schema_hash': schema_hash, 'scores': rows,
                                   'rejected': rejected, 'rows': len(features), 'features': len(names)})


if __name__ == '__main__':
    run(sys.argv[1])
