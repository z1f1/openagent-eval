"""运行信息上传禅道：把一次回归的执行结果与报告归档成禅道里的一条记录。

实测确认的接口契约（v2，本地禅道）
----------------------------------
1. 登录取 token      POST /api.php/v2/users/login  {"account","password"} -> {"token"}
2. 创建记录          POST /api.php/v2/bugs         json: productID/title/steps/openedBuild
                     -> {"status":"success","id":N}
3. 上传附件          POST /api.php/v2/files        multipart: file + objectType + objectID
                     -> {"status":"success","data":{"id":N}}

**顺序很重要：先创建记录拿到 id，再带 objectType/objectID 上传附件。**

踩过的坑（受控实验结论，别再走回头路）：
- 把 ``uid`` 放进创建记录的 payload 会**阻止**附件关联；
- 不带 uid 时禅道会把该产品下所有「未挂载的文件」自动挂到这条新记录上——
  这是隐式行为，一旦有残留文件或并发就会挂错。
因此采用显式 objectID 绑定：每份附件必然只属于本次运行记录。

4. ``openedBuild`` 是 Bug 的必填项；产品没有版本数据时用 ``"trunk"`` 即可通过校验。

凭证全部走环境变量，代码里不留明文。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

from env_config import get as env_get

LOGGER = logging.getLogger("llm-regression.zentao")
TIMEOUT = 30.0

# 记录正文的展示顺序（与报告、Markdown 摘要保持一致）
STATUS_LABELS = {"passed": "通过", "failed": "失败", "error": "错误", "skipped": "跳过"}


class ZentaoError(RuntimeError):
    """禅道交互失败。"""


@dataclass
class CaseResult:
    """一条用例的执行结果。"""

    case_id: str
    title: str
    scenario: str
    outcome: str  # passed / failed / error / skipped
    duration: float = 0.0
    detail: str = ""
    status_code: Any = None
    error_code: Any = None
    elapsed: float | None = None
    attempts: int | None = None

    @property
    def outcome_label(self) -> str:
        return STATUS_LABELS.get(self.outcome, self.outcome)


@dataclass
class RunResult:
    """一次回归的整体执行信息。"""

    environment: str
    target: str
    api_url: str
    model: str
    command: str
    started_at: str
    duration: float = 0.0
    latency_baseline: float = 0.0
    retry_max: int = 0
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.outcome == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for c in self.cases if c.outcome in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for c in self.cases if c.outcome == "skipped")

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def pass_rate(self) -> float:
        counted = self.total - self.skipped
        return (self.passed / counted * 100) if counted else 0.0


# --------------------------------------------------------------------------
# 环境变量
# --------------------------------------------------------------------------
def _settings(require_enabled: bool = True) -> dict[str, str] | None:
    if require_enabled and env_get("ZENTAO_ENABLED").lower() not in ("1", "true", "yes"):
        LOGGER.info("未启用禅道上传（设置 ZENTAO_ENABLED=1 启用）")
        return None

    base_url = env_get("ZENTAO_BASE_URL").rstrip("/")
    product_id = env_get("ZENTAO_PRODUCT_ID")
    missing = [
        name
        for name, value in (("ZENTAO_BASE_URL", base_url), ("ZENTAO_PRODUCT_ID", product_id))
        if not value
    ]
    if missing:
        raise ZentaoError("禅道配置不完整：缺少 " + ", ".join(missing))

    settings = {
        "base_url": base_url,
        "product_id": product_id,
        "account": env_get("ZENTAO_ACCOUNT"),
        "password": env_get("ZENTAO_PASSWORD"),
        "token": env_get("ZENTAO_TOKEN"),
        "build": env_get("ZENTAO_BUILD", "trunk") or "trunk",
        "severity": env_get("ZENTAO_SEVERITY", "3") or "3",
        "pri": env_get("ZENTAO_PRI", "3") or "3",
    }
    if not settings["token"] and not (settings["account"] and settings["password"]):
        raise ZentaoError("缺少凭证：需要 ZENTAO_TOKEN，或 ZENTAO_ACCOUNT + ZENTAO_PASSWORD")
    return settings


def login(settings: dict[str, str]) -> str:
    """优先用现成 token；否则用账号密码换取 token。"""
    if settings["token"]:
        return settings["token"]
    resp = requests.post(
        f"{settings['base_url']}/api.php/v2/users/login",
        json={"account": settings["account"], "password": settings["password"]},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise ZentaoError(f"禅道登录失败 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        token = resp.json().get("token")
    except ValueError as exc:
        raise ZentaoError(f"禅道登录响应不是 JSON：{resp.text[:200]}") from exc
    if not token:
        raise ZentaoError(f"禅道登录未返回 token：{resp.text[:200]}")
    return str(token)


# --------------------------------------------------------------------------
# 正文与摘要
# --------------------------------------------------------------------------
def build_steps(run: RunResult) -> str:
    """记录正文：环境信息 + 结果明细表 + 失败清单。"""
    lines = [
        "【执行环境】" + ("自建 Mock 服务（稳定复现）" if run.target.startswith("mock") else "真实接口"),
        f"【接口地址】{run.api_url}",
        f"【模型】{run.model}",
        f"【执行命令】{run.command}",
        f"【开始时间】{run.started_at}",
        f"【执行耗时】{run.duration:.2f}s",
        f"【耗时基线】{run.latency_baseline}s（重试上限 {run.retry_max} 次）",
        "",
        f"【结果总览】共 {run.total} 条：通过 {run.passed}、失败 {run.failed}、跳过 {run.skipped}，通过率 {run.pass_rate:.1f}%",
        "",
        "【用例明细】",
        "| 编号 | 场景 | 结果 | 耗时(s) | HTTP | 业务错误码 | 重试 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in run.cases:
        lines.append(
            f"| {case.case_id} | {case.scenario} | {case.outcome_label} | {case.duration:.3f} "
            f"| {case.status_code if case.status_code is not None else '-'} "
            f"| {case.error_code if case.error_code else '-'} "
            f"| {case.attempts if case.attempts is not None else '-'} |"
        )

    failures = [c for c in run.cases if c.outcome in ("failed", "error")]
    if failures:
        lines.append("")
        lines.append("【失败清单】")
        for case in failures:
            lines.append(f"- {case.case_id} {case.title}：{case.detail or '见附件报告'}")

    lines.append("")
    lines.append("【附件】pytest-html 可视化报告、JUnit XML（失败用例含请求参数与响应结果）")
    return "\n".join(lines)


def render_markdown(run: RunResult) -> str:
    """与禅道记录内容一致的 Markdown 摘要，便于本地留档。"""
    return f"# 自动化回归运行信息（{run.environment}）\n\n```\n{build_steps(run)}\n```\n"


def to_dict(run: RunResult) -> dict[str, Any]:
    return asdict(run)


# --------------------------------------------------------------------------
# 上传
# --------------------------------------------------------------------------
def upload_file(
    settings: dict[str, str],
    token: str,
    path: Path,
    *,
    object_type: str,
    object_id: int,
) -> int | None:
    """上传附件并显式绑定到指定对象，返回禅道文件 id。"""
    suffix = path.suffix.lower()
    mime = {
        ".html": "text/html",
        ".xml": "application/xml",
        ".md": "text/markdown",
        ".log": "text/plain",
    }.get(suffix, "application/octet-stream")

    resp = requests.post(
        f"{settings['base_url']}/api.php/v2/files",
        headers={"Token": token},
        files={"file": (path.name, path.read_bytes(), mime)},
        data={"objectType": object_type, "objectID": str(object_id)},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        LOGGER.error("附件上传失败 %s HTTP %s：%s", path.name, resp.status_code, resp.text[:200])
        return None
    try:
        body = resp.json()
    except ValueError:
        LOGGER.error("附件上传响应不是 JSON：%s", resp.text[:200])
        return None
    if body.get("status") != "success":
        LOGGER.error("附件上传未成功 %s：%s", path.name, body)
        return None
    file_id = (body.get("data") or {}).get("id") or body.get("id")
    LOGGER.info("附件已上传并绑定到 %s#%s：%s -> file id=%s", object_type, object_id, path.name, file_id)
    return int(file_id) if file_id else None


def create_record(settings: dict[str, str], token: str, run: RunResult) -> tuple[int | None, str]:
    """创建运行信息记录；返回 (记录 id, 消息)。"""
    title = (
        f"【自动化回归】{run.environment} {run.passed}/{run.total} 通过"
        f"（失败 {run.failed}） {run.started_at[:16]}"
    )
    payload = {
        "productID": int(settings["product_id"]),
        "title": title,
        "steps": build_steps(run),
        "severity": int(settings["severity"]),
        "pri": int(settings["pri"]),
        "type": "codeerror",
        # 影响版本是 Bug 必填项；没有版本数据时用 trunk
        "openedBuild": [settings["build"]],
    }
    resp = requests.post(
        f"{settings['base_url']}/api.php/v2/bugs",
        headers={"Token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}：{resp.text[:200]}"
    try:
        body = resp.json()
    except ValueError:
        return None, f"响应不是 JSON：{resp.text[:200]}"

    if body.get("status") == "success" or body.get("result") == "success":
        return int(body.get("id") or (body.get("data") or {}).get("id") or 0) or None, body.get("message", "成功")
    return None, json.dumps(body, ensure_ascii=False)[:300]


def submit_run(run: RunResult, attachments: list[Path], dry_run: bool = False) -> dict[str, Any]:
    """把一次回归的运行信息提交到禅道。

    顺序：先建记录 -> 再带 objectID 上传附件（显式绑定，不依赖隐式行为）。

    Returns:
        {"uploaded": bool, "record_id": int|None, "files": [...], "message": str}
    """
    result: dict[str, Any] = {"uploaded": False, "record_id": None, "files": [], "message": ""}
    settings = _settings()
    if settings is None:
        result["message"] = "未启用（ZENTAO_ENABLED != 1）"
        return result

    if dry_run:
        result["message"] = "dry-run：仅生成正文，未提交禅道"
        LOGGER.info("dry-run 正文：\n%s", build_steps(run))
        return result

    token = login(settings)

    record_id, message = create_record(settings, token, run)
    result["record_id"] = record_id
    result["message"] = message
    if record_id is None:
        LOGGER.error("运行信息记录创建失败：%s", message)
        return result

    for path in attachments:
        if not path.exists():
            LOGGER.warning("附件不存在，跳过：%s", path)
            continue
        file_id = upload_file(settings, token, path, object_type="bug", object_id=record_id)
        if file_id:
            result["files"].append({"name": path.name, "id": file_id})

    result["uploaded"] = True
    LOGGER.warning(
        "运行信息已上传禅道：记录 id=%s，附件 %d 个 -> %s/bug-view-%s.html",
        record_id,
        len(result["files"]),
        settings["base_url"],
        record_id,
    )
    return result


__all__ = [
    "CaseResult",
    "RunResult",
    "ZentaoError",
    "build_steps",
    "render_markdown",
    "submit_run",
    "to_dict",
]
