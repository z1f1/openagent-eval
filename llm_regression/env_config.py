"""统一从环境变量读取配置，并支持本地 .env 文件（已忽略，不进版本库）。

优先级：真实环境变量 > .env 文件。

设计意图
--------
凭证绝不写进代码。需要使用时：
1. 复制 `.env.example` 为 `.env` 并填值（本地开发，最省事），或
2. 在 shell / CI 里导出环境变量（CI 必须走这条）。

python-dotenv 没安装时自动降级为「只读真实环境变量」，不会报错中断回归。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
_loaded = False


def load_env(env_file: Path | None = None) -> bool:
    """加载 .env（若存在且未加载过）。返回是否成功加载了文件。"""
    global _loaded
    if _loaded:
        return ENV_FILE.exists()

    path = env_file or ENV_FILE
    _loaded = True
    if not path.exists():
        return False

    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return True
    except ImportError:
        # 降级实现：按 KEY=VALUE 逐行解析，忽略注释与空行
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
        return True


def get(name: str, default: str = "") -> str:
    """读取环境变量（会先确保 .env 已加载）。"""
    load_env()
    return (os.environ.get(name) or default).strip()


__all__ = ["ENV_FILE", "REPO_ROOT", "get", "load_env"]
