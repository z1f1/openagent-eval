"""登录与会话相关 UI 测试。

覆盖：正常登录、错误密码、不存在用户、未登录访问受保护页面。
设计方法：等价类划分 + 场景法。
"""

from __future__ import annotations

import pytest

from ui.pages.gitea_login import GiteaLoginPage

pytestmark = [pytest.mark.ui]


class TestLoginPositive:
    """正常登录路径。"""

    def test_login_with_valid_credentials(self, driver, target, registered_account) -> None:
        """正确凭据应登录成功并离开登录页。"""
        page = GiteaLoginPage(driver, target)
        result = page.login(
            type("A", (), {  # 简易账号对象
                "username": registered_account["username"],
                "password": registered_account["password"],
            })()
        )

        assert result.success, f"正确凭据登录失败：{result.error_messages}"
        assert page.has_logout(), "登录后导航栏没有退出入口，会话状态可疑"


class TestLoginNegative:
    """登录失败路径。"""

    def test_wrong_password_is_rejected(self, driver, target, registered_account) -> None:
        """错误密码应被拒绝且停留在登录页。"""
        page = GiteaLoginPage(driver, target)
        result = page.login(
            type("A", (), {
                "username": registered_account["username"],
                "password": registered_account["password"] + "-wrong",
            })()
        )

        assert not result.success, "错误密码竟然登录成功"
        assert page.is_login_page, "登录失败后应停留在登录页"
        assert result.error_messages, "登录失败但没有错误提示"

    def test_nonexistent_user_is_rejected(self, driver, target) -> None:
        """不存在的用户应被拒绝（等价类：无效账号）。"""
        page = GiteaLoginPage(driver, target)
        result = page.login(
            type("A", (), {"username": "no_such_user_xyz_999", "password": "Whatever@123"})()
        )

        assert not result.success, "不存在的用户竟然登录成功"
        assert result.error_messages, "登录失败但没有错误提示"

    def test_empty_credentials_are_rejected(self, driver, target) -> None:
        """空凭据提交应被拒绝。"""
        page = GiteaLoginPage(driver, target).open()
        result = page.submit()

        assert not result.success, "空凭据竟然登录成功"
        assert page.is_login_page, "空凭据提交后不应离开登录页"


class TestSessionAndAccessControl:
    """会话与访问控制。"""

    @pytest.mark.parametrize(
        "path",
        ["/user/settings", "/repo/create"],
        ids=["用户设置页", "新建仓库页"],
    )
    def test_protected_page_requires_login(self, driver, target, path: str) -> None:
        """未登录访问受保护页面，应被重定向到登录页。

        这是访问控制的基本要求：不能只靠前端隐藏入口。
        """
        # 先确保是未登录状态
        driver.delete_all_cookies()
        driver.get(target.url(path))

        page = GiteaLoginPage(driver, target)
        assert page.is_login_page, (
            f"未登录访问 {path} 竟然没有跳转登录页，实际地址：{driver.current_url}"
        )
