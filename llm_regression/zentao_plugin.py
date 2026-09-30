"""禅道提单：把失败用例转成规范缺陷单，附件自动带上复现信息。

凭证全部走环境变量或 .env（**不再明文写在代码里**）：
    ZENTAO_BASE_URL   例如 http://127.0.0.1:81/zentao
    ZENTAO_TOKEN      个人令牌；或用 ZENTAO_ACCOUNT + ZENTAO_PASSWORD 自动换 token
    ZENTAO_PRODUCT_ID 产品 ID（必填）
    ZENTAO_MODULE_ID  模块 ID（可选，默认 0）
    ZENTAO_ENABLED    置为 1 时才真正提单（默认不启用，避免本地跑失败就污染缺陷库）

只要 ZENTAO_BASE_URL / 凭证 / ZENTAO_PRODUCT_ID 缺任意一个，本模块静默跳过；
提单失败只记日志，绝不改变用例的通过/失败判定。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import requests

from env_config import get as env_get

if TYPE_CHECKING:  # pragma: no cover
    from cases import CaseData
    from client import Trace

LOGGER = logging.getLogger("llm-regression.zentao")
TIMEOUT = 10.0


def _settings() -> dict[str, str] | None:
    """读取并校验禅道配置；未启用或配置不全时返回 None。"""
    if env_get("ZENTAO_ENABLED").lower() not in ("1", "true", "yes"):
        LOGGER.info("未启用禅道自动提单（设置 ZENTAO_ENABLED=1 启用）")
        return None

    base_url = env_get("ZENTAO_BASE_URL").rstrip("/")
    token = env_get("ZENTAO_TOKEN")
    product_id = env_get("ZENTAO_PRODUCT_ID")
    has_login = bool(env_get("ZENTAO_ACCOUNT") and env_get("ZENTAO_PASSWORD"))
    missing = [
        name
        for name, value in (("ZENTAO_BASE_URL", base_url), ("ZENTAO_PRODUCT_ID", product_id))
        if not value
    ]
    if not token and not has_login:
        missing.append("ZENTAO_TOKEN 或 ZENTAO_ACCOUNT+ZENTAO_PASSWORD")
    if missing:
        LOGGER.warning("禅道配置不完整，跳过提单：缺少 %s", ", ".join(missing))
        return None

    return {
        "base_url": base_url,
        "token": token,
        "product_id": product_id,
        "module_id": env_get("ZENTAO_MODULE_ID", "0") or "0",
    }


def build_steps(case: "CaseData | None", trace: "Trace | None", longrepr: str) -> str:
    """缺陷单正文：前置条件 / 复现步骤 / 请求参数 / 实际结果 / 期望结果。

    目标是让开发拿到单子就能复现，不用回来问「怎么重现」。
    """
    lines: list[str] = []
    if case is not None:
        lines.append(f"【用例编号】{case.id}")
        lines.append(f"【场景分类】{case.scenario_label}")
        lines.append(f"【用例标题】{case.title}")
        if case.design:
            lines.append(f"【设计方法】{case.design.strip()}")
        lines.append(f"【期望结果】{case.expect}")

    lines.append("【复现步骤】")
    lines.append("1. 启动被测服务（或自建 Mock：python run_tests.py --env mock）")
    lines.append(f"2. 以相同参数请求 {os.environ.get('LLM_BASE_URL', '(见用例留痕中的 url)')}")
    lines.append("3. 观察响应状态码、业务错误码与响应耗时")

    if trace is not None:
        import json

        lines.append("【请求参数】")
        lines.append(json.dumps(trace.request, ensure_ascii=False, indent=2, default=repr))
        lines.append("【实际结果】")
        lines.append(json.dumps(trace.response, ensure_ascii=False, indent=2, default=repr))

    lines.append("【失败详情】")
    lines.append(longrepr)
    return "\n".join(lines)


def submit_bug(
    node_id: str,
    case: "CaseData | None",
    trace: "Trace | None",
    longrepr: str,
) -> str | None:
    """提交缺陷，返回缺陷编号/响应摘要；未启用或失败时返回 None。"""
    settings = _settings()
    if settings is None:
        return None

    if case is not None:
        title = f"【LLM接口回归失败】{case.id} {case.scenario_label} {case.title}"
    else:
        title = f"【LLM接口回归失败】{node_id}"

    payload: dict[str, Any] = {
        "product": int(settings["product_id"]),
        "title": title,
        "steps": build_steps(case, trace, longrepr),
        "severity": 3,
        "pri": 3,
        "type": "codeerror",
    }
    if settings["module_id"] not in ("", "0"):
        payload["module"] = int(settings["module_id"])

    response = requests.post(
        f"{settings['base_url']}/bugs",
        json=payload,
        headers={
            "Token": settings["token"],
            "Authorization": f"Bearer {settings['token']}",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    )
    if response.status_code >= 400:
        LOGGER.error("禅道提单返回 %s：%s", response.status_code, response.text[:300])
        return None
    return response.text[:300]
