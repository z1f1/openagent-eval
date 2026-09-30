"""配置层：接口地址、鉴权、超时、重试、耗时基线。

凭证不落盘：api_key 与 base_url 支持 ${VAR} 占位符，从环境变量注入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from env_config import get as env_get
from env_config import load_env

MODULE_ROOT = Path(__file__).resolve().parent
CONFIG_FILE = MODULE_ROOT / "config.yaml"

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    """配置缺失或占位符无法解析。"""


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    path: str
    api_key: str
    model: str
    timeout: float

    @property
    def url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.path}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    backoff: float = 0.05


@dataclass(frozen=True)
class MockServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass(frozen=True)
class EnvConfig:
    name: str
    target: str
    api: ApiConfig
    # 请求体格式：simple = {"model","prompt"}（自建 Mock）；openai = {"model","messages":[...]}
    payload_style: str = "simple"
    retry: RetryConfig = field(default_factory=RetryConfig)
    latency_baseline: float = 1.0
    mock_server: MockServerConfig = field(default_factory=MockServerConfig)

    @property
    def is_mock(self) -> bool:
        return self.target.startswith("mock")

    @property
    def is_openai_style(self) -> bool:
        return self.payload_style == "openai"


def _resolve_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item) for item in value]
    if not isinstance(value, str):
        return value

    def _sub(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        env_value = env_get(name)
        if env_value:
            return env_value
        if default is not None:
            return default
        raise ConfigError(
            f"环境变量 {name} 未设置，且占位符未提供默认值（配置中的 ${{{name}}}）"
        )

    return _PLACEHOLDER.sub(_sub, value)


def load_config(env: str, config_file: Path | None = None) -> EnvConfig:
    """加载 config.yaml 中的某个环境（mock / real）。"""
    path = config_file or CONFIG_FILE
    if not path.exists():
        raise ConfigError(f"配置文件不存在：{path}")

    # 先加载 .env，让 ${VAR} 占位符能取到本地配置里的值
    load_env()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    environments = raw.get("environments") or {}
    if env not in environments:
        raise ConfigError(f"config.yaml 中没有环境 {env!r}，可用：{sorted(environments)}")

    # 只解析被选中环境的占位符：否则 mock 环境会被 real 环境未设置的变量卡住
    block = _resolve_placeholders(environments[env])

    api_raw = block.get("api") or {}
    api = ApiConfig(
        base_url=str(api_raw.get("base_url", "")).strip(),
        path=str(api_raw.get("path", "/llm/chat")),
        api_key=str(api_raw.get("api_key", "")).strip(),
        model=str(api_raw.get("model", "mock-llm")),
        timeout=float(api_raw.get("timeout", 10.0)),
    )
    if not api.base_url.startswith(("http://", "https://")):
        raise ConfigError(f"环境 {env} 的 api.base_url 非法：{api.base_url!r}")

    retry_raw = block.get("retry") or {}
    mock_raw = block.get("mock_server") or {}
    return EnvConfig(
        name=env,
        target=str(block.get("target", env)),
        api=api,
        payload_style=str(block.get("payload_style", "simple")),
        retry=RetryConfig(
            max_attempts=int(retry_raw.get("max_attempts", 3)),
            backoff=float(retry_raw.get("backoff", 0.05)),
        ),
        latency_baseline=float(block.get("latency_baseline", 1.0)),
        mock_server=MockServerConfig(
            host=str(mock_raw.get("host", "127.0.0.1")),
            port=int(mock_raw.get("port", 8000)),
        ),
    )
