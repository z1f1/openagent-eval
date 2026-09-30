"""用环境变量里的账号密码换取禅道 token（不再硬编码凭证）。

用法
----
在仓库根目录创建 .env（从 .env.example 复制）并填写：
    ZENTAO_BASE_URL=http://127.0.0.1:81/zentao
    ZENTAO_ACCOUNT=admin
    ZENTAO_PASSWORD=你的密码

然后执行：
    .venv\\Scripts\\python.exe get_zentao_token.py

拿到 token 后可写回 .env 的 ZENTAO_TOKEN=（也会被 .gitignore 忽略）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "llm_regression"))

from env_config import ENV_FILE, get  # noqa: E402


def main() -> int:
    base_url = get("ZENTAO_BASE_URL").rstrip("/")
    account = get("ZENTAO_ACCOUNT")
    password = get("ZENTAO_PASSWORD")

    missing = [
        name
        for name, value in (
            ("ZENTAO_BASE_URL", base_url),
            ("ZENTAO_ACCOUNT", account),
            ("ZENTAO_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        print(f"缺少配置：{', '.join(missing)}")
        print(f"请复制 .env.example 为 {ENV_FILE.name} 并填写，或直接导出环境变量。")
        return 2

    try:
        resp = requests.post(
            f"{base_url}/api.php/v2/users/login",
            json={"account": account, "password": password},
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        print(f"请求异常：{exc}")
        return 1

    print("状态码：", resp.status_code)
    print("响应文本：", resp.text[:400])

    try:
        token = resp.json().get("token")
    except ValueError:
        token = None
    if token:
        print(f"\n取得 token（{len(token)} 字符）：{token}")
        print(f"可写入 {ENV_FILE.name} 的 ZENTAO_TOKEN= 一行后复用。")
    return 0 if token else 1


if __name__ == "__main__":
    sys.exit(main())
