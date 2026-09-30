"""被测接口契约的自建 Mock 服务（仅用标准库，无需额外依赖）。

为什么有两种模式
----------------
真实被测对象是外部的 OpenAI 兼容开源服务，实测它**几乎不做输入校验**：
空串 / 纯空白 / NUL / 控制字符 / 超长内容全部返回 200。但我们同时需要表达
「容器类系统应当拒绝这些输入」的契约口径，用来做对照。

于是用模型名区分两种契约：

    model = mock-llm         宽松模式（默认）：对齐被测服务的实测行为
    model = mock-strict      严格模式：按「应有输入校验」的契约口径拒绝非法输入

两种模式共用同一套 HTTP 状态码与错误码约定，因此同一批用例在 mock / real
两种环境下都能跑，差异只体现在「预期值」上，写用例数据里即可。

契约（POST /llm/chat）
----------------------
请求：{"model": "...", "prompt": "用户输入"}

  200 {"content": "...", "usage": {...}}                 正常
  422 {"error": {"code": "invalid_request_error"}}       结构非法（messages/prompt 缺失、类型错误）
  400 纯文本 "Failed to parse the request body as JSON"  请求体不是合法 JSON
  400 {"error": {"code": "empty_prompt"}}                [严格模式] prompt 为空字符串
  400 {"error": {"code": "blank_prompt"}}                [严格模式] prompt 为纯空白
  400 {"error": {"code": "illegal_characters"}}          [严格模式] 含 NUL / 控制字符
  413 {"error": {"code": "context_length_exceeded"}}     [严格模式] 超过 MAX_PROMPT_CHARS
  401 {"error": {"code": "invalid_api_key"}}             缺少 / 非法 Authorization
  500 {"error": {"code": "model_internal_error"}}        故障注入（model 字段指定）
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# 契约边界：仅严格模式下生效的 prompt 长度上限
MAX_PROMPT_CHARS = 1000

# 模型名约定
LENIENT_MODEL = "mock-llm"          # 宽松：对齐被测服务实测行为
STRICT_MODEL = "mock-strict"        # 严格：按契约口径校验
UNKNOWN_MODEL = "mock-unknown"      # 不在白名单内的模型名，用于验证模型校验
FATAL_ERROR_MODEL = "error-500"
TRANSIENT_ERROR_PREFIX = "error-500x"

# 严格模式下会拒绝的模型名（宽松模式照常接受）
ALLOWED_MODELS = {"mock-llm", "mock-strict", "gpt-4o-mini", "deepseek-chat", "qwen-plus"}


class MockServerError(RuntimeError):
    """Mock 服务启停失败。"""


def _error(code: str, message: str, status: int) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message}}


def _success(content: str, prompt_chars: int) -> dict[str, Any]:
    """成功响应统一为 OpenAI 兼容形状，与真实被测服务保持一致。

    真实服务返回 choices[0].message.content，Mock 也返回同一形状，
    这样同一份结构断言（openai_chat）在两种环境下都成立。
    """
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": LENIENT_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, prompt_chars),
            "completion_tokens": max(1, len(content)),
            "total_tokens": max(2, prompt_chars + len(content)),
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
    server_version = "MockLLMChat/1.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        """静音访问日志，避免污染 pytest 输出。"""

    def _send(self, status: int, body: dict[str, Any] | str) -> None:
        if isinstance(body, str):
            raw = body.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        else:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/llm/health"):
            self._send(200, {"status": "ok"})
            return
        self._send(404, _error("not_found", f"未知路径 {self.path}", 404)[1])

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/llm/chat":
            self._send(404, _error("not_found", f"未知路径 {self.path}", 404)[1])
            return

        # 1) 鉴权
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
            self._send(401, _error("invalid_api_key", "缺少或非法的 API Key", 401)[1])
            return

        # 2) 请求体必须能解析成 JSON 对象；否则按被测服务实测行为回【纯文本 400】
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, "Failed to parse the request body as JSON: expected value at line 1 column 1")
            return
        if not isinstance(payload, dict):
            self._send(400, "Failed to parse the request body as JSON: root must be an object")
            return

        model = str(payload.get("model") or "")
        # 严格模式 = 按契约口径校验；mock-unknown 专门用来验证模型白名单校验
        strict = model in (STRICT_MODEL, UNKNOWN_MODEL)

        # 3) 故障注入（仅 Mock 支持的模型名）
        if model == FATAL_ERROR_MODEL:
            self._send(500, _error("model_internal_error", "模型服务内部错误（Mock 注入）", 500)[1])
            return
        transient = _transient_failures(model)
        if transient is not None:
            with self.server.counter_lock:  # type: ignore[attr-defined]
                counters = self.server.transient_counters  # type: ignore[attr-defined]
                counters[model] = counters.get(model, 0) + 1
                hit = counters[model]
            if hit <= transient:
                self._send(
                    500,
                    _error("model_internal_error", f"瞬时错误 第 {hit}/{transient} 次（Mock 注入）", 500)[1],
                )
                return

        # 4) 模型白名单：严格模式才校验（宽松模式对齐真实服务的宽容度）
        if strict and model and model not in ALLOWED_MODELS:
            self._send(400, _error("invalid_request_error", f"模型 {model} 不存在", 400)[1])
            return

        # 5) messages / prompt 结构校验：两种模式一致（对齐被测服务的实测行为）
        if "messages" in payload:
            messages = payload["messages"]
            if isinstance(messages, list) and not messages:
                # 实测：业务层返回 400 + Empty input messages（不是 422）
                self._send(400, _error("invalid_request_error", "Empty input messages", 400)[1])
                return

        if "prompt" not in payload:
            self._send(
                422,
                _error("invalid_request_error", "missing field `prompt`", 422)[1],
            )
            return
        prompt = payload["prompt"]
        if not isinstance(prompt, str):
            self._send(
                422,
                _error("invalid_request_error", "prompt should be a string", 422)[1],
            )
            return

        # 6) 慢响应 / 超时注入
        if "__timeout__" in prompt:
            time.sleep(2.0)
        else:
            delay = _slow_seconds(prompt)
            if delay:
                time.sleep(delay)

        # 7) 以下校验【仅严格模式】生效；宽松模式对齐被测服务的实测行为（照常 200）
        if strict:
            if prompt == "":
                self._send(400, _error("empty_prompt", "prompt 为空字符串", 400)[1])
                return
            if prompt.strip() == "":
                self._send(400, _error("blank_prompt", "prompt 为纯空白字符", 400)[1])
                return
            if "\x00" in prompt:
                self._send(400, _error("illegal_characters", "prompt 含 NUL 字符", 400)[1])
                return
            if any(ord(ch) < 0x20 and ch not in "\t\n\r" for ch in prompt):
                self._send(400, _error("illegal_characters", "prompt 含不可打印控制字符", 400)[1])
                return
            if len(prompt) > MAX_PROMPT_CHARS:
                self._send(
                    413,
                    _error(
                        "context_length_exceeded",
                        f"prompt 长度 {len(prompt)} 超过上限 {MAX_PROMPT_CHARS}",
                        413,
                    )[1],
                )
                return

        # 8) 正常返回（模拟模型生成）
        content = f"[mock:{'strict' if strict else 'lenient'}] 已收到 {len(prompt)} 个字符，这是一条模拟模型回复。"
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
