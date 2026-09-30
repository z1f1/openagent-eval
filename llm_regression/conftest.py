"""pytest 全局配置：fixture（前置/teardown）、参数化驱动、失败留痕 + 禅道提单。

分层对应
--------
session 级 fixture   → 按环境拉起 Mock 服务 / 准备真实接口连接，统一鉴权与超时
function 级 fixture  → 每条用例绑定一份 Trace
pytest_generate_tests → 数据层参数化：四类场景一键驱动，新增用例只加数据
pytest_runtest_makereport → 失败留痕（写进 HTML 报告）+ 自动提禅道缺陷

禅道凭证**不在本文件**，全部走环境变量（见 zentao_plugin.py），避免明文进 git 历史。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

import pytest

try:
    import pytest_html
except ImportError:  # pragma: no cover - 未安装 pytest-html 时降级为纯 pytest 输出
    pytest_html = None  # type: ignore[assignment]

from cases import CaseData, load_cases
from client import LLMChatClient, Trace
from config import ConfigError, EnvConfig, load_config
from mock_server import MockLLMChatServer, MockServerError

LOGGER = logging.getLogger("llm-regression")
REPORT_DIR = Path(__file__).resolve().parent / "reports"
SCENARIOS = ("normal", "empty", "oversized", "illegal")
# 单块留痕内容上限：超长用例的请求体可能很长，全量写进报告会让报告不可读
MAX_TRACE_CHARS = 4000


# --------------------------------------------------------------------------
# 命令行选项
# --------------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("llm-regression")
    group.addoption(
        "--env",
        action="store",
        default="mock",
        choices=("mock", "real"),
        help="运行环境：mock=自建 Mock 服务（默认，稳定可复现）；real=真实接口（凭证走环境变量）",
    )
    group.addoption(
        "--scenario",
        action="store",
        default=None,
        choices=SCENARIOS,
        help="只跑某一类场景：normal/empty/oversized/illegal",
    )


# --------------------------------------------------------------------------
# Session 级前置
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def env_name(request: pytest.FixtureRequest) -> str:
    return str(request.config.getoption("--env"))


@pytest.fixture(scope="session")
def env_config(env_name: str) -> EnvConfig:
    """配置层：缺凭证 / 地址非法在这里就报清楚，避免拿空地址发请求。"""
    try:
        return load_config(env_name)
    except ConfigError as exc:
        pytest.exit(f"配置加载失败（--env {env_name}）：{exc}", returncode=2)


@pytest.fixture(scope="session")
def mock_server(env_config: EnvConfig) -> Iterator[MockLLMChatServer | None]:
    """mock 环境拉起自建 Mock 服务；real 环境不做任何事。"""
    if not env_config.is_mock:
        yield None
        return

    server = MockLLMChatServer(env_config.mock_server.host, env_config.mock_server.port)
    try:
        server = server.start()
    except MockServerError:
        # 配置端口被占用时退化为随机端口，不让环境问题卡死回归
        server = MockLLMChatServer(env_config.mock_server.host, 0).start()
        LOGGER.warning("配置端口不可用，Mock 已改用随机端口 %s", server.port)
    LOGGER.info("Mock 服务已启动：%s/llm/chat", server.base_url)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="session")
def active_config(env_config: EnvConfig, mock_server: MockLLMChatServer | None) -> EnvConfig:
    """实际生效的配置（Mock 可能使用随机端口）。"""
    if mock_server is None:
        return env_config
    return replace(env_config, api=replace(env_config.api, base_url=mock_server.base_url))


@pytest.fixture(scope="session")
def client(active_config: EnvConfig) -> Iterator[LLMChatClient]:
    """请求层：会话复用 + 统一鉴权 + 统一超时/重试。"""
    chat_client = LLMChatClient(active_config)
    LOGGER.info("被测接口：%s（env=%s）", active_config.api.url, active_config.name)
    try:
        yield chat_client
    finally:
        chat_client.close()


@pytest.fixture(scope="session")
def latency_baseline(active_config: EnvConfig) -> float:
    return active_config.latency_baseline


# --------------------------------------------------------------------------
# 数据层参数化
# --------------------------------------------------------------------------
def _scenario_of(metafunc: pytest.Metafunc) -> str | None:
    for marker in metafunc.definition.iter_markers():
        if marker.name in SCENARIOS:
            return marker.name
    return None


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """把 cases/*.yaml 注入用例函数：新增场景只需加一行数据。"""
    if "case" not in metafunc.fixturenames:
        return

    scenario = _scenario_of(metafunc)
    if scenario is None:
        pytest.exit(
            "用例文件未声明场景标记，请加 pytest.mark.normal/empty/oversized/illegal",
            returncode=2,
        )

    # --scenario 只筛选场景：未选中的用例显式 skip，保持报告可读
    only = metafunc.config.getoption("--scenario")
    if only and only != scenario:
        metafunc.parametrize(
            "case",
            [pytest.param(None, marks=pytest.mark.skip(reason=f"--scenario 未选中 {scenario} 场景"))],
        )
        return

    env_name = str(metafunc.config.getoption("--env"))
    cases = load_cases(scenario, target=env_name)
    if not cases:
        metafunc.parametrize(
            "case", [pytest.param(None, marks=pytest.mark.skip(reason="该场景暂无用例数据"))]
        )
        return
    metafunc.parametrize("case", cases, ids=[c.id for c in cases])


# --------------------------------------------------------------------------
# 每条用例的留痕绑定
# --------------------------------------------------------------------------
@pytest.fixture
def trace(request: pytest.FixtureRequest, client: LLMChatClient) -> Iterator[Trace]:
    bound = client.bind(request.node.nodeid)
    request.node._llm_trace = bound  # type: ignore[attr-defined]
    yield bound


# --------------------------------------------------------------------------
# 失败留痕 + 禅道提单
# --------------------------------------------------------------------------
def _clip(text: str) -> str:
    if len(text) <= MAX_TRACE_CHARS:
        return text
    return f"{text[:MAX_TRACE_CHARS]}\n…（留痕已截断，原文共 {len(text)} 字符）"


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]):
    """用例失败时：① 请求参数/响应体/耗时写进 HTML 报告 ② 自动提禅道缺陷。"""
    outcome = yield
    report = outcome.get_result()

    callspec = getattr(item, "callspec", None)
    case: CaseData | None = callspec.params.get("case") if callspec is not None else None
    setattr(report, "_llm_case", case)

    if report.when != "call" or not report.failed:
        return

    trace_obj: Trace | None = getattr(item, "_llm_trace", None)
    sections: list[tuple[str, str]] = []
    if case is not None:
        sections.append(("用例编号", case.id))
        sections.append(("场景分类", case.scenario_label))
        sections.append(("用例标题", case.title))
        sections.append(("设计方法", case.design))
        sections.append(("期望结果", json.dumps(case.expect, ensure_ascii=False, default=repr)))
    if trace_obj is not None:
        sections.extend(trace_obj.as_sections().items())

    if pytest_html is not None:
        report.extras = list(getattr(report, "extras", [])) + [
            pytest_html.extras.text(_clip(content), name=label) for label, content in sections
        ]

    LOGGER.error("用例失败留痕：%s | 字段：%s", item.nodeid, " | ".join(label for label, _ in sections))

    # 禅道提单：只在配置了环境变量时启用；提单失败绝不能影响回归判定
    try:
        from zentao_plugin import submit_bug

        bug_id = submit_bug(item.nodeid, case, trace_obj, report.longreprtext)
        if bug_id:
            LOGGER.warning("已自动提交禅道缺陷：%s", bug_id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("禅道提单失败（不影响回归结果）：%s", exc)


def pytest_html_report_title(report: Any) -> None:
    report.title = "AI 大模型对话接口 · 自动化回归报告"


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_header(cells: list[Any]) -> None:
    cells.insert(2, "<th>场景 / 用例</th>")


@pytest.hookimpl(optionalhook=True)
def pytest_html_results_table_row(report: Any, cells: list[Any]) -> None:
    case: CaseData | None = getattr(report, "_llm_case", None)
    if case is None:
        cells.insert(2, "<td>-</td>")
    else:
        cells.insert(2, f"<td>{case.scenario_label}<br><b>{case.id}</b> {case.title}</td>")


def pytest_html_results_summary(prefix: list[str], summary: list[str], postfix: list[str]) -> None:
    """报告头部的执行元信息，保证报告本身可追溯。"""
    env_name = getattr(pytest, "_llm_env_name", "mock")
    env = load_env_safely(env_name)
    mode = "自建 Mock 服务（稳定复现）" if (env and env.is_mock) else "真实接口"
    prefix.append("<div class='llm-meta'>")
    prefix.append(f"<p><b>被测环境：</b>{mode}（{env_name}）</p>")
    if env is not None:
        prefix.append(f"<p><b>接口地址：</b>{env.api.url}</p>")
        prefix.append(f"<p><b>模型：</b>{env.api.model}</p>")
        prefix.append(f"<p><b>耗时基线：</b>{env.latency_baseline}s</p>")
        prefix.append(f"<p><b>重试策略：</b>最多 {env.retry.max_attempts} 次</p>")
    prefix.append("</div>")


def load_env_safely(name: str) -> EnvConfig | None:
    try:
        return load_config(name)
    except ConfigError:
        return None


def pytest_configure(config: pytest.Config) -> None:
    """缓存环境名供报告钩子使用；real 环境在此校验凭证，缺凭证直接终止。"""
    env_name = str(config.getoption("--env"))
    setattr(pytest, "_llm_env_name", env_name)
    setattr(pytest, "_llm_scenario", config.getoption("--scenario"))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        env = load_config(env_name)
    except ConfigError as exc:
        raise pytest.UsageError(f"配置加载失败（--env {env_name}）：{exc}") from exc

    if not env.is_mock:
        missing = [
            name
            for name, value in (("LLM_BASE_URL", env.api.base_url), ("LLM_API_KEY", env.api.api_key))
            if not value
        ]
        if missing:
            raise pytest.UsageError(
                "real 环境缺少环境变量："
                + ", ".join(missing)
                + '（示例：$env:LLM_BASE_URL="https://api.deepseek.com"; $env:LLM_API_KEY="sk-..."）'
            )
