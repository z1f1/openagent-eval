"""请求层：requests.Session 统一封装 host、鉴权 header、超时与重试。

要点
----
- 会话复用：一个 Session 承载连接池与默认 header。
- 重试有边界：只对 5xx 与连接异常重试；4xx 是业务结果不重试；超时不重试，
  否则超时用例会被重试拖成分钟级。
- 全程留痕：入参、响应体、耗时、重试次数写进 Trace，供失败留痕与禅道提单复用。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from config import EnvConfig

MASK = "***"
# 不传 timeout 时用配置默认值；需要“无超时”这种极端场景时显式传 None 之外的语义值
_DEFAULT = object()


class RequestError(RuntimeError):
    """连接类异常：DNS 失败、连接被拒绝、TLS 握手失败。"""


@dataclass
class ApiResponse:
    """一次接口调用的结构化结果（断言层的唯一输入）。"""

    status_code: int | None
    body: dict[str, Any] | None
    raw_text: str
    elapsed: float
    attempts: int
    error_kind: str | None = None  # timeout | transport | None
    error_message: str = ""

    @property
    def error_code(self) -> Any:
        """业务错误码：error.code。"""
        if isinstance(self.body, dict):
            error = self.body.get("error")
            if isinstance(error, dict):
                return error.get("code")
        return None

    @property
    def content(self) -> str | None:
        if isinstance(self.body, dict):
            value = self.body.get("content")
            if isinstance(value, str):
                return value
        return None


@dataclass
class Trace:
    """单条用例的完整留痕信息。"""

    case_id: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """非 JSON 原生类型降级为可读文本。

        留痕数据里可能含 bytes（异常编码用例直接把字节当请求体），
        json.dumps 会抛 TypeError 并让整个报告崩掉，宁可少一点结构也不能丢报告。
        """
        if isinstance(value, bytes):
            return f"<bytes len={len(value)} hex={value.hex()}>"
        return repr(value)

    def as_sections(self) -> dict[str, str]:
        return {
            "请求参数": json.dumps(self.request, ensure_ascii=False, indent=2, default=self._json_safe),
            "响应结果": json.dumps(self.response, ensure_ascii=False, indent=2, default=self._json_safe),
        }


class LLMChatClient:
    """/llm/chat 接口客户端。"""

    def __init__(self, config: EnvConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(config.api.headers)
        self._traces: dict[str, Trace] = {}

    def close(self) -> None:
        self.session.close()

    # ---- 留痕 -----------------------------------------------------------
    def bind(self, node_id: str) -> Trace:
        trace = Trace()
        self._traces[node_id] = trace
        return trace

    def trace_for(self, node_id: str) -> Trace | None:
        return self._traces.get(node_id)

    # ---- 主调用 ---------------------------------------------------------
    def chat(
        self,
        prompt: str | None = None,
        *,
        case_id: str = "",
        model: str | None = None,
        timeout: float | None | object = _DEFAULT,
        raw_body: Any = None,
        trace: Trace | None = None,
    ) -> ApiResponse:
        """调用对话接口。

        Args:
            prompt: 用户输入；``None`` 表示不发送 prompt 字段（缺失场景）。
            raw_body: 直接指定请求体（bytes 或字符串），用于非法 JSON / 异常编码场景。
        """
        effective_timeout = self.config.api.timeout if timeout is _DEFAULT else timeout  # type: ignore[assignment]

        if raw_body is not None:
            payload: Any = raw_body
        else:
            payload = {"model": model or self.config.api.model, "prompt": prompt}
            if prompt is None:
                payload.pop("prompt")

        if trace is not None:
            trace.case_id = case_id
            trace.request = {
                "method": "POST",
                "url": self.config.api.url,
                "headers": self._masked_headers(),
                "timeout": effective_timeout,
                "body": payload,
            }

        response, attempts = self._post_with_retry(payload, effective_timeout)  # type: ignore[arg-type]
        if trace is not None:
            trace.response = {
                "status_code": response.status_code,
                "elapsed_seconds": round(response.elapsed, 4),
                "attempts": attempts,
                "error_kind": response.error_kind,
                "error_message": response.error_message,
                "body": response.body if response.body is not None else response.raw_text[:2000],
            }
        return response

    # ---- 内部实现 -------------------------------------------------------
    def _masked_headers(self) -> dict[str, str]:
        headers = dict(self.session.headers)
        if "Authorization" in headers:
            headers["Authorization"] = f"Bearer {MASK}"
        return headers

    def _post_with_retry(self, payload: Any, timeout: float | None) -> tuple[ApiResponse, int]:
        retry = self.config.retry
        last: ApiResponse | None = None

        for attempt in range(1, retry.max_attempts + 1):
            started = time.perf_counter()
            try:
                data = payload if isinstance(payload, (bytes, str)) else json.dumps(
                    payload, ensure_ascii=False
                ).encode("utf-8")
                resp = self.session.post(self.config.api.url, data=data, timeout=timeout)
                elapsed = time.perf_counter() - started
                last = ApiResponse(
                    status_code=resp.status_code,
                    body=self._parse_json(resp),
                    raw_text=resp.text,
                    elapsed=elapsed,
                    attempts=attempt,
                )
                if resp.status_code < 500:
                    return last, attempt
            except requests.exceptions.Timeout as exc:
                return (
                    ApiResponse(
                        status_code=None,
                        body=None,
                        raw_text="",
                        elapsed=time.perf_counter() - started,
                        attempts=attempt,
                        error_kind="timeout",
                        error_message=str(exc),
                    ),
                    attempt,
                )
            except requests.exceptions.RequestException as exc:
                last = ApiResponse(
                    status_code=None,
                    body=None,
                    raw_text="",
                    elapsed=time.perf_counter() - started,
                    attempts=attempt,
                    error_kind="transport",
                    error_message=str(exc),
                )
                if attempt == retry.max_attempts:
                    raise RequestError(
                        f"连接 {self.config.api.url} 失败（已尝试 {attempt} 次）：{exc}"
                    ) from exc

            if attempt < retry.max_attempts:
                time.sleep(retry.backoff * attempt)

        if last is None:  # pragma: no cover - 理论不可达
            raise RequestError("请求未产生任何响应")
        return last, last.attempts

    @staticmethod
    def _parse_json(resp: requests.Response) -> dict[str, Any] | None:
        try:
            parsed = resp.json()
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
