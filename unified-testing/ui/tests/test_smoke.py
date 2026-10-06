"""UI 冒烟测试：验证环境、靶子可达性与首页关键元素。

这一组用例不修改靶子数据，只做只读检查 —— 可以放心反复运行。
价值：任何一次框架或环境变更后，先跑它确认链路没断。
"""

from __future__ import annotations

import pytest

from ui.pages.gitea_home import GiteaHomePage

pytestmark = [pytest.mark.ui, pytest.mark.smoke]


class TestGiteaHomeSmoke:
    """Gitea 首页冒烟。"""

    @pytest.fixture(autouse=True)
    def _page(self, driver, target) -> GiteaHomePage:
        self.page = GiteaHomePage(driver, target).open().wait_loaded()
        return self.page

    def test_title_contains_site_name(self, target) -> None:
        """页面标题应包含站点名，证明页面确实加载成功。"""
        assert self.page.title, "页面标题为空，可能未加载成功"
        assert "gitea" in self.page.title.lower() or target.name.split()[0].lower() in self.page.title.lower(), (
            f"页面标题异常：{self.page.title!r}"
        )

    def test_landing_url_is_target_root(self, driver, target) -> None:
        """当前地址应指向靶子根路径，证明访问的是正确系统。"""
        assert driver.current_url.rstrip("/") == target.base_url.rstrip("/"), (
            f"期望停留在 {target.base_url}，实际 {driver.current_url}"
        )

    def test_login_entry_visible(self) -> None:
        """首页必须提供登录入口（未登录状态）。"""
        assert self.page.has_sign_in(), "未找到登录链接"

    def test_signup_entry_visible(self) -> None:
        """首页必须提供注册入口（本实例允许自注册，UI 测试依赖它）。"""
        assert self.page.has_sign_up(), "未找到注册链接"

    def test_main_layout_present(self) -> None:
        """主内容区、导航栏、页脚应存在，证明页面结构完整。"""
        assert self.page.find(GiteaHomePage.MAIN_CONTAINER), "未找到主内容容器"
        assert self.page.find(GiteaHomePage.NAVBAR), "未找到导航栏"
        assert self.page.find(GiteaHomePage.FOOTER), "未找到页脚"

    def test_page_has_meaningful_content(self, driver) -> None:
        """页面源码不应为空 —— 防止"状态码 200 但内容空白"的假成功。"""
        source = driver.page_source
        assert len(source) > 1000, f"页面内容过少（{len(source)} 字符），可能加载异常"
