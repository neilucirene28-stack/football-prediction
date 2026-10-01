"""
共享 HTTP 工具：curl 子进程封装（带重试/退避/超时）

说明: 8 个数据源模块实际都是用 curl 子进程走代理（而非 urllib），
原因是通过 --cacert 显式指定 Hatch 出口代理的 MITM CA，比 urllib
配 ssl 上下文更稳。这里统一封装重试逻辑，各模块只换内部实现，
函数签名保持不变。

提供:
- clean_no_proxy(): 清理 no_proxy 里带方括号的 IPv6 条目（如 [::1]），
  新版 httpx 会因此报 InvalidURL。import 本模块时自动执行一次。
- fetch_with_retry(): GET 请求，最多 3 次、指数退避 1s/2s/4s，
  区分超时 / HTTP 错误 / 429 限流，默认超时 20 秒。
- download_with_retry(): 下载文件到本地（带 -L 可选、最小体积校验）。
- fetch_response_headers(): 取响应头（用于读剩余额度类场景）。
"""

import json
import os
import subprocess
import time

CA_BUNDLE = "/run/hatch/egress-tls/ca-bundle.pem"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 3
# 指数退避: 第1/2/3次重试前分别睡 1s / 2s / 4s
BACKOFF_BASE = 1
# 429 限流后的等待秒数
RETRY_AFTER_429 = 60


class HTTPError(RuntimeError):
    """HTTP 状态码错误（4xx/5xx），code 可供调用方分支判断"""

    def __init__(self, code, url, body=""):
        super().__init__(f"HTTP {code}: {url}: {body[:200]}")
        self.code = code
        self.url = url
        self.body = body


def clean_no_proxy():
    """
    清理 no_proxy / NO_PROXY 中的 IPv6 字面量条目。
    - 带方括号的如 [::1]：新版 httpx 解析时报 InvalidURL: Invalid port: ':1]'
    - 裸 IPv6 如 ::1：同样会被 httpx 误解析为 host:port 而崩溃
    只保留主机名和 IPv4，curl 自身对两种格式都能正确处理，
    且本项目所有请求都是公网 https，不依赖 IPv6 直连。
    返回清理后的值（便于日志）。
    """
    import ipaddress

    def _is_ipv6(entry):
        probe = entry.strip()
        if probe.startswith("[") and probe.endswith("]"):
            probe = probe[1:-1]
        try:
            return isinstance(ipaddress.ip_address(probe), ipaddress.IPv6Address)
        except ValueError:
            return False

    for var in ("no_proxy", "NO_PROXY"):
        val = os.environ.get(var)
        if not val:
            continue
        kept = [p.strip() for p in val.split(",") if p.strip() and not _is_ipv6(p)]
        os.environ[var] = ",".join(kept)
    return os.environ.get("no_proxy", os.environ.get("NO_PROXY", ""))


# import 时清理一次；每次请求前 fetch_with_retry 内也会再调
clean_no_proxy()


def _build_cmd(url, headers=None, timeout=DEFAULT_TIMEOUT, extra_args=None):
    cmd = ["curl", "-s", "--max-time", str(timeout), "-w", "\n%{http_code}"]
    if os.path.exists(CA_BUNDLE):
        cmd += ["--cacert", CA_BUNDLE]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if extra_args:
        cmd += extra_args
    cmd.append(url)
    return cmd


def _split_body_code(raw):
    """分离 curl -w 追加的 http_code"""
    out = raw.rstrip(b"\n")
    if b"\n" in out:
        body, code_str = out.rsplit(b"\n", 1)
    else:
        body, code_str = out, b""
    try:
        code = int(code_str.strip())
    except ValueError:
        code = 0
    return body, code


def fetch_with_retry(url, headers=None, timeout=DEFAULT_TIMEOUT,
                     max_retries=MAX_RETRIES, extra_args=None,
                     parse_json=True):
    """
    GET 请求，带重试。
    - 超时 / 连接失败 / 5xx / 429：指数退避重试（1s/2s/4s；429 固定等 60s）
    - 4xx（除 408/429）：直接抛 HTTPError，不重试
      （如 football-charts 的 unknown_season=400，调用方可据此探测可用赛季）
    - parse_json=True 时返回解析后的 dict/list，否则返回原始 bytes
    """
    clean_no_proxy()
    last_err = None
    for attempt in range(max_retries):
        cmd = _build_cmd(url, headers, timeout, extra_args)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired as e:
            last_err = RuntimeError(f"请求超时({timeout}s): {url}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        except OSError as e:
            last_err = RuntimeError(f"curl 启动失败: {e}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue

        body, code = _split_body_code(proc.stdout)
        body_text = body.decode("utf-8", "ignore")

        if code == 429:
            last_err = HTTPError(429, url, body_text)
            time.sleep(RETRY_AFTER_429)
            continue
        if code != 200:
            last_err = HTTPError(code, url, body_text)
            # 客户端错误不重试（未知赛季/参数错误等），直接抛给调用方处理
            if 400 <= code < 500 and code not in (408, 429):
                raise last_err
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue

        if parse_json:
            try:
                return json.loads(body)
            except json.JSONDecodeError as e:
                last_err = RuntimeError(f"JSON 解析失败: {url}: {e}")
                time.sleep(BACKOFF_BASE * (2 ** attempt))
                continue
        return body

    raise RuntimeError(f"请求失败({max_retries}次重试): {url}: {last_err}")


def download_with_retry(url, dest, headers=None, timeout=30,
                        max_retries=MAX_RETRIES, follow_redirects=False,
                        min_size=1):
    """
    下载文件到 dest。成功返回 dest；失败抛异常。
    min_size: 文件字节数下限（用于发现下载到错误页面/空文件）。
    """
    clean_no_proxy()
    last_err = None
    for attempt in range(max_retries):
        cmd = ["curl", "-s", "--max-time", str(timeout)]
        if os.path.exists(CA_BUNDLE):
            cmd += ["--cacert", CA_BUNDLE]
        if follow_redirects:
            cmd.append("-L")
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        cmd += ["-o", dest, url]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"下载超时({timeout}s): {url}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        if proc.returncode != 0:
            last_err = RuntimeError(
                f"curl 退出码 {proc.returncode}: {url}: "
                f"{proc.stderr.decode('utf-8', 'ignore')[:150]}"
            )
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        if not os.path.exists(dest) or os.path.getsize(dest) < min_size:
            size = os.path.getsize(dest) if os.path.exists(dest) else -1
            last_err = RuntimeError(f"下载文件异常({size}字节 < {min_size}): {url}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        return dest

    raise RuntimeError(f"下载失败({max_retries}次重试): {url}: {last_err}")


def fetch_response_headers(url, headers=None, timeout=15):
    """
    只取响应头（不下载 body），返回小写 key 的 dict。
    用于 The Odds API 这类把剩余额度放在 x-requests-remaining 里的场景。
    """
    clean_no_proxy()
    cmd = ["curl", "-s", "-D", "-", "-o", "/dev/null",
           "--max-time", str(timeout)]
    if os.path.exists(CA_BUNDLE):
        cmd += ["--cacert", CA_BUNDLE]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"取响应头超时: {url}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        out = {}
        for line in proc.stdout.decode("utf-8", "ignore").split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip().lower()] = v.strip()
        return out
    raise RuntimeError(f"取响应头失败({MAX_RETRIES}次重试): {url}: {last_err}")
