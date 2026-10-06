"""pytest 公共 fixture：所有测试类型共用。"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import pytest

# 把项目根目录加入 sys.path，使 `from common.xxx import ...` 可用
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.browser import BrowserSettings, clear_proxy_env, create_driver, save_screenshot
from common.config import Target, list_targets, load_settings, load_target

LOGGER = logging.getLogger("unified-testing")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("unified-testing")
    group.addoption(
        "--target",
        action="store",
        default=None,
        help=f"被测靶子名称，可选：{', '.join(list_targets())}（默认取 settings.yaml 的 default_target）",
    )


@pytest.fixture(scope="session")
def settings() -> dict:
    return load_settings()


@pytest.fixture(scope="session")
def target(request: pytest.FixtureRequest, settings: dict) -> Target:
    """被测靶子配置。"""
    name = request.config.getoption("--target") or settings.get("default_target") or "gitea"
    tgt = load_target(name)
    LOGGER.info("被测靶子：%s（%s）", tgt.name, tgt.base_url)
    return tgt


@pytest.fixture(scope="session")
def browser_settings(settings: dict) -> BrowserSettings:
    return BrowserSettings.from_settings(settings)


@pytest.fixture()
def driver(browser_settings: BrowserSettings, target: Target):
    """每个用例独立的浏览器实例 + 自动状态隔离。

    为什么要每用例独立浏览器：
      共享会让用例互相污染 —— 例如"注册后处于登录态"，
      导致后续"未登录应跳转登录页"的用例失败。这类耦合故障极难排查。
      实测：共享浏览器时 8 failed / 7 passed，且一条失败引发级联超时。

    启动失败或会话崩溃时自动重建（Edge 驱动偶发原生崩溃）。
    """
    from common.browser import is_session_alive, quit_quietly

    clear_proxy_env()
    attempts = browser_settings.max_retries + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        drv = None
        try:
            drv = create_driver(browser_settings, target=target)
            # 每个用例从干净会话开始：清掉上一个用例残留的 cookie
            drv.delete_all_cookies()
            if not is_session_alive(drv):
                raise RuntimeError("新会话立即不可用")
        except Exception as exc:  # noqa: BLE001 - 启动失败则重建
            last_error = exc
            LOGGER.warning("浏览器启动异常（第 %d 次）：%s", attempt, type(exc).__name__)
            quit_quietly(drv)
            if attempt >= attempts:
                raise
            continue

        try:
            yield drv
        finally:
            quit_quietly(drv)
        return

    raise RuntimeError(f"浏览器会话始终不可用：{last_error}")


@pytest.fixture(scope="session")
def session_driver(browser_settings: BrowserSettings, target: Target):
    """会话级浏览器，仅供"注册测试账号"这类一次性前置使用。

    注意：不要把它用于普通用例，否则会造成用例间状态污染。
    """
    clear_proxy_env()
    drv = create_driver(browser_settings, target=target)
    yield drv
    drv.quit()
    LOGGER.info("会话级浏览器已关闭")


# ------------------------------------------------------------------ 账号相关
def make_credentials(prefix: str = "qa") -> dict[str, str]:
    """生成唯一的测试账号凭据。

    用时间戳保证唯一，避免重复注册失败，也让每次运行的数据可区分。
    """
    stamp = f"{int(time.time())}"
    username = f"{prefix}{stamp}"
    return {
        "username": username,
        "password": f"{prefix.capitalize()}@{stamp}",
        "email": f"{username}@example.com",
    }


def register_account(driver, target: Target, creds: dict[str, str]) -> None:
    """通过 UI 注册账号（注册本身就是被测功能）。"""
    from ui.pages.gitea_signup import GiteaSignUpPage

    page = GiteaSignUpPage(driver, target).open()
    if page.has_captcha():
        raise RuntimeError(
            "★ Gitea 开启了验证码，UI 自动化无法完成注册。"
            "请在 _sut/custom/conf/app.ini 的 [service] 段设置 ENABLE_CAPTCHA = false"
        )
    page.fill(username=creds["username"], email=creds["email"], password=creds["password"])
    result = page.submit()
    if result.still_on_signup:
        raise RuntimeError(f"注册失败，错误信息：{result.error_messages}")


@pytest.fixture(scope="session")
def registered_account(session_driver, target: Target) -> dict[str, str]:
    """会话级：注册一个测试账号并返回凭据。

    注册走 UI（同时验证了注册功能），每个会话只做一次，
    后续用例直接用它登录，避免重复注册拖慢测试。
    使用独立的会话级浏览器，避免把登录态污染给普通用例。
    """
    creds = make_credentials("qa")
    register_account(session_driver, target, creds)
    LOGGER.info("测试账号已就绪：%s", creds["username"])
    return creds


@pytest.fixture()
def temp_account() -> dict[str, str]:
    """函数级：生成一组随机凭据（用于注册相关的正/负向用例）。"""
    return make_credentials("tmp")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """失败留痕：用例失败时保存截图与地址，写入测试报告。

    这是"可追溯"的基础 —— 提缺陷单时可直接附上证据。
    """
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or not report.failed:
        return

    driver = item.funcargs.get("driver")
    if driver is None:
        return

    try:
        shot = save_screenshot(driver, item.nodeid)
        extras = getattr(report, "extras", [])
        if shot is not None:
            from pytest_html import extras as html_extras

            extras.append(html_extras.image(str(shot)))
            extras.append(html_extras.url("失败页面", driver.current_url))
        extras.append(html_extras.text(f"失败地址：{driver.current_url}", name="上下文"))
        report.extras = extras
    except Exception:  # noqa: BLE001 - 留痕失败不应影响测试结论
        LOGGER.warning("失败留痕写入时出错", exc_info=True)
