"""Gitea 注册页页面对象。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC

from common.browser import drain_alert, visible_texts, wait_for
from common.config import Target


@dataclass
class SignUpResult:
    """注册提交后的可观测结果，供断言使用。"""

    url: str
    title: str
    error_messages: list[str]
    still_on_signup: bool
    logged_in_user: str | None


class GiteaSignUpPage:
    """Gitea 注册页（/user/sign_up）。"""

    PATH = "/user/sign_up"

    USERNAME = (By.ID, "user_name")
    EMAIL = (By.ID, "email")
    PASSWORD = (By.ID, "password")
    REPEAT = (By.ID, "retype")
    # ★ 注意：页面上不止一个 form（Gitea 还有"计时器"表单，其提交按钮排在前面）。
    #   必须把按钮限定在注册表单 form.ui.form 内，否则会点到计时器按钮。
    FORM = (By.CSS_SELECTOR, "form.ui.form")
    SUBMIT = (By.CSS_SELECTOR, "form.ui.form button[type='submit']")
    # 错误提示区域（Gitea 把表单错误渲染在 .ui.negative.message 中）
    ERRORS_CSS = ".ui.negative.message, .ui.error.message, .error-message"

    def __init__(self, driver: WebDriver, target: Target) -> None:
        self.driver = driver
        self.target = target

    # ---- 动作 ----
    def logout(self) -> GiteaSignUpPage:
        """清空会话，确保处于未登录状态。

        ★ 关键：已登录时访问 /user/sign_up 会被 Gitea 重定向到首页，
          导致注册表单永远不出现。凡是要重复注册的场景都必须先退出登录。
        """
        self.driver.delete_all_cookies()
        return self

    def open(self, *, ensure_logged_out: bool = True) -> GiteaSignUpPage:
        if ensure_logged_out:
            self.logout()
        self.driver.get(self.target.url(self.PATH))

        # 若被重定向走（通常是已登录），再清一次会话重试
        if self.PATH not in self.driver.current_url:
            self.logout()
            self.driver.get(self.target.url(self.PATH))

        wait_for(self.driver).until(EC.presence_of_element_located(self.USERNAME))
        return self

    def fill(self, *, username: str, email: str, password: str, repeat: str | None = None) -> None:
        """填写表单。repeat 为空时默认与 password 相同。"""
        self._type(self.USERNAME, username)
        self._type(self.EMAIL, email)
        self._type(self.PASSWORD, password)
        self._type(self.REPEAT, repeat if repeat is not None else password)

    def clear_field(self, locator: tuple[str, str]) -> None:
        element = wait_for(self.driver).until(EC.presence_of_element_located(locator))
        element.clear()

    def submit(self) -> SignUpResult:
        drain_alert(self.driver)  # 提交前清掉可能残留的弹窗
        try:
            wait_for(self.driver).until(EC.element_to_be_clickable(self.SUBMIT)).click()
        except UnexpectedAlertPresentException:
            # 提交瞬间弹出 alert 会中断点击命令，清掉后重试一次
            drain_alert(self.driver)
            wait_for(self.driver).until(EC.element_to_be_clickable(self.SUBMIT)).click()
        drain_alert(self.driver)  # 提交后立即处理弹窗，否则后续命令会被阻塞
        return self.result()

    def submit_empty(self) -> SignUpResult:
        """不填任何内容直接提交，用于验证必填校验。"""
        return self.submit()

    def result(self) -> SignUpResult:
        # 给浏览器一点时间完成跳转或渲染提示
        time.sleep(0.5)
        return SignUpResult(
            url=self.driver.current_url,
            title=self.driver.title,
            error_messages=self.error_texts(),
            still_on_signup=self.PATH in self.driver.current_url,
            logged_in_user=self._detect_logged_in_user(),
        )

    # ---- 查询 ----
    def error_texts(self) -> list[str]:
        """服务端返回的错误提示（过滤隐藏的空容器）。"""
        return visible_texts(self.driver, self.ERRORS_CSS)

    def has_html5_validation_error(self) -> bool:
        """表单是否被浏览器原生校验拦下（如 required 未填）。

        与"服务端校验"区分：原生校验不会产生服务端错误文本，
        但表单会停留在 invalid 状态。
        """
        try:
            return bool(
                self.driver.execute_script(
                    "const f = document.querySelector('form.ui.form');"
                    "return f ? !f.checkValidity() : false;"
                )
            )
        except Exception:  # noqa: BLE001 - 脚本执行失败时视为无校验
            return False

    def has_captcha(self) -> bool:
        """检测页面是否有验证码（有的话 UI 自动化无法完成注册）。"""
        return bool(
            self.driver.find_elements(By.CSS_SELECTOR, "img[src*='captcha'], input[name*='captcha']")
        )

    # ---- 内部 ----
    def _type(self, locator: tuple[str, str], value: str) -> None:
        element = wait_for(self.driver).until(EC.presence_of_element_located(locator))
        element.clear()
        if value:
            element.send_keys(value)

    def _detect_logged_in_user(self) -> str | None:
        """从导航栏推断当前登录用户；未登录返回 None。"""
        links = self.driver.find_elements(By.CSS_SELECTOR, "a[href^='/']")
        for link in links:
            href = link.get_attribute("href") or ""
            text = (link.text or "").strip()
            # Gitea 登录后导航栏会出现指向用户主页的链接
            if "/user/login" in href or "/user/sign_up" in href:
                return None
            if text in ("登录", "注册", "Sign In", "Register"):
                return None
        # 退出链接存在通常意味着已登录
        if self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/user/logout']"):
            return "已登录"
        return None
