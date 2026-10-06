"""Gitea 首页页面对象。

页面对象（Page Object）模式：把"页面元素定位"和"测试逻辑"分开。
好处：界面改了只需改这里，用例代码不动。
"""

from __future__ import annotations

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC

from common.browser import wait_for
from common.config import Target


class GiteaHomePage:
    """Gitea 首页。"""

    # ---- 元素定位（集中管理，界面改动只改这里）----
    SIGN_IN_LINK = (By.CSS_SELECTOR, "a[href*='/user/login']")
    SIGN_UP_LINK = (By.CSS_SELECTOR, "a[href*='/user/sign_up']")
    MAIN_CONTAINER = (By.CSS_SELECTOR, ".page-content, .ui.container")
    FOOTER = (By.CSS_SELECTOR, "footer")
    NAVBAR = (By.CSS_SELECTOR, ".navbar, nav")

    def __init__(self, driver: WebDriver, target: Target) -> None:
        self.driver = driver
        self.target = target

    # ---- 动作 ----
    def open(self) -> GiteaHomePage:
        self.driver.get(self.target.url("/"))
        return self

    def go_to_sign_up(self) -> None:
        wait_for(self.driver).until(EC.element_to_be_clickable(self.SIGN_UP_LINK)).click()

    def go_to_sign_in(self) -> None:
        wait_for(self.driver).until(EC.element_to_be_clickable(self.SIGN_IN_LINK)).click()

    # ---- 查询 ----
    @property
    def title(self) -> str:
        return self.driver.title

    def has_sign_in(self) -> bool:
        return bool(self.driver.find_elements(*self.SIGN_IN_LINK))

    def has_sign_up(self) -> bool:
        return bool(self.driver.find_elements(*self.SIGN_UP_LINK))

    def find(self, locator: tuple[str, str]) -> list:
        return self.driver.find_elements(*locator)

    def wait_loaded(self) -> GiteaHomePage:
        wait_for(self.driver).until(EC.presence_of_element_located(self.MAIN_CONTAINER))
        return self
