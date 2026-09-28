# 请求结构定义（Ingestion V1）
from enum import Enum


class IngestionSource(str, Enum):
    XIAODIANHUO = "xiaodianhuo"
    TITAN007 = "titan007"
    MANUAL = "manual"
    UNKNOWN = "unknown"
