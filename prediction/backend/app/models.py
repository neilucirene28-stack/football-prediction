# 数据模型说明（第一版仅作常量/占位，实际依赖原始 JSON 落库）
from enum import Enum


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class EntityType(str, Enum):
    MATCH = "match"
    ODDS = "odds"
    LINEUP = "lineup"
    STANDING = "standing"
    OTHER = "other"
