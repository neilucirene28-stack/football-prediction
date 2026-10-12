"""竞彩业务流程隔离；合成用例不作为准确率提升证据。"""

from copy import deepcopy
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from engine.jingcai_config import default_config
from engine.jingcai_predictor import predict
from engine.jingcai_calibration import load_form_only_config
from engine.jingcai_core import consistency_check
from test_jingcai_inputs_grid import payload, CONFIG, ASOF


def test_jingcai_runs_without_legacy_core_or_other_project_files(tmp_path):
    engine_dir = Path(__file__).resolve().parents[1] / "engine"
    shutil.copytree(
        engine_dir,
        tmp_path / "engine",
        ignore=shutil.ignore_patterns(
            "__pycache__", "beidan*", "predictor.py", "letdraw.py", "calibration.json"
        ),
    )
    data = {"payload": payload(), "config": CONFIG, "asof": ASOF.isoformat()}
    (tmp_path / "input.json").write_text(json.dumps(data))
    code = """
import sys,json,importlib.abc
from datetime import datetime
class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('engine.predictor','engine.letdraw') or fullname.startswith('engine.beidan'):
            raise RuntimeError('unexpected legacy import: '+fullname)
sys.meta_path.insert(0,BlockLegacy())
from engine.jingcai_predictor import predict
from engine.jingcai_config import default_config
from engine.jingcai_calibration import load_form_only_config
x=json.load(open('input.json'))
r=predict(x['payload'],x['config'],asof=datetime.fromisoformat(x['asof']))
assert r['status']=='ok' and r['project_scope']=='jingcai'
assert load_form_only_config()[0] is None
assert 'beidan' not in r
assert 'engine.predictor' not in sys.modules
assert not any(n.startswith('engine.beidan') for n in sys.modules)
print('standalone_jingcai_ok')
"""
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    assert "standalone_jingcai_ok" in r.stdout


def test_default_config_returns_independent_copy():
    first = default_config()
    second = default_config()
    first["rho"] = 0
    assert second["rho"] == -0.13 and default_config()["rho"] == -0.13


def test_legacy_prior_mutation_does_not_change_jingcai(monkeypatch):
    from engine import letdraw as legacy
    from engine import jingcai_letdraw as jc

    base = predict(payload(), CONFIG, asof=ASOF)
    assert jc.TIER_PRIOR is not legacy.TIER_PRIOR
    monkeypatch.setitem(legacy.TIER_PRIOR, 1, 0.99)
    monkeypatch.setitem(legacy.LEAGUE_PRIOR_1, "测试联赛", 0.99)
    new = predict(payload(), CONFIG, asof=ASOF)
    assert new["score_matrix_full"] == base["score_matrix_full"]
    assert new["model_version"] == base["model_version"]


def test_legacy_helper_mutation_does_not_change_jingcai(monkeypatch):
    legacy = importlib.import_module("engine.predictor")

    def forbidden(*args, **kwargs):
        raise RuntimeError("legacy core must not run")

    for name in ("predict", "estimate_lambdas", "ensemble", "apply_beidan_calibration"):
        monkeypatch.setattr(legacy, name, forbidden)
    assert predict(payload(), CONFIG, asof=ASOF)["status"] == "ok"


@pytest.mark.parametrize("rq", [-2, -1, 0, 1, 2])
@pytest.mark.parametrize("strength", [0, 0.5, 1])
def test_new_strengths_and_legacy_are_independently_consistent(rq, strength):
    from engine.predictor import predict as legacy_predict
    from engine.poisson import match_probs

    p = payload(handicap_line=rq)
    before = deepcopy(p)
    cfg = {**CONFIG, "letdraw_strength": strength, "project_scope": "jingcai",
           'strength_baseline_mode': 'venue_normalized'}
    isolated = predict(p, cfg, asof=ASOF)
    reference = legacy_predict(p, cfg, asof=ASOF, model="jingcai")
    for result in (isolated, reference):
        assert match_probs(result['score_matrix_full']) == pytest.approx(result['p_final_full'])
    assert isolated['lambda_notes']['home_goal_baseline'] + isolated['lambda_notes']['away_goal_baseline'] == pytest.approx(2.7)
    assert isolated['lambda_home_full'] != reference['lambda_home_full']
    after = legacy_predict(p, cfg, asof=ASOF, model='jingcai')
    assert after['score_matrix_full'] == reference['score_matrix_full']
    assert p == before


def test_old_calibration_file_not_auto_loaded(tmp_path, monkeypatch):
    import engine.jingcai_calibration as c

    path = tmp_path / "jingcai_calibration.py"
    path.write_text("")
    (tmp_path / "calibration.json").write_text(
        json.dumps(
            {
                "form_only": {"home": [1, 0]},
                "form_only_provenance": {"model": "jingcai"},
            }
        )
    )
    monkeypatch.setattr(c, "__file__", str(path))
    cfg, audit = c.load_form_only_config()
    assert cfg is None and audit["reason"] == "missing_or_invalid_artifact"


def test_error_type_and_lazy_exports_remain_compatible():
    from engine.errors import PredictError
    from engine.predictor import PredictError as legacy_error
    from engine.jingcai_predictor import PredictError as jc_error
    import engine

    assert PredictError is legacy_error is jc_error
    assert callable(engine.predict) and callable(engine.goal_weight)
    assert "apply_beidan_calibration" in dir(engine)
    with pytest.raises(AttributeError):
        getattr(engine, "unavailable_export")


@pytest.mark.parametrize(
    "where,key,value",
    [
        ("payload", "project", "beidan"),
        ("payload", "beidan_settings", {}),
        ("config", "model", "beidan"),
        ("config", "beidan_calibration", {}),
    ],
)
def test_direct_core_cannot_bypass_project_guard(monkeypatch, where, key, value):
    import engine.jingcai_core as core
    from engine.errors import PredictError

    def forbidden(*args, **kwargs):
        pytest.fail("data must not load before scope validation")

    monkeypatch.setattr(core, "load_jingcai_cards_context", forbidden)
    p = payload()
    cfg = dict(CONFIG)
    (p if where == "payload" else cfg)[key] = value
    with pytest.raises(PredictError):
        core.predict(p, cfg, asof=ASOF)
