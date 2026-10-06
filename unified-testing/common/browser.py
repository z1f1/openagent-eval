"""浏览器层：Selenium WebDriver 的统一封装。

职责：
- 按配置创建浏览器实例（默认复用本机 Edge，免下载 Chromium）
- 统一处理代理（★ 关键：本地靶子必须绕过代理，否则驱动下载/请求会 502）
- 统一超时与显式等待
- 失败自动截图留痕

设计说明见项目 README「为什么要有这一层」。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from common.config import PROJECT_ROOT, Target, load_settings

LOGGER = logging.getLogger("unified-testing.browser")

# 代理环境变量：跑 UI 测试前必须清掉，否则 Selenium Manager 下载驱动会拿到 Bad Gateway
PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


def clear_proxy_env() -> list[str]:
    """清除当前进程的代理环境变量，返回被清除的键名。

    背景：本机有一个本地代理（127.0.0.1:7897），一旦设置，
    Selenium 下载 Edge 驱动会得到 502 Bad Gateway。
    本地靶子（127.0.0.1）本身也不该走代理。
    """
    cleared = []
    for key in PROXY_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared.append(key)
    if cleared:
        LOGGER.info("已清除代理环境变量：%s", ", ".join(cleared))
    return cleared


@dataclass
class BrowserSettings:
    name: str = "edge"
    headless: bool = True
    window_size: str = "1440,900"
    explicit_wait: int = 10
    page_load_timeout: int = 30
    screenshot_on_failure: bool = True
    # 浏览器启动失败时的额外重试次数（Edge 驱动偶发原生崩溃）
    max_retries: int = 2

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None = None) -> BrowserSettings:
        raw = (settings or load_settings()).get("browser") or {}
        size = raw.get("window_size", "1440,900")
        if isinstance(size, (list, tuple)):
            size = ",".join(str(x) for x in size)
        return cls(
            name=str(raw.get("name", "edge")).lower(),
            headless=bool(raw.get("headless", True)),
            window_size=str(size),
            explicit_wait=int(raw.get("explicit_wait", 10)),
            page_load_timeout=int(raw.get("page_load_timeout", 30)),
            screenshot_on_failure=bool(raw.get("screenshot_on_failure", True)),
            max_retries=int(raw.get("max_retries", 2)),
        )


def _build_options(name: str, settings: BrowserSettings):
    """按浏览器类型构造 Options，并统一关闭代理。"""
    if name == "edge":
        from selenium.webdriver.edge.options import Options

        options = Options()
        if settings.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--window-size={settings.window_size}")
        # 禁止走系统代理：本地靶子必须直连
        options.add_argument("--no-proxy-server")
        return options

    if name in ("chrome", "chromium"):
        from selenium.webdriver.chrome.options import Options

        options = Options()
        if settings.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(f"--window-size={settings.window_size}")
        options.add_argument("--no-proxy-server")
        return options

    raise ValueError(f"暂不支持的浏览器：{name!r}（目前支持 edge / chrome）")


def create_driver(
    settings: BrowserSettings | None = None,
    *,
    target: Target | None = None,
    retries: int = 2,
) -> WebDriver:
    """创建浏览器实例，失败自动重试。

    target 传本地靶子时会强制清代理。
    重试的必要性：Edge 驱动偶发启动失败（原生崩溃），重试可显著提升稳定性。
    """
    settings = settings or BrowserSettings.from_settings()
    if target is None or target.no_proxy:
        clear_proxy_env()

    options = _build_options(settings.name, settings)
    last_error: Exception | None = None

    for attempt in range(1, retries + 2):
        started = time.time()
        try:
            driver = webdriver.Edge(options=options) if settings.name == "edge" else webdriver.Chrome(options=options)
            driver.set_page_load_timeout(settings.page_load_timeout)
            LOGGER.info(
                "浏览器已启动：%s %s（%.1fs）%s",
                settings.name,
                driver.capabilities.get("browserVersion", "?"),
                time.time() - started,
                f"第 {attempt} 次尝试" if attempt > 1 else "",
            )
            return driver
        except Exception as exc:  # noqa: BLE001 - 需要重试任何启动失败
            last_error = exc
            LOGGER.warning("浏览器启动失败（第 %d 次）：%s", attempt, str(exc)[:200])
            time.sleep(1.5 * attempt)

    raise RuntimeError(f"浏览器启动失败，已重试 {retries + 1} 次：{last_error}")


def is_session_alive(driver: WebDriver) -> bool:
    """探测 WebDriver 会话是否仍然可用。

    用途：Edge 驱动偶发原生崩溃后，所有后续命令都会超时；
    提前探测可让 fixture 及时重建会话，避免整个测试文件级联失败。
    """
    try:
        _ = driver.current_url
        return True
    except Exception:  # noqa: BLE001 - 任何异常都视为会话已死
        return False


def quit_quietly(driver: WebDriver | None) -> None:
    """关闭浏览器，忽略已崩溃导致的异常。"""
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:  # noqa: BLE001 - 崩溃的会话无法正常关闭
        LOGGER.debug("浏览器关闭时出错（通常因会话已崩溃）", exc_info=True)


def wait_for(driver: WebDriver, timeout: int | None = None) -> WebDriverWait:
    """统一的显式等待对象。"""
    settings = BrowserSettings.from_settings()
    return WebDriverWait(driver, timeout or settings.explicit_wait)


def save_screenshot(driver: WebDriver, name: str, directory: Path | None = None) -> Path | None:
    """失败留痕：保存截图，返回文件路径。"""
    try:
        out_dir = directory or (PROJECT_ROOT / "reports" / "screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        path = out_dir / f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if driver.save_screenshot(str(path)):
            LOGGER.info("已保存失败截图：%s", path)
            return path
    except Exception:  # noqa: BLE001 - 截图失败不应掩盖原始错误
        LOGGER.warning("截图失败", exc_info=True)
    return None


def drain_alert(driver: WebDriver) -> str | None:
    """若存在 JS 弹窗，读取内容并关闭，返回弹窗文本。

    必要性：JS alert 会阻塞 WebDriver 的后续命令，
    导致整个浏览器会话超时甚至驱动崩溃。提交表单前后都应调用。
    """
    try:
        alert = driver.switch_to.alert
        text = alert.text
        alert.accept()
        LOGGER.info("已关闭 JS 弹窗：%r", text)
        return text
    except Exception:  # noqa: BLE001 - 没有弹窗是正常情况
        return None


def is_displayed(element) -> bool:
    """元素是否存在且可见（用于过滤隐藏的提示容器）。"""
    try:
        return bool(element.is_displayed())
    except Exception:  # noqa: BLE001 - 元素失效时视为不可见
        return False


def visible_texts(driver: WebDriver, css: str) -> list[str]:
    """取某类元素的"可见且有文本"的内容，过滤掉占位的空容器。

    Gitea 会在页面上渲染空且隐藏的 .ui.negative.message 容器，
    不过滤会导致"永远存在错误提示"的误判。
    """
    texts = []
    for element in driver.find_elements("css selector", css):
        if not is_displayed(element):
            continue
        text = (element.text or "").strip()
        if text:
            texts.append(text)
    return texts
