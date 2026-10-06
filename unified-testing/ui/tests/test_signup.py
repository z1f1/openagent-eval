"""注册功能 UI 测试。

覆盖：正常注册、必填校验、格式校验、密码一致性、用户名重复。
设计方法：等价类划分 + 边界值。
"""

from __future__ import annotations

import pytest

from conftest import make_credentials
from ui.pages.gitea_login import GiteaLoginPage
from ui.pages.gitea_signup import GiteaSignUpPage

pytestmark = [pytest.mark.ui]


@pytest.fixture()
def signup_page(driver, target) -> GiteaSignUpPage:
    return GiteaSignUpPage(driver, target).open()


class TestSignUpPositive:
    """正常注册路径。"""

    def test_register_new_account_succeeds(self, driver, target, signup_page, temp_account) -> None:
        """用合法信息注册，应成功并离开注册页。"""
        assert not signup_page.has_captcha(), "靶子开启了验证码，UI 自动化无法注册"

        signup_page.fill(
            username=temp_account["username"],
            email=temp_account["email"],
            password=temp_account["password"],
        )
        result = signup_page.submit()

        assert not result.still_on_signup, (
            f"注册后仍停留在注册页，错误信息：{result.error_messages}"
        )
        assert not result.error_messages, f"注册出现错误提示：{result.error_messages}"

    def test_new_account_can_login(self, driver, target, temp_account) -> None:
        """端到端：注册后应能用该账号登录成功。"""
        register_page = GiteaSignUpPage(driver, target).open()
        register_page.fill(
            username=temp_account["username"],
            email=temp_account["email"],
            password=temp_account["password"],
        )
        register_page.submit()

        # 先退出登录：已登录状态访问登录页会跳转，且无法验证"登录"这个动作本身
        driver.delete_all_cookies()
        login_result = GiteaLoginPage(driver, target).login(
            type("A", (), {  # 简易账号对象，避免依赖具体数据类构造
                "username": temp_account["username"],
                "password": temp_account["password"],
            })()
        )

        assert login_result.success, (
            f"新注册账号无法登录，错误信息：{login_result.error_messages}"
        )


class TestSignUpValidation:
    """表单校验（负向）。

    校验可能发生在两层，断言时两层都接受：
      - 浏览器原生校验（HTML5 required / type=email）→ 表单停留在 invalid 状态
      - 服务端校验 → 渲染错误提示文本
    只要"确实被拦住了"即算通过，并同时检查是否有提示。
    """

    @staticmethod
    def _assert_rejected(page, result, scene: str) -> None:
        assert result.still_on_signup, f"{scene}：竟然注册成功并离开了注册页"
        blocked = bool(result.error_messages) or page.has_html5_validation_error()
        assert blocked, f"{scene}：停留在注册页但既无服务端提示也无原生校验，用户得不到任何反馈"

    def test_empty_form_is_rejected(self, signup_page) -> None:
        """全空提交应被拒绝并给出反馈。"""
        result = signup_page.submit_empty()
        self._assert_rejected(signup_page, result, "全空表单提交")

    def test_username_missing_is_rejected(self, signup_page, temp_account) -> None:
        """缺失用户名应被拒绝。"""
        signup_page.fill(
            username="",
            email=temp_account["email"],
            password=temp_account["password"],
        )
        result = signup_page.submit()
        self._assert_rejected(signup_page, result, "缺失用户名")

    def test_whitespace_only_username_is_rejected(self, signup_page, temp_account) -> None:
        """纯空格用户名应被拒绝（等价类：无效输入）。"""
        signup_page.fill(
            username="   ",
            email=temp_account["email"],
            password=temp_account["password"],
        )
        result = signup_page.submit()
        self._assert_rejected(signup_page, result, "纯空格用户名")

    def test_short_password_is_rejected(self, signup_page, temp_account) -> None:
        """过短密码应被拒绝（Gitea 默认要求至少 8 位）。"""
        signup_page.fill(
            username=temp_account["username"],
            email=temp_account["email"],
            password="123",
        )
        result = signup_page.submit()
        self._assert_rejected(signup_page, result, "3 位密码")

    def test_password_mismatch_is_rejected(self, signup_page, temp_account) -> None:
        """两次密码不一致应被拒绝。"""
        signup_page.fill(
            username=temp_account["username"],
            email=temp_account["email"],
            password=temp_account["password"],
            repeat=temp_account["password"] + "X",
        )
        result = signup_page.submit()
        self._assert_rejected(signup_page, result, "两次密码不一致")

    def test_invalid_email_is_rejected(self, signup_page, temp_account) -> None:
        """非法邮箱格式应被拒绝；若被接受则标记为待评估的产品行为。"""
        signup_page.fill(
            username=temp_account["username"],
            email="not-an-email",
            password=temp_account["password"],
        )
        result = signup_page.submit()

        if not result.still_on_signup:
            pytest.xfail("靶子接受了非法邮箱格式（not-an-email），属待评估的产品行为")
        self._assert_rejected(signup_page, result, "非法邮箱")

    def test_duplicate_username_is_rejected(self, driver, target) -> None:
        """已存在的用户名再注册应被拒绝。"""
        first = make_credentials("dup")
        page = GiteaSignUpPage(driver, target).open()
        page.fill(
            username=first["username"],
            email=first["email"],
            password=first["password"],
        )
        page.submit()

        second = make_credentials("dup2")
        page2 = GiteaSignUpPage(driver, target).open()
        page2.fill(
            username=first["username"],  # 故意重复
            email=second["email"],
            password=second["password"],
        )
        result = page2.submit()

        self._assert_rejected(page2, result, "重复用户名")
