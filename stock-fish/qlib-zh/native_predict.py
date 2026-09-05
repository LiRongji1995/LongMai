"""Run the released CSI300 Alpha158 checkpoint on Windows without Docker."""
import argparse
import hashlib
import json
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
import yaml
from qlib.contrib.data.handler import Alpha158
from scripts.practice.run_stage2_walk_forward import (
    MODEL_SPECS, _find_latest_fold_checkpoint, _predict_only_export,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--pred-date', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    model_dir = Path(args.model_dir)
    output = Path(args.output_dir)
    fold = _find_latest_fold_checkpoint(model_dir / 'model_predict/walk_forward', MODEL_SPECS)
    checkpoint = fold['checkpoints']['lightgbm']
    config = yaml.safe_load(Path(fold['fold_config']).read_text(encoding='utf-8'))
    handler = config['task']['dataset']['kwargs']['handler']
    handler_kwargs = handler.get('kwargs', {})
    if handler['class'] not in ('Alpha158', 'MultiHorizonAlpha158Handler') or any(
        handler_kwargs.get(k) for k in ('infer_processors', 'shared_processors')
    ):
        raise ValueError('This local runner requires the released, unnormalized Alpha158 features.')
    qlib.init(provider_uri=os.environ['QLIB_DATA_DIR'], region='cn',
              kernels=int(os.environ.get('QLIB_KERNELS', '1')),
              local_cache_path=str(Path(os.environ['QLIB_DATA_DIR']).parent / '.cache'))
    print(f'Loading checkpoint: {checkpoint}', flush=True)
    with checkpoint.open('rb') as source:
        model = pickle.load(source)
    booster = model.model
    with (checkpoint.parent / 'dataset').open('rb') as source:
        training_dataset = pickle.load(source)
    training_handler = training_dataset.handler
    if training_handler.infer_processors or training_handler.shared_processors:
        raise ValueError('The checkpoint requires feature processors not supported by this runner.')
    expected_expressions, expected_names = training_handler.data_loader.fields['feature']
    # Alpha158 expressions obtain their own rolling lookback; labels are unused at inference.
    current_handler = Alpha158(instruments='csi300', start_time=args.pred_date,
                               end_time=args.pred_date, learn_processors=[])
    expressions, names = current_handler.data_loader.fields['feature']
    if list(expressions) != list(expected_expressions) or list(names) != list(expected_names):
        raise ValueError('Alpha158 feature expressions/order differ from the saved training dataset.')
    data = current_handler.fetch(col_set='feature')
    if data.empty or data.shape[1] != booster.num_feature():
        raise ValueError(f'Feature count mismatch: {data.shape}, model={booster.num_feature()}')
    # Qlib trains with a NumPy array, so released boosters may use positional Column_N names.
    positional_names = [f'Column_{i}' for i in range(data.shape[1])]
    if list(data.columns) != list(expected_names) or booster.feature_name() not in (list(data.columns), positional_names):
        raise ValueError('Feature names/order differ from the released checkpoint.')
    values = booster.predict(data, num_threads=1, predict_disable_shape_check=False)
    if not np.isfinite(values).all():
        raise ValueError('Model produced non-finite scores.')
    prediction = pd.DataFrame({'score': values}, index=data.index)
    _predict_only_export(prediction, None, output, args.pred_date)
    manifest = dict(executed_at=datetime.now(timezone.utc).isoformat(),
                    model_dir=str(model_dir), checkpoint=str(checkpoint),
                    checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                    fold=fold['fold_tag'], train_start=fold['train_start'], train_end=fold['train_end'],
                    pred_date=args.pred_date, universe='csi300', rows=len(data), features=data.shape[1],
                    qlib_version=qlib.__version__, data_dir=os.environ['QLIB_DATA_DIR'],
                    strict_feature_names=True, strict_feature_expressions=True, trained=False)
    (output / 'execution.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    main()
