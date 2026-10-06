"""统一测试入口：一条命令运行指定类型或全部测试。

用法：
    python run.py ui            # 只跑 UI 测试
    python run.py ui --target gitea
    python run.py all           # 跑全部已实现的测试类型

设计说明：
    三类测试（UI / 接口 / 性能 / 安全）共用同一个运行器，
    这是"统一测试框架"的体现 —— 不需要记多套命令。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPORT_DIR = PROJECT_ROOT / "reports"

# 测试类型 → 用例目录
SUITES: dict[str, str] = {
    "ui": "ui/tests",
    "api": "api/tests",
    "perf": "perf",
    "security": "security",
}

BROWSER_SUITES = {"ui"}


def clear_proxy_env() -> None:
    """跑浏览器测试前必须清代理，否则 Selenium 下载驱动会 502。

    背景见 README「已知环境坑」。
    """
    import os

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        if os.environ.pop(key, None):
            print(f"  [环境] 已清除代理变量 {key}")


def run_suite(name: str, extra: list[str]) -> int:
    directory = PROJECT_ROOT / SUITES[name]
    if not directory.exists():
        print(f"[跳过] {name}：目录尚不存在（{SUITES[name]}）")
        return 0

    if name in BROWSER_SUITES:
        clear_proxy_env()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / f"report_{name}.html"
    junit = REPORT_DIR / f"junit_{name}.xml"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        SUITES[name],
        "-c",
        str(PROJECT_ROOT / "pytest.ini"),
        f"--html={report}",
        "--self-contained-html",
        f"--junitxml={junit}",
        *extra,
    ]

    print(f"\n{'=' * 64}")
    print(f"  运行 {name} 测试")
    print(f"  命令：{' '.join(cmd)}")
    print(f"{'=' * 64}\n")

    started = time.time()
    code = subprocess.call(cmd, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - started

    print(f"\n{'=' * 64}")
    print(f"  {name} 结束：退出码 {code}，耗时 {elapsed:.1f}s")
    if report.exists():
        print(f"  报告：{report}")
    print(f"{'=' * 64}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="统一测试运行器")
    parser.add_argument(
        "suite",
        choices=[*SUITES, "all"],
        help="要运行的测试类型（all = 全部已实现）",
    )
    parser.add_argument("--target", help="被测靶子名称（如 gitea / zentao）")
    args, extra = parser.parse_known_args()

    if args.target:
        extra = [*extra, "--target", args.target]

    names = list(SUITES) if args.suite == "all" else [args.suite]

    codes = {}
    for name in names:
        codes[name] = run_suite(name, extra)

    print(f"\n{'=' * 64}")
    print("  汇总")
    print(f"{'=' * 64}")
    for name, code in codes.items():
        status = "通过" if code == 0 else f"失败（退出码 {code}）"
        print(f"  {name:<10} {status}")

    return 0 if all(code == 0 for code in codes.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
