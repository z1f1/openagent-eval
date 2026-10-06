"""Gitea 登录页页面对象。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from selenium.common.exceptions import UnexpectedAlertPresentException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC

from common.browser import drain_alert, visible_texts, wait_for
from common.config import Account, Target


@dataclass
class LoginResult:
    """登录提交后的可观测结果。"""

    url: str
    success: bool
    error_messages: list[str]


class GiteaLoginPage:
    """Gitea 登录页（/user/login）。"""

    PATH = "/user/login"

    USERNAME = (By.ID, "user_name")
    PASSWORD = (By.ID, "password")
    # ★ 同注册页：页面有多个 form，必须把提交按钮限定在登录表单内
    FORM = (By.CSS_SELECTOR, "form.ui.form")
    SUBMIT = (By.CSS_SELECTOR, "form.ui.form button[type='submit']")
    ERRORS_CSS = ".ui.negative.message, .ui.error.message, .error-message"
    LOGOUT = (By.CSS_SELECTOR, "a[href*='/user/logout']")

    def __init__(self, driver: WebDriver, target: Target) -> None:
        self.driver = driver
        self.target = target

    # ---- 动作 ----
    def open(self) -> GiteaLoginPage:
        self.driver.get(self.target.url(self.PATH))
        wait_for(self.driver).until(EC.presence_of_element_located(self.USERNAME))
        return self

    def fill(self, username: str, password: str) -> GiteaLoginPage:
        self._type(self.USERNAME, username)
        self._type(self.PASSWORD, password)
        return self

    def submit(self) -> LoginResult:
        drain_alert(self.driver)
        try:
            wait_for(self.driver).until(EC.element_to_be_clickable(self.SUBMIT)).click()
        except UnexpectedAlertPresentException:
            # 提交瞬间弹出 alert 会中断点击命令，清掉后重试一次
            drain_alert(self.driver)
            wait_for(self.driver).until(EC.element_to_be_clickable(self.SUBMIT)).click()
        drain_alert(self.driver)

        time.sleep(0.5)
        return LoginResult(
            url=self.driver.current_url,
            success=self.PATH not in self.driver.current_url,
            error_messages=self.error_texts(),
        )

    def login(self, account: Account) -> LoginResult:
        return self.open().fill(account.username, account.password).submit()

    # ---- 查询 ----
    def error_texts(self) -> list[str]:
        """服务端返回的错误提示（过滤隐藏的占位容器）。"""
        return visible_texts(self.driver, self.ERRORS_CSS)

    @property
    def is_login_page(self) -> bool:
        """当前是否停留在登录页（未登录成功的特征）。"""
        return self.PATH in self.driver.current_url

    def has_logout(self) -> bool:
        """导航栏是否有退出入口 —— 有则表示已登录。"""
        return bool(self.driver.find_elements(*self.LOGOUT))

    def _type(self, locator: tuple[str, str], value: str) -> None:
        element = wait_for(self.driver).until(EC.presence_of_element_located(locator))
        element.clear()
        element.send_keys(value)
