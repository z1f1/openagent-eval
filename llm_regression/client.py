"""请求层：requests.Session 统一封装 host、鉴权 header、超时与重试。

要点
----
- 会话复用：一个 Session 承载连接池与默认 header。
- **本地地址不走代理**：被测服务是 localhost/127.0.0.1 时清空代理并关闭 trust_env。
  否则 HTTP(S)_PROXY 环境变量会把对本地 Mock 的请求也转发出去，
  返回 502 Bad Gateway（实测踩过：设了代理后 30 条用例全挂）。
- 重试有边界：4xx 是业务结果不重试；超时不重试；**连接类异常与 5xx 才重试**，
  并对连接类异常做指数退避（真实服务存在随机 SSL 断连，不重试会让回归随机变红）。
- 失败不中断：重试耗尽后返回带 ``error_kind`` 的响应，让「连接失败」成为可断言的
  失败结果，从而被失败留痕与报告记录下来。
- 全程留痕：入参、响应体、耗时、重试次数写进 Trace，供失败留痕与禅道提单复用。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from config import EnvConfig
from mock_server import LENIENT_MODEL

LOGGER = logging.getLogger("llm-regression.client")
MASK = "***"
# 不传 timeout 时用配置默认值；需要“无超时”这种极端场景时显式传 None 之外的语义值
_DEFAULT = object()

# 本地地址：指向这些主机时不走代理
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def is_local_url(url: str) -> bool:
    """判断 URL 是否指向本机（本地 Mock 服务）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS or host.startswith("127.")


def build_session(config: EnvConfig, session: requests.Session | None = None) -> requests.Session:
    """按目标地址决定代理策略。

    - 本地地址：清空 proxies 并关闭 trust_env，避免 HTTP(S)_PROXY 把请求转发给代理而拿到 502。
    - 远端地址：保持默认行为（沿用环境变量里的代理），否则在必须走代理的网络环境下直连不通。
    """
    sess = session or requests.Session()
    if is_local_url(config.api.url):
        sess.trust_env = False
        sess.proxies = {}
        LOGGER.info("目标为本地地址 %s，已禁用代理（避免 502）", config.api.url)
    return sess


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
        """生成内容提取器，同时支持两种结构。

        - OpenAI 兼容：``choices[0].message.content``（真实服务）
        - 扁平简化：``content``（早期 Mock 形状，保留兼容）
        """
        body = self.body
        if not isinstance(body, dict):
            return None

        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]

        value = body.get("content")
        return value if isinstance(value, str) else None


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
        # 本地地址自动禁用代理，避免被 HTTP(S)_PROXY 拦成 502
        self.session = build_session(config, session)
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
        mock_model: str | None = None,
        timeout: float | None | object = _DEFAULT,
        raw_body: Any = None,
        trace: Trace | None = None,
    ) -> ApiResponse:
        """调用对话接口。

        Args:
            prompt: 用户输入；``None`` 表示不发送 prompt 字段（缺失场景）。
            model: 覆盖默认模型（真实环境生效）。
            mock_model: 仅 mock 环境生效的模型覆盖。``mock-strict`` 让 Mock 走
                「应有输入校验」的严格契约，默认 ``mock-llm`` 对齐被测服务的宽松实测行为。
            raw_body: 直接指定请求体（bytes 或字符串），用于非法 JSON / 异常编码场景。
        """
        effective_timeout = self.config.api.timeout if timeout is _DEFAULT else timeout  # type: ignore[assignment]

        if raw_body is not None:
            payload: Any = raw_body
        else:
            # mock 环境：默认用宽松模型（对齐被测服务实测行为）；
            # 显式指定 mock_model 时才把该名字直接发给 Mock（例如 mock-strict / mock-unknown）
            resolved_model = (
                (mock_model or LENIENT_MODEL) if self.config.is_mock else (model or self.config.api.model)
            )
            if self.config.is_openai_style:
                # OpenAI 兼容格式：prompt 放进 messages[0].content
                message: dict[str, Any] = {"role": "user"}
                if prompt is not None:
                    message["content"] = prompt
                payload = {"model": resolved_model, "messages": [message]}
            else:
                payload = {"model": resolved_model, "prompt": prompt}
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

    @staticmethod
    def _backoff_delay(base: float, attempt: int, cap: float = 8.0) -> float:
        """指数退避，避免对瞬时故障做密集重试。"""
        return min(base * (2 ** (attempt - 1)), cap)

    def _post_with_retry(self, payload: Any, timeout: float | None) -> tuple[ApiResponse, int]:
        """发送请求并按错误类型决定是否重试。

        重试策略（三类错误区别对待）：
        - **超时**：不重试。重试会把「超时用例」拖成分钟级，且客户端已放弃这次调用。
        - **连接类异常**（SSLEOFError / ConnectionReset / 读超时等）：重试 + 指数退避。
          实测被真实服务的随机 SSL 断连干扰，不重试会导致 RAG 回归随机变红。
        - **5xx**：重试 + 指数退避（服务端临时故障）。
        - **4xx / 2xx**：直接返回，4xx 是业务结果，重试没有意义。

        重试耗尽后**返回带 error_kind 的响应**而不是抛异常 —— 让「连接失败」成为一条
        可断言的失败结果（失败留痕与报告才能记录到它），而不是中断整个会话。
        """
        retry = self.config.retry
        last: ApiResponse | None = None

        for attempt in range(1, retry.max_attempts + 1):
            started = time.perf_counter()
            try:
                data = payload if isinstance(payload, (bytes, str)) else json.dumps(
                    payload, ensure_ascii=False
                ).encode("utf-8")
                # 用例数据里可以写 {model} 占位符，按当前环境替换成实际模型名，
                # 这样同一份 raw_body 在 mock / real 两种环境下都能用
                if isinstance(data, (bytes, bytearray)):
                    data = data.replace(b"{model}", self.config.api.model.encode("utf-8"))
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
                # 超时不重试：立刻返回，让超时用例快速失败
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
                    error_kind="connection",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                LOGGER.warning(
                    "第 %s/%s 次请求连接失败（%s），%s",
                    attempt,
                    retry.max_attempts,
                    type(exc).__name__,
                    "将退避后重试" if attempt < retry.max_attempts else "重试已耗尽",
                )

            if attempt < retry.max_attempts:
                time.sleep(self._backoff_delay(retry.backoff, attempt))

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
