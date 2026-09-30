"""被测接口契约的自建 Mock 服务（仅用标准库，无需额外依赖）。

为什么需要它
------------
真实的大模型对话服务不存在 / 不稳定 / 按量计费，空输入、超长、非法字符、超时这些
分支几乎无法稳定复现。把 Mock 作为**契约的显式实现**：
契约写在文档与 Mock 里，真实服务实现了同一套契约，同一批用例即可两边都跑。

契约（POST /llm/chat）
----------------------
请求：{"prompt": "用户输入"}
    200 {"content": "...", "usage": {...}}          正常
    400 {"error": {"code": "invalid_json"}}         请求体不是合法 JSON / 非 UTF-8
    400 {"error": {"code": "empty_prompt"}}         prompt 缺失、非字符串
    400 {"error": {"code": "blank_prompt"}}         prompt 为纯空白
    400 {"error": {"code": "illegal_characters"}}   含 NUL 或不可打印控制字符
    413 {"error": {"code": "context_length_exceeded"}} 超过 MAX_PROMPT_CHARS
    500 {"error": {"code": "model_internal_error"}} 故障注入（model 字段指定）
    401 {"error": {"code": "invalid_api_key"}}      缺少 / 非法 Authorization
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# 契约边界：prompt 长度上限 1000 字符
MAX_PROMPT_CHARS = 1000
# 故障注入用的模型名
FATAL_ERROR_MODEL = "error-500"
TRANSIENT_ERROR_PREFIX = "error-500x"


class MockServerError(RuntimeError):
    """Mock 服务启停失败。"""


def _error(code: str, message: str, status: int) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message}}


def _success(content: str, prompt_chars: int) -> dict[str, Any]:
    return {
        "content": content,
        "usage": {
            "prompt_chars": prompt_chars,
            "completion_chars": len(content),
        },
    }


def _slow_seconds(prompt: str) -> float | None:
    """解析 __slow_1.5__ 延迟标记，用于耗时基线 / 超时用例。"""
    marker = "__slow_"
    if marker not in prompt:
        return None
    tail = prompt.split(marker, 1)[1]
    number = tail.split("__", 1)[0]
    try:
        return float(number)
    except ValueError:
        return 1.2


def _transient_failures(model: str) -> int | None:
    """error-500xN：前 N 次返回 500，之后成功，用于验证重试自愈。"""
    if not model.startswith(TRANSIENT_ERROR_PREFIX):
        return None
    try:
        return int(model[len(TRANSIENT_ERROR_PREFIX) :])
    except ValueError:
        return None


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockLLMChat/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        """静音访问日志，避免污染 pytest 输出。"""

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/llm/health"):
            self._send(200, {"status": "ok"})
            return
        self._send(*_error("not_found", f"未知路径 {self.path}", 404))

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/llm/chat":
            self._send(*_error("not_found", f"未知路径 {self.path}", 404))
            return

        # 1) 鉴权
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
            self._send(*_error("invalid_api_key", "缺少或非法的 API Key", 401))
            return

        # 2) 请求体必须是合法 UTF-8 JSON
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(*_error("invalid_json", "请求体不是合法 JSON 或编码异常", 400))
            return
        if not isinstance(payload, dict):
            self._send(*_error("invalid_json", "请求体必须是 JSON 对象", 400))
            return

        # 3) 故障注入（仅错误编码的模型名会走到这里，所以放在白名单前）
        model = str(payload.get("model") or "")
        if model == FATAL_ERROR_MODEL:
            self._send(*_error("model_internal_error", "模型服务内部错误（Mock 注入）", 500))
            return
        transient = _transient_failures(model)
        if transient is not None:
            with self.server.counter_lock:  # type: ignore[attr-defined]
                counters = self.server.transient_counters  # type: ignore[attr-defined]
                counters[model] = counters.get(model, 0) + 1
                hit = counters[model]
            if hit <= transient:
                self._send(
                    *_error("model_internal_error", f"瞬时错误 第 {hit}/{transient} 次（Mock 注入）", 500)
                )
                return

        # 4) prompt 结构校验
        if "prompt" not in payload:
            self._send(*_error("empty_prompt", "prompt 字段缺失", 400))
            return
        prompt = payload["prompt"]
        if not isinstance(prompt, str):
            self._send(*_error("empty_prompt", f"prompt 必须是字符串，实际 {type(prompt).__name__}", 400))
            return

        # 5) 慢响应 / 超时注入
        if "__timeout__" in prompt:
            time.sleep(2.0)
        else:
            delay = _slow_seconds(prompt)
            if delay:
                time.sleep(delay)

        # 6) 空与空白
        if prompt == "":
            self._send(*_error("empty_prompt", "prompt 为空字符串", 400))
            return
        if prompt.strip() == "":
            self._send(*_error("blank_prompt", "prompt 为纯空白字符", 400))
            return

        # 7) 非法字符
        if "\x00" in prompt:
            self._send(*_error("illegal_characters", "prompt 含 NUL 字符", 400))
            return
        if any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in prompt):
            self._send(*_error("illegal_characters", "prompt 含不可打印控制字符", 400))
            return

        # 8) 超长（边界：恰好 1000 通过，1001 拒绝）
        if len(prompt) > MAX_PROMPT_CHARS:
            self._send(
                *_error(
                    "context_length_exceeded",
                    f"prompt 长度 {len(prompt)} 超过上限 {MAX_PROMPT_CHARS}",
                    413,
                )
            )
            return

        # 9) 正常返回（模拟模型生成）
        content = f"[mock] 已收到 {len(prompt)} 个字符，这是一条模拟模型回复。"
        self._send(200, _success(content, len(prompt)))


class MockLLMChatServer:
    """可编程启停的 Mock 服务（线程内运行，不阻塞 pytest）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "MockLLMChatServer":
        if self._httpd is not None:
            return self
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError as exc:
            raise MockServerError(
                f"Mock 服务无法监听 {self.host}:{self.port} —— {exc}。"
                "请确认端口未被占用，或修改 config.yaml 的 mock_server.port"
            ) from exc
        self._httpd.daemon_threads = True
        self._httpd.transient_counters = {}  # type: ignore[attr-defined]
        self._httpd.counter_lock = threading.Lock()  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="mock-llm", daemon=True)
        self._thread.start()
        self.host, self.port = self._httpd.server_address[0], self._httpd.server_address[1]
        return self

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "MockLLMChatServer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()
