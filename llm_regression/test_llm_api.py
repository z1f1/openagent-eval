"""用例层：四类场景回归。

每条函数只做三件事：取数据 → 调用接口 → 跑断言。用例数据在 cases/*.yaml，
断言规则在 assertions.py，接口封装在 client.py —— 结构变了先改对应层，不动这里。

原始用例编号沿用：TC01 正常 / TC02 空输入 / TC03 超长 / TC04 XSS。
"""

from __future__ import annotations

import logging

import pytest

from assertions import assert_all
from cases import CaseData
from client import LLMChatClient, Trace

LOGGER = logging.getLogger("llm-regression")

pytestmark = pytest.mark.regression


def _run(case: CaseData, client: LLMChatClient, trace: Trace, baseline: float) -> None:
    """统一的「调用 + 断言 + 日志」流程，四个场景共用。"""
    LOGGER.info("执行用例 %s：%s", case.id, case.title)
    prompt = case.prompt if case.send_prompt else None
    response = client.chat(
        prompt,
        case_id=case.id,
        model=case.model,
        timeout=case.request_timeout,
        raw_body=case.raw_body,
        trace=trace,
    )
    LOGGER.info(
        "用例 %s 响应：status=%s code=%s attempts=%s error_kind=%s elapsed=%.3fs",
        case.id,
        response.status_code,
        response.error_code,
        response.attempts,
        response.error_kind,
        response.elapsed,
    )
    assert_all(case, response, baseline)


@pytest.mark.normal
def test_normal_chat(
    case: CaseData, client: LLMChatClient, trace: Trace, latency_baseline: float
) -> None:
    """TC01/TC05 正常场景：返回 200、结构完整、内容非空且不过短。"""
    _run(case, client, trace, latency_baseline)


@pytest.mark.empty
def test_empty_prompt(
    case: CaseData, client: LLMChatClient, trace: Trace, latency_baseline: float
) -> None:
    """TC02/TC06~TC09 空输入：必须明确返回 4xx + 业务错误码，不允许静默成功。"""
    _run(case, client, trace, latency_baseline)


@pytest.mark.oversized
def test_oversized_prompt(
    case: CaseData, client: LLMChatClient, trace: Trace, latency_baseline: float
) -> None:
    """TC03/TC10~TC12 超长文本：边界三点夹逼上限 1000，越界要快速拒绝。"""
    _run(case, client, trace, latency_baseline)


@pytest.mark.illegal
def test_illegal_prompt(
    case: CaseData, client: LLMChatClient, trace: Trace, latency_baseline: float
) -> None:
    """TC04/TC13~TC19 非法字符与异常分支：非法字符拒绝、XSS 不回显、重试与超时可判定。"""
    _run(case, client, trace, latency_baseline)
