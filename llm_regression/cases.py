"""数据层：加载 cases.yaml 参数化用例数据。

设计目标：新增场景只需加一行数据，用例代码与断言规则都不用改。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODULE_ROOT = Path(__file__).resolve().parent
CASES_DIR = MODULE_ROOT / "cases"

# 数据里用 {{'长' * 1001}} 声明超长内容，避免在 YAML 里堆一千多字面字符
_REPEAT = re.compile(r"\{\{\s*'([^']*)'\s*\*\s*(\d+)\s*\}\}")

SCENARIO_LABELS = {
    "normal": "正常（有效等价类）",
    "empty": "空输入",
    "oversized": "超长文本（边界值）",
    "illegal": "非法字符 / 异常编码",
}


class CaseDataError(RuntimeError):
    """用例数据格式错误。"""


class _Loader(yaml.SafeLoader):
    """SafeLoader + !raw 标签：原样构造非法请求体。"""


def _raw_constructor(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_Loader.add_constructor("!raw", _raw_constructor)


def _expand_repeat(value: Any) -> Any:
    if isinstance(value, str):
        match = _REPEAT.fullmatch(value.strip())
        if match:
            return match.group(1) * int(match.group(2))
    return value


@dataclass(frozen=True)
class CaseData:
    """一条参数化用例数据。"""

    id: str
    title: str
    scenario: str
    design: str = ""
    prompt: Any = None
    send_prompt: bool = True
    model: str | None = None
    # 仅 mock 环境生效的模型覆盖：
    # mock-llm        = 宽松（对齐被测开源服务的实测行为）
    # mock-strict     = 严格（按「应有输入校验」的契约口径，用于契约对照）
    mock_model: str | None = None
    raw_body: Any = None
    expect: dict[str, Any] = field(default_factory=dict)
    latency_max: float | str | None = None
    request_timeout: float | None = None
    mock_only: bool = False

    @property
    def scenario_label(self) -> str:
        return SCENARIO_LABELS.get(self.scenario, self.scenario)

    @property
    def param_id(self) -> str:
        return f"{self.scenario}-{self.id}"


def _parse_case(raw: dict[str, Any], source: Path) -> CaseData:
    if not isinstance(raw, dict):
        raise CaseDataError(f"{source.name}: 用例条目必须是映射")
    missing = [key for key in ("id", "title", "scenario") if not raw.get(key)]
    if missing:
        raise CaseDataError(f"{source.name}: 用例缺少字段 {missing}（{raw}）")
    if not isinstance(raw.get("expect"), dict):
        raise CaseDataError(f"{source.name}: 用例 {raw['id']} 缺少 expect 映射")

    return CaseData(
        id=str(raw["id"]),
        title=str(raw["title"]),
        scenario=str(raw["scenario"]),
        design=str(raw.get("design", "")),
        prompt=_expand_repeat(raw.get("prompt")),
        send_prompt=bool(raw.get("send_prompt", True)),
        model=raw.get("model"),
        mock_model=raw.get("mock_model"),
        raw_body=_expand_repeat(raw.get("raw_body")),
        expect=dict(raw["expect"]),
        latency_max=raw.get("latency_max"),
        request_timeout=raw.get("request_timeout"),
        mock_only=bool(raw.get("mock_only", False)),
    )


def load_cases(scenario: str | None = None, target: str | None = None) -> list[CaseData]:
    """加载用例数据；target=real 时跳过 mock_only 用例。"""
    if not CASES_DIR.exists():
        raise CaseDataError(f"用例数据目录不存在：{CASES_DIR}")

    if scenario:
        file = CASES_DIR / f"{scenario}.yaml"
        if not file.exists():
            raise CaseDataError(f"场景数据文件不存在：{file}")
        files = [file]
    else:
        files = sorted(CASES_DIR.glob("*.yaml"))

    cases: list[CaseData] = []
    seen: set[str] = set()
    for file in files:
        raw_list = yaml.load(file.read_text(encoding="utf-8"), Loader=_Loader) or []
        if not isinstance(raw_list, list):
            raise CaseDataError(f"{file.name}: 顶层结构必须是列表")
        for raw in raw_list:
            case = _parse_case(raw, file)
            if case.mock_only and target == "real":
                continue
            if case.id in seen:
                raise CaseDataError(f"用例编号重复：{case.id}（{file.name}）")
            seen.add(case.id)
            cases.append(case)
    return cases


def load_all_cases(target: str | None = None) -> list[CaseData]:
    return load_cases(target=target)
