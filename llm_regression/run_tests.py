"""一键回归入口：把「手动逐个点」变成「一条命令、可重复、可追溯」。

用法
----
    python run_tests.py                          # Mock 环境跑四类场景（默认）
    python run_tests.py --scenario illegal       # 只跑非法/异常场景
    python run_tests.py --keyword TC0            # 按用例编号过滤

    # 真实接口（凭证走环境变量，不落盘）
    $env:LLM_BASE_URL="https://api.deepseek.com"
    $env:LLM_API_KEY="sk-..."
    python run_tests.py --env real --scenario normal

产物
----
    reports/report_<env>_<时间戳>.html   可视化报告（归档）
    reports/latest_<env>.html            最新报告（固定文件名，便于流水线取用）
    reports/junit_<env>.xml              JUnit XML（给 CI 做门禁）
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parent
REPORT_DIR = MODULE_ROOT / "reports"


def build_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 大模型对话接口自动化回归")
    parser.add_argument("--env", default="mock", choices=("mock", "real"), help="运行环境")
    parser.add_argument(
        "--scenario",
        default=None,
        choices=("normal", "empty", "oversized", "illegal"),
        help="只跑某一类场景",
    )
    parser.add_argument("--keyword", default=None, help="按用例编号过滤，等价 -k")
    parser.add_argument(
        "--upload-zentao",
        action="store_true",
        help="跑完后把运行信息（结果明细 + HTML/JUnit 报告附件）上传到禅道；需先设置 ZENTAO_* 环境变量",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = build_args(argv)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    report_html = REPORT_DIR / f"report_{args.env}_{stamp}.html"
    latest_html = REPORT_DIR / f"latest_{args.env}.html"
    junit_xml = REPORT_DIR / f"junit_{args.env}.xml"

    pytest_args = [
        str(MODULE_ROOT / "test_llm_api.py"),
        "-c",
        str(MODULE_ROOT / "pytest.ini"),
        f"--env={args.env}",
        f"--html={report_html}",
        "--self-contained-html",
        f"--junitxml={junit_xml}",
    ]
    if args.scenario:
        pytest_args.append(f"--scenario={args.scenario}")
    if args.keyword:
        pytest_args.extend(["-k", args.keyword])
    if args.upload_zentao:
        pytest_args.append("--upload-zentao")

    print(f"[回归启动] env={args.env} scenario={args.scenario or 'all'}")
    started = dt.datetime.now()
    exit_code = pytest.main(pytest_args)
    duration = (dt.datetime.now() - started).total_seconds()

    # 退出码 4 = 命令行/配置错误（例如 real 环境缺凭证）：
    # 此时没有真实用例结果，必须清掉空报告，否则会被误当成「真实接口回归已执行」
    if int(exit_code) == 4:
        for artifact in (report_html, latest_html, junit_xml):
            artifact.unlink(missing_ok=True)
        print(f"[回归中止] 配置或参数错误（退出码 4），未产生有效报告；耗时 {duration:.2f}s")
        return int(exit_code)

    if report_html.exists():
        latest_html.write_bytes(report_html.read_bytes())

    print(f"[回归结束] 退出码={int(exit_code)} 耗时={duration:.2f}s")
    print(f"[报告] {report_html}")
    print(f"[最新] {latest_html}")
    print(f"[JUnit] {junit_xml}")
    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())
