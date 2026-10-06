"""统一配置加载层。

三类测试（UI / 接口 / 性能 / 安全）共用这一份加载逻辑，
这是"统一测试框架"的基础。

用法：
    from common.config import load_target, load_settings
    gitea = load_target("gitea")
    print(gitea.base_url)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGETS_FILE = PROJECT_ROOT / "targets" / "targets.yaml"
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.yaml"
ENV_FILE = PROJECT_ROOT / ".env"

# 形如 ${VAR} 或 ${VAR:-默认值}
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


# ------------------------------------------------------------------ .env 加载
def load_env(path: Path = ENV_FILE) -> None:
    """把 .env 里的键值对注入环境变量（已存在的变量不覆盖）。

    不依赖 python-dotenv，避免多一个安装项；格式支持 KEY=VALUE 与 # 注释。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve(value: Any) -> Any:
    """递归替换 ${VAR} / ${VAR:-default}（只在目标环境内解析）。"""
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            found = os.environ.get(name)
            if found is None:
                if default is None:
                    raise KeyError(
                        f"环境变量 {name!r} 未设置，且未提供默认值。"
                        f"请在 .env 中配置。"
                    )
                return default
            return found

        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的明确提示
        raise ImportError(
            "需要 PyYAML：请执行\n"
            r"  C:\Users\LX\miniconda3\python.exe -m pip install pyyaml"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"配置文件格式错误（顶层应为映射）：{path}")
    return data


# ------------------------------------------------------------------ 数据结构
@dataclass(frozen=True)
class Account:
    """一组测试账号。"""

    username: str
    password: str
    email: str = ""


@dataclass(frozen=True)
class Target:
    """一个被测系统。"""

    key: str
    name: str
    type: str
    base_url: str
    capabilities: tuple[str, ...] = ()
    accounts: dict[str, Account] = field(default_factory=dict)
    health_check: dict[str, Any] = field(default_factory=dict)
    no_proxy: bool = True

    def account(self, role: str = "admin") -> Account:
        if role not in self.accounts:
            raise KeyError(f"靶子 {self.key!r} 没有名为 {role!r} 的账号")
        return self.accounts[role]

    def url(self, path: str = "/") -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


# ------------------------------------------------------------------ 对外接口
def load_target(key: str, *, env_file: Path = ENV_FILE) -> Target:
    """按名称加载一个靶子配置。"""
    load_env(env_file)
    raw = _load_yaml(TARGETS_FILE)
    if key not in raw:
        available = ", ".join(sorted(raw))
        raise KeyError(f"未找到靶子 {key!r}；可用：{available}")
    return _build_target(key, _resolve(raw[key]))


def list_targets(*, env_file: Path = ENV_FILE) -> list[str]:
    load_env(env_file)
    return sorted(_load_yaml(TARGETS_FILE))


def _build_target(key: str, data: dict[str, Any]) -> Target:
    # 账号密码走环境变量。若未配置（.env 缺失），这里会解析失败。
    # 处理策略：降级为"无可用账号"，只让需要账号的用例失败，
    # 而不是让整个靶子无法加载、连冒烟用例都跑不了。
    accounts: dict[str, Account] = {}
    raw_accounts = data.get("accounts") or {}
    try:
        accounts = {
            role: Account(
                username=str(cfg.get("username", "")),
                password=str(cfg.get("password", "")),
                email=str(cfg.get("email", "")),
            )
            for role, cfg in raw_accounts.items()
        }
    except KeyError as exc:
        LOGGER.warning(
            "靶子 %r 的账号密码未配置（%s）；请复制 .env.example 为 .env 并填写。"
            "依赖账号的用例会失败，其余用例不受影响。",
            key,
            exc,
        )
        accounts = {}

    return Target(
        key=key,
        name=str(data.get("name", key)),
        type=str(data.get("type", "web")),
        base_url=str(data["base_url"]),
        capabilities=tuple(data.get("capabilities") or ()),
        accounts=accounts,
        health_check=dict(data.get("health_check") or {}),
        no_proxy=bool(data.get("no_proxy", True)),
    )


def load_settings(*, env_file: Path = ENV_FILE) -> dict[str, Any]:
    """加载全局设置（超时、阈值、报告目录等）。"""
    load_env(env_file)
    if not SETTINGS_FILE.exists():
        return {}
    return _resolve(_load_yaml(SETTINGS_FILE))


if __name__ == "__main__":
    # 自检：直接运行本文件可打印当前配置，方便排查。
    # 注意：只解析"默认靶子"的占位符；其他靶子若缺少环境变量，
    #       仅提示而不中断（否则配了禅道却没测禅道时，跑任何测试都会报错）。
    print(f"项目根目录：{PROJECT_ROOT}")
    names = list_targets()
    print(f"可用靶子：{names}")

    default_name = load_settings().get("default_target") or (names[0] if names else None)
    for name in names:
        marker = "（默认）" if name == default_name else ""
        try:
            target = load_target(name)
        except KeyError as exc:
            print(f"\n[{name}]{marker} ⚠️ 无法解析：{exc}")
            print("   → 若暂不测试该靶子，可忽略；需要时请在 .env 中补上对应变量。")
            continue
        print(f"\n[{name}]{marker} {target.name}")
        print(f"  地址    ：{target.base_url}")
        print(f"  能力    ：{', '.join(target.capabilities)}")
        print(f"  账号    ：{', '.join(target.accounts) or '无'}")
