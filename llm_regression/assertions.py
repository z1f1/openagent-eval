"""断言规则层：把「状态码对了」升级为「可度量」。

五层校验
--------
1. 传输层：HTTP 状态码 / 超时分支 / 重试次数
2. 结构层：响应 JSON 结构（chat_success / error）
3. 类型层：关键字段类型
4. 业务层：业务错误码 error.code
5. 性能层：响应耗时阈值基线（把性能回退纳入回归判定）

所有失败信息都带用例编号与期望/实际值，可直接贴进缺陷单。
"""

from __future__ import annotations

from typing import Any

from client import ApiResponse
from cases import CaseData
# 结构模板：字段名 -> 期望类型
SCHEMA_RULES: dict[str, dict[str, Any]] = {
    "chat_success": {
        "content": str,
        "usage": dict,
    },
    "error": {
        "error": dict,
    },
}


def _fail(case: CaseData, message: str) -> None:
    raise AssertionError(f"[{case.id} {case.title}] {message}")


def check_transport(case: CaseData, response: ApiResponse) -> None:
    """传输层：状态码 / 超时分支 / 重试次数。"""
    if case.expect.get("timeout"):
        if response.error_kind != "timeout":
            _fail(
                case,
                f"期望客户端超时失败，实际 error_kind={response.error_kind!r}, status={response.status_code}",
            )
        return

    if response.error_kind is not None:
        _fail(case, f"请求未得到 HTTP 响应：{response.error_kind} —— {response.error_message}")

    expected = int(case.expect.get("status", 200))
    if response.status_code != expected:
        preview = response.raw_text[:300].replace("\n", " ")
        _fail(case, f"HTTP 状态码不符：期望 {expected}，实际 {response.status_code}；响应预览：{preview}")

    min_attempts = case.expect.get("min_attempts")
    if min_attempts and response.attempts < int(min_attempts):
        _fail(case, f"重试策略未生效：期望至少 {min_attempts} 次尝试，实际 {response.attempts} 次")


def check_schema(case: CaseData, response: ApiResponse) -> None:
    """结构层 + 类型层。"""
    schema_name = case.expect.get("schema")
    if not schema_name:
        return
    rules = SCHEMA_RULES.get(str(schema_name))
    if rules is None:
        _fail(case, f"未知的 schema 规则：{schema_name}")

    body = response.body
    if not isinstance(body, dict):
        _fail(case, f"响应体不是 JSON 对象，实际 {type(body).__name__}，原文：{response.raw_text[:200]}")

    for field_name, expected_type in rules.items():
        if field_name not in body:
            _fail(case, f"响应缺少字段 {field_name}；实际字段：{sorted(body)}")
        if not isinstance(body[field_name], expected_type):
            _fail(
                case,
                f"字段 {field_name} 类型不符：期望 {expected_type.__name__}，"
                f"实际 {type(body[field_name]).__name__}（值：{body[field_name]!r}）",
            )

    if schema_name == "error":
        error = body["error"]
        if not isinstance(error.get("message"), str) or not error["message"].strip():
            _fail(case, f"error.message 缺失或为空：{error!r}")


def check_business_code(case: CaseData, response: ApiResponse) -> None:
    """业务层：业务错误码。"""
    expected = case.expect.get("error_code")
    if expected is None:
        return
    actual = response.error_code
    if actual != expected:
        _fail(case, f"业务错误码不符：期望 {expected}，实际 {actual!r}（响应体：{response.body}）")


def check_content(case: CaseData, response: ApiResponse) -> None:
    """内容层：成功响应必须返回非空生成内容，并按需做最小长度校验。

    只断言 ``len(content) > 0`` 是不够的——返回一个句号也能“通过”。
    """
    if not case.expect.get("content_not_empty"):
        return
    content = response.content
    if content is None or not content.strip():
        _fail(case, f"生成内容为空：{content!r}")
        return
    min_chars = case.expect.get("content_min_chars")
    if min_chars and len(content) < int(min_chars):
        _fail(case, f"生成内容过短：{len(content)} 字符 < 期望下限 {min_chars}")


def check_xss_not_executable(case: CaseData, response: ApiResponse) -> None:
    """安全层：XSS payload 必须被当作普通文本处理。

    合法字符集内的 ``<script>`` 属于**正常输入**（后端应答并转义），
    因此这里不断言状态码，而是断言响应里不能出现可执行的 HTML 片段
    —— 把「输入校验」和「输出编码」两件事分开。
    """
    if not case.expect.get("xss_not_executable"):
        return
    content = response.content or response.raw_text
    lowered = content.lower()
    for pattern in ("<script", "javascript:", "onerror="):
        if pattern in lowered:
            _fail(case, f"响应中回显了可执行片段 {pattern!r}，存在 XSS 风险：{content[:200]!r}")


def check_latency(case: CaseData, response: ApiResponse, baseline: float) -> None:
    """性能层：耗时阈值（用例级 latency_max 优先；写 auto 表示跟随环境基线）。"""
    if case.expect.get("timeout"):
        return
    if case.latency_max is None or str(case.latency_max).strip().lower() == "auto":
        limit = baseline
    else:
        limit = float(case.latency_max)
    if response.elapsed > limit:
        _fail(
            case,
            f"响应耗时回退：{response.elapsed:.3f}s 超过阈值 {limit:.3f}s"
            f"（超过 {response.elapsed - limit:.3f}s）",
        )


def assert_all(case: CaseData, response: ApiResponse, baseline: float) -> None:
    check_transport(case, response)
    check_schema(case, response)
    check_business_code(case, response)
    check_content(case, response)
    check_xss_not_executable(case, response)
    check_latency(case, response, baseline)
