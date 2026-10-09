"""
premierinjuries.com 接入：英超伤病 / 停赛 / 复出名单
https://www.premierinjuries.com/injury-table.php

实测 (2026-10-09):
- 静态 WordPress 页面，无反爬、无登录，curl 直接 200 返回完整 HTML。
- 20 支英超球队完整表格 (class="injury-table injury-table-full injury-table-epl")，
  每队一块: tr.heading (div.injury-team = 队名) + tr.sub-head (表头) + N 个
  tr.player-row.team_<id>。
- 球员行 7 列: Player / Reason / Further Detail / Potential Return /
  Condition / Status / (跟踪按钮)。
  - Reason: 伤病部位 ("Thigh Injury" 等) 或 "Suspended" (停赛)。
  - Potential Return: DD/MM/YYYY，如 "21/11/2026"。
  - Status: "Ruled Out" / "25%" / "50%" / "75%" / "100%"。
  - Condition: "Not Available" / "Currently Being Assessed" /
    "Late Fitness Test" / "Passed Fit"。
  - Further Detail 开头常带新闻更新日期 ("Oct 08: '...'" 或 "Sept 16: '...'"，
    或纯文本如 "Sending Off - Red Card")。

只做纯 HTML 解析，标准库 + 正则，无新依赖。
网络失败 / 表格结构变化时抛异常，由 daily_fetch 捕获。
"""

import html as html_module
import re

from _http import fetch_with_retry

BASE_URL = "https://www.premierinjuries.com"
TABLE_URL = f"{BASE_URL}/injury-table.php"
TABLE_CLASS = "injury-table injury-table-full injury-table-epl"

# 状态 → 中文 (任务口径: 受伤 / 停赛 / 复出)
STATUS_INJURED = "受伤"
STATUS_SUSPENDED = "停赛"
STATUS_RETURNED = "复出"

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sept": 9, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _clean_cell(td_html):
    """去掉 mob-title 标注 div 与所有标签，解码 HTML 实体，压缩空白。"""
    t = re.sub(r'<div class="mob-title">.*?</div>', "", td_html, flags=re.S)
    # 去掉 "See Player Page" 链接 (球员页链接本身无数据价值)
    t = re.sub(r'<a href="/newsroom/epl/players/[^"]*">[^<]*</a>', "", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html_module.unescape(t)
    return " ".join(t.split())


def _map_status(reason, condition, status_raw):
    if reason.strip().lower() == "suspended":
        return STATUS_SUSPENDED
    if condition.strip() == "Passed Fit" or status_raw.strip() == "100%":
        return STATUS_RETURNED
    return STATUS_INJURED


def _parse_news_date(detail):
    """Further Detail 开头的 "Oct 08:" / "Sept 16:" → ISO 日期 (YYYY-MM-DD)。"""
    m = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2})\s*:", detail)
    if not m:
        return None
    month = _MONTHS.get(m.group(1))
    if not month:
        return None
    day = int(m.group(2))
    # 按当前赛季推算年份: 今天是 2026-10-09；月份 > 10 视为上一年赛季尾段
    year = 2026 if month <= 10 else 2025
    return f"{year}-{month:02d}-{day:02d}"


def _parse_return_date(raw):
    """Potential Return "21/11/2026" (DD/MM/YYYY) → ISO；空或非法返回 None。"""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", raw or "")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return f"{y}-{mo:02d}-{d:02d}"
    except ValueError:
        return None


def parse_injury_table(page_html):
    """
    纯解析函数：从 injury-table.php 整页 HTML 提取英超伤病名单。

    返回 list[dict]，每条:
      player            球员名
      team              球队名
      injury            Reason (伤病部位 / "Suspended")
      status            中文状态: 受伤 / 停赛 / 复出
      status_raw        站点原始 Status 列 ("Ruled Out"/"25%" 等)
      condition         站点原始 Condition 列
      expected_return   Potential Return 原始串 (DD/MM/YYYY)
      expected_return_iso  同上转 ISO，非法为空时 None
      detail            Further Detail 纯文本
      updated           Further Detail 开头新闻日期转 ISO，无日期时 None

    表格结构变化 / 找不到表格时抛 ValueError (调用方按抓取失败处理)。
    """
    m = re.search(
        r'<table class="%s">(.*?)</table>' % re.escape(TABLE_CLASS),
        page_html, re.S,
    )
    if not m:
        raise ValueError(
            "premierinjuries: 找不到 injury-table-full 表格，页面结构可能已变更"
        )
    table = m.group(1)

    records = []
    current_team = None
    for row_m in re.finditer(r"<tr([^>]*)>(.*?)</tr>", table, re.S):
        attrs, body = row_m.group(1), row_m.group(2)
        if 'class="heading"' in attrs:
            t = re.search(r'<div class="injury-team">(.*?)</div>', body, re.S)
            current_team = html_module.unescape(t.group(1)).strip() if t else None
            continue
        if "player-row" not in attrs:
            continue
        cells = re.findall(r"<td.*?</td>", body, re.S)
        if len(cells) < 6 or current_team is None:
            continue  # 行结构异常：跳过脏行，不整体失败
        player = _clean_cell(cells[0])
        reason = _clean_cell(cells[1])
        detail = _clean_cell(cells[2])
        potential_return = _clean_cell(cells[3])
        condition = _clean_cell(cells[4])
        status_raw = _clean_cell(cells[5])
        if not player:
            continue
        records.append({
            "player": player,
            "team": current_team,
            "injury": reason,
            "status": _map_status(reason, condition, status_raw),
            "status_raw": status_raw,
            "condition": condition,
            "expected_return": potential_return,
            "expected_return_iso": _parse_return_date(potential_return),
            "detail": detail,
            "updated": _parse_news_date(detail),
        })
    if not records:
        raise ValueError("premierinjuries: 表格解析出 0 条记录，页面结构可能已变更")
    return records


def get_injuries():
    """
    抓取英超最新伤病 / 停赛 / 复出名单。
    返回 parse_injury_table 的 list[dict]；网络或解析失败时抛异常。
    """
    body = fetch_with_retry(
        TABLE_URL,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
        timeout=25,
        parse_json=False,
    )
    return parse_injury_table(body.decode("utf-8", "ignore"))


if __name__ == "__main__":
    rows = get_injuries()
    print(f"teams={len({r['team'] for r in rows})} rows={len(rows)}")
    for r in rows[:5]:
        print(r["team"], "|", r["player"], "|", r["injury"], "|",
              r["status"], "|", r["expected_return_iso"], "|", r["updated"])
