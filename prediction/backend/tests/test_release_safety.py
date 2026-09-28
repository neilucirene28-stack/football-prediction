"""Pure offline regression checks; do not connect to a server or write a database."""
from pathlib import Path

import pytest

from app.services.source_detection import detect_source, resolve_source


def _xdh():
    return {"reportType": "XDH_JCZQ_BATCH", "matches": [
        {"match": {"match_id": "1260920020", "home": "富勒姆", "away": "曼联"}}]}


def _titan():
    return {"比赛信息": {"比赛ID": "3003893"}, "数据状态": {"分析": "已抓取"}, "分析": {}}


def test_structured_detection():
    assert detect_source(_xdh()) == "xiaodianhuo"
    assert detect_source(_titan()) == "titan007"


@pytest.mark.parametrize("payload", [
    {}, {"reportType": "XDH_JCZQ_BATCH"}, {"reportType": "other", "matches": []},
    {"reportType": "XDH_JCZQ_BATCH", "matches": [{}]},
    {"比赛信息": {"比赛ID": "3003893"}},
    {"matches": []}, [],
])
def test_unknown_does_not_become_titan(payload):
    assert detect_source(payload) is None
    with pytest.raises(ValueError):
        resolve_source(payload)


def test_ambiguous_and_declared_mismatch_rejected():
    payload = dict(_xdh(), **_titan())
    assert detect_source(payload) is None
    with pytest.raises(ValueError):
        resolve_source(_xdh(), "titan007")
    with pytest.raises(ValueError):
        resolve_source(_titan(), "manual")


def test_read_only_ui_and_server_default():
    project = Path(__file__).resolve().parents[2]
    page = (project / "backend/app/static/index.html").read_text()
    compose = (project / "docker-compose.yml").read_text()
    main = (project / "backend/app/main.py").read_text()
    assert 'id="token"' not in page and 'id="upBtn"' not in page
    assert '采集快照原始数据' not in page
    assert 'UPLOADS_ENABLED: "false"' in compose
    assert 'if req.url.scheme != "https"' in main
    assert main.count('    _check_upload_token(') == 3
