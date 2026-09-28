"""Network/DB-free regression tests for source identification and safe display."""
import pytest

from app.services.source_validation import SourceValidationError, identify_source, validate_source
from app.services.detail_modules import project_modules


def xdh():
    return {"reportType": "XDH_JCZQ_BATCH", "matches": [
        {"match": {"match_id": "1260920020", "home": "富勒姆", "away": "曼联"}, "details": {}}
    ]}


def titan():
    return {"比赛信息": {"比赛ID": "3003893"}, "数据状态": {"分析": "已抓取"},
            "分析": {"说明文字": {"赛前简报": "赛前简报\n示例文本\n未来五场\n无需显示"}}}


def test_exact_positive_source_signatures():
    assert identify_source(xdh()) == "xiaodianhuo"
    assert identify_source(titan()) == "titan007"


@pytest.mark.parametrize("payload", [
    {"matches": [{"match": {"match_id": "1"}}]},
    {"reportType": "OTHER", "matches": [{"match": {"match_id": "1"}}]},
    {"reportType": "XDH_JCZQ_BATCH", "matches": []},
    {"比赛信息": {"比赛ID": "3003893"}},
    {"data": {"比赛信息": {"比赛ID": "3003893"}}},
    {}, [],
])
def test_unrecognized_payload_never_defaults_to_titan(payload):
    with pytest.raises(SourceValidationError):
        identify_source(payload)


def test_declared_source_must_agree_with_verified_structure():
    assert validate_source(xdh(), "unknown") == "xiaodianhuo"
    assert validate_source(titan(), "titan007") == "titan007"
    with pytest.raises(SourceValidationError):
        validate_source(xdh(), "titan007")


def test_failed_xdh_modules_not_classified_as_real_data():
    raw = {"details": {"match_base": {"status": "failed", "reason": "HTTP 404"},
                       "asia_stats": {"status": "success"}}}
    view = project_modules(raw)
    assert view["analysis"] == {"status": "failed", "reason": "HTTP 404"}
    assert view["asia"]["status"] == "pending_review"
    assert view["corners"] is None
    assert view["wdl"] is None


def test_titan_module_display_is_bounded_and_corners_unconfirmed():
    raw = titan()
    raw["亚让"] = {"公司盘口": [{"公司": "澳*", "初盘盘口": "受让半球", "初盘主水": "0.84",
                                   "初盘客水": "1.00", "即时盘口": "受让半球", "即时主水": "0.88", "即时客水": "0.96"}]}
    raw["角球"] = {"公司盘口": [{"公司": "Crow*", "初盘盘口": "10"}]}
    raw["胜平负"] = {"公司指数": [], "公司数量": 0}
    view = project_modules(raw)
    assert view["analysis"]["来源赛前简报（未经独立核实）"] == "示例文本"
    assert view["asia"]["已抓取盘口公司数"] == "1"
    assert view["corners"]["status"] == "pending_review"
    assert view["wdl"] is None
