"""Explicit version routing; existing saved R1 runs retain their identity."""
from importlib import import_module

SHORT_HORIZON_IDS = ('SHORT_HORIZON_STRENGTH_R1', 'SHORT_HORIZON_STRENGTH_R2')


def short_horizon_module(strategy_id):
    modules = dict(zip(SHORT_HORIZON_IDS, ('short_horizon_strength', 'short_horizon_participation')))
    if strategy_id not in modules:
        raise ValueError('unsupported explicitly selected short-horizon research strategy')
    return import_module('.' + modules[strategy_id], __package__)
