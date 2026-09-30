@echo off
REM ============================================================
REM  llm_regression 一键回归（Windows）
REM  双击即可运行；也可在终端执行：run.bat --scenario illegal
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [错误] 找不到虚拟环境：%PY%
    echo 请先在仓库根目录执行：python -m venv .venv
    echo 然后执行：.venv\Scripts\python.exe -m pip install -r llm_regression\requirements.txt
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
"%PY%" "%ROOT%llm_regression\run_tests.py" %*

echo.
echo 报告：%ROOT%llm_regression\reports\latest_mock.html
pause
