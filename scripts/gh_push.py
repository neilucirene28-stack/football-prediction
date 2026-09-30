#!/usr/bin/env python3
"""把 football-prediction-v2 整棵树推送到 GitHub 私有仓库的 v2 分支。

走 git database API（create blobs → tree → commit → ref），不碰 main 分支。
认证复用 github skill 的 custom.github 凭据（经 authd 中转，原始 token 不落地）。

用法: python3 scripts/gh_push.py
"""
import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response  # noqa: E402

OWNER = "neilucirene28-stack"
REPO = "football-prediction"
BRANCH = "v2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = f"https://api.github.com/repos/{OWNER}/{REPO}"

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
SKIP_FILES = {".DS_Store"}


def api(method, path, data=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "muse-github-skill",
                 "Content-Type": "application/json"},
        method=method)
    add_surrogate_to_request(req, "custom.github",
                             allowed_hosts=["api.github.com"])
    with urllib.request.urlopen(req, timeout=60) as resp:
        return read_json_response(resp)


def collect_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            with open(full, "rb") as f:
                out.append((rel, f.read()))
    return sorted(out)


def main():
    files = collect_files()
    print(f"共 {len(files)} 个文件")
    tree = []
    for i, (rel, content) in enumerate(files):
        blob = api("POST", "/git/blobs",
                   {"content": base64.b64encode(content).decode(),
                    "encoding": "base64"})
        tree.append({"path": rel, "mode": "100644", "type": "blob",
                     "sha": blob["sha"]})
        if (i + 1) % 10 == 0:
            print(f"  blob {i + 1}/{len(files)}")
    tree_obj = api("POST", "/git/trees", {"tree": tree})
    print("tree:", tree_obj["sha"][:12])
    commit = api("POST", "/git/commits",
                 {"message": "football-prediction-v2: 采集器+部署+引擎 (v2.3)",
                  "tree": tree_obj["sha"], "parents": []})
    print("commit:", commit["sha"][:12])
    try:
        ref = api("GET", f"/git/ref/heads/{BRANCH}")
        api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit["sha"],
                                                  "force": True})
        print(f"分支 {BRANCH} 已更新 (原 {ref['object']['sha'][:12]})")
    except Exception:
        api("POST", "/git/refs",
            {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
        print(f"分支 {BRANCH} 已创建")
    print("OK: https://github.com/%s/%s/tree/%s" % (OWNER, REPO, BRANCH))


if __name__ == "__main__":
    main()
