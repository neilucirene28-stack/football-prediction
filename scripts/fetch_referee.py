#!/usr/bin/env python3
"""赛前裁判任命采集（MVP-5，尽力而为）。

尝试从 Titan007 公开分析页解析当值裁判；拿不到 → 返回 None，
engine/cards.py 会用乘子 1.0 + low_confidence + 明确标记，绝不编造。

现状（2026-09-30 已验证）：Titan007 公开页赛前无稳定的裁判任命数据源
（分析页裁判模块为 JS 动态加载，无公开接口；详情页裁判仅出现在赛后事件图例）。
因此当前 referee 以手动/上游输入为主，本脚本保留为采集钩子。
"""
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def fetch_titan007(titan_id: str) -> dict:
    """返回 {"referee": str|None, "source": str, "note": str}。"""
    url = f"https://zq.titan007.com/analysis/{titan_id}cn.htm"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Referer": "https://zq.titan007.com/"})
        html = urllib.request.urlopen(req, timeout=30).read().decode(
            "utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return {"referee": None, "source": "titan007",
                "note": f"页面抓取失败: {e}"}
    # 已知裁判任命文本模式（若未来 Titan007 公开显示则命中）
    for pat in (r"主裁判[：:]\s*([^\s<&,]{2,20})",
                r"裁判[：:]\s*([^\s<&,]{2,20})",
                r"Referee[：:]\s*([A-Za-z .]{3,30})"):
        m = re.search(pat, html)
        if m:
            return {"referee": m.group(1).strip(), "source": "titan007",
                    "note": "分析页解析"}
    return {"referee": None, "source": "titan007",
            "note": "公开页无赛前裁判任命数据（已验证）"}


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: fetch_referee.py <titan007比赛ID>")
        return 1
    print(fetch_titan007(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
