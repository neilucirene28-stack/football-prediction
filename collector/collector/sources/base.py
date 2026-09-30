"""Source 抽象基类：所有数据源实现 fetch_matches() 即可接入。"""
from abc import ABC, abstractmethod
from datetime import datetime


class Source(ABC):
    name = "base"

    @abstractmethod
    def fetch_matches(self) -> list[dict]:
        """返回比赛列表，每项至少含：
        {external_id, competition, home_team, away_team, kickoff_at(iso带时区),
         status, odds:{home,draw,away}(可选), home_recent/away_recent(可选)}。
        """
        raise NotImplementedError

    def validate(self, matches: list[dict]) -> list[dict]:
        ok = []
        for m in matches:
            try:
                dt = datetime.fromisoformat(m["kickoff_at"].replace("Z", "+00:00"))
                assert dt.tzinfo is not None
                assert m["home_team"] and m["away_team"] and m["home_team"] != m["away_team"]
                ok.append(m)
            except (KeyError, AssertionError, ValueError):
                continue
        return ok
