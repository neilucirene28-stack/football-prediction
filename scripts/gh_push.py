#!/usr/bin/env python3
"""把 football-prediction-v2 整棵树推送到 GitHub 私有仓库的 v2 分支。

走 git database API（create blobs → tree → commit → ref），不碰 main 分支。
认证复用 github skill 的 custom.github 凭据（经 authd 中转，原始 token 不落地）。

用法: python3 scripts/gh_push.py
"""
import base64
import fnmatch
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response  # noqa: E402

OWNER = "neilucirene28-stack"
REPO = "football-prediction"
BRANCH = "v2"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = f"https://api.github.com/repos/{OWNER}/{REPO}"

SKIP_DIRS = {".git", "__pycache__", ".venv", ".venv-soccerdata", "venv",
             "node_modules", ".pytest_cache"}
# 按相对路径前缀跳过的目录（每日生成的快照，不进仓库）
SKIP_DIR_PREFIXES = ("data/daily/",)
SKIP_FILES = {".DS_Store"}


def load_gitignore():
    """读取 .gitignore，推送时同样生效（防止 key 泄漏）。"""
    pats = []
    gi = os.path.join(ROOT, ".gitignore")
    if os.path.exists(gi):
        with open(gi, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pats.append(line)
    return pats


GITIGNORE_PATS = load_gitignore()


def ignored(rel):
    base = os.path.basename(rel)
    for pat in GITIGNORE_PATS:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def api(method, path, data=None, retries=5):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "muse-github-skill",
                     "Content-Type": "application/json"},
            method=method)
        add_surrogate_to_request(req, "custom.github",
                                 allowed_hosts=["api.github.com"])
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return read_json_response(resp)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (400, 429, 500, 502, 503):
                raise
            time.sleep(2 ** attempt)
    raise last


def collect_files():
    out = []
    skipped_secret = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if rel.replace(os.sep, "/").startswith(SKIP_DIR_PREFIXES):
                continue
            if ignored(rel):
                skipped_secret.append(rel)
                continue
            with open(full, "rb") as f:
                out.append((rel, f.read()))
    if skipped_secret:
        print(f"已按 .gitignore 跳过 {len(skipped_secret)} 个文件: "
              f"{', '.join(skipped_secret[:8])}"
              f"{'...' if len(skipped_secret) > 8 else ''}")
    return sorted(out)


def main():
    msg = (sys.argv[1] if len(sys.argv) > 1
           else "football-prediction-v2: 数据源接入+每日抓取 (2026-10-01)")
    files = collect_files()
    print(f"共 {len(files)} 个文件")
    # 安全复核：key 文件绝不能出现在推送列表里
    bad = [r for r, _ in files
           if os.path.basename(r).startswith(".") and
           r.split(".")[-1].endswith("_key") or r in
           (".af_key", ".fd_key", ".odds_key", ".fc_key")]
    if bad:
        print(f"拒绝推送：发现密钥文件 {bad}")
        sys.exit(1)
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
    try:
        ref = api("GET", f"/git/ref/heads/{BRANCH}")
        parent_sha = ref["object"]["sha"]
        print(f"v2 当前头: {parent_sha[:12]}，将作为父提交（保留历史）")
        parents = [parent_sha]
        ref_exists = True
    except Exception:
        parents = []
        ref_exists = False
    commit = api("POST", "/git/commits",
                 {"message": msg,
                  "tree": tree_obj["sha"], "parents": parents})
    print("commit:", commit["sha"][:12])
    if ref_exists:
        api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit["sha"]})
        print(f"分支 {BRANCH} 已快进更新")
    else:
        api("POST", "/git/refs",
            {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
        print(f"分支 {BRANCH} 已创建")
    print("OK: https://github.com/%s/%s/tree/%s" % (OWNER, REPO, BRANCH))


if __name__ == "__main__":
    main()
