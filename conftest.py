"""仓库根 conftest：失败自动提禅道 Bug（凭证全部走环境变量 / .env，不再明文）。

需要的环境变量（可写入仓库根目录的 .env，已加入 .gitignore）：
    ZENTAO_ENABLED=1
    ZENTAO_BASE_URL=http://127.0.0.1:81/zentao
    ZENTAO_TOKEN=...            或 ZENTAO_ACCOUNT + ZENTAO_PASSWORD
    ZENTAO_PRODUCT_ID=1
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import requests

# 复用 llm_regression 里的 .env 加载器，避免两套配置逻辑
sys.path.insert(0, str(Path(__file__).resolve().parent / "llm_regression"))
from env_config import get as env_get  # noqa: E402


def _credentials() -> tuple[str, dict[str, str]] | None:
    """读取禅道配置；未启用或配置不全时返回 None（静默跳过，不影响用例判定）。"""
    if env_get("ZENTAO_ENABLED").lower() not in ("1", "true", "yes"):
        return None

    base_url = env_get("ZENTAO_BASE_URL").rstrip("/")
    product_id = env_get("ZENTAO_PRODUCT_ID")
    token = env_get("ZENTAO_TOKEN")
    if not base_url or not product_id or not token:
        return None
    return token, {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Token": token,
    }


PRODUCT_ID = int(env_get("ZENTAO_PRODUCT_ID", "1") or "1")

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """pytest全局钩子：捕获用例执行结果，失败自动提交禅道Bug"""
    outcome = yield
    rep = outcome.get_result()

    # 把各阶段执行结果挂载到item，便于fixture读取
    if rep.when == "call":
        item.rep_call = rep

    # 仅在【用例执行阶段】且【失败】时触发
    if rep.when == "call" and rep.failed:
        creds = _credentials()
        if creds is None:
            print("\n[禅道] 未启用或配置不全，跳过自动提单（设置 ZENTAO_ENABLED=1 与环境变量）")
            return

        _token, headers = creds
        print("\n[禅道] 检测到用例失败，尝试自动提交 Bug")

        # 读取测试节点的自定义标记信息
        feature_mark = item.get_closest_marker("feature")
        feature = feature_mark.args[0] if feature_mark else "未指定模块"

        prio_mark = item.get_closest_marker("priority")
        priority = prio_mark.args[0] if prio_mark else "P3"

        desc_mark = item.get_closest_marker("desc")
        case_desc = desc_mark.args[0] if desc_mark else "无用例描述"

        # 组装Bug内容
        bug_title = f"【{feature}】[{priority}]自动化失败：{item.nodeid}"
        bug_steps = f"""
测试节点nodeid：{item.nodeid}
功能模块：{feature}
用例优先级：{priority}
用例描述：{case_desc}

原始报错堆栈：
{rep.longreprtext}
"""
        bug_data = {
            "productID": PRODUCT_ID,
            "title": bug_title,
            "severity": 3,
            "pri": 3,
            "type": "codeerror",
            # 影响版本是必填项；产品无版本数据时用 trunk
            "openedBuild": [env_get("ZENTAO_BUILD", "trunk") or "trunk"],
            "steps": bug_steps,
        }

        try:
            res = requests.post(
                url=f"{env_get('ZENTAO_BASE_URL').rstrip('/')}/api.php/v2/bugs",
                json=bug_data,
                headers=headers,
                timeout=10,
            )
            print(f"[禅道] 提交结果：{res.text[:200]}")
        except Exception as exc:  # noqa: BLE001 - 提单失败不影响用例判定
            print(f"[禅道] 提交 Bug 失败：{exc}")
