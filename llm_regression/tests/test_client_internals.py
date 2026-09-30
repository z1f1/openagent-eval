"""框架自身的单元测试：锁住容易回归的关键行为。

这些用例不属于"接口回归"，而是保证框架不出错 —— 尤其是那些
"看起来正常、实际会静默挂掉"的行为。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import requests

from client import build_session, is_local_url
from config import load_config


class TestIsLocalUrl:
    """本地地址识别：决定要不要走代理。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8000/llm/chat",
            "http://localhost:8000/llm/chat",
            "http://127.0.0.1/",
            "http://127.1.2.3:8080/x",
            "http://0.0.0.0:9000/",
            "http://[::1]:8000/",
        ],
    )
    def test_local_urls_detected(self, url: str) -> None:
        assert is_local_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.deepseek.com/v1/chat/completions",
            "https://api.openai.com/v1/chat/completions",
            "http://192.168.1.10:8000/llm/chat",
            "http://example.com/llm/chat",
        ],
    )
    def test_remote_urls_not_local(self, url: str) -> None:
        assert is_local_url(url) is False

    def test_malformed_url_is_not_local(self) -> None:
        # 畸形 URL 不应抛异常，按"非本地"处理即可
        assert is_local_url("not a url") is False


class TestBuildSessionProxyPolicy:
    """代理策略：本地不走代理、远端保持默认。

    回归背景：曾因未做此处理，设了 HTTP(S)_PROXY 后所有 Mock 用例拿到 502 Bad Gateway。
    """

    def test_local_target_disables_proxy(self) -> None:
        config = replace(
            load_config("mock"),
            api=replace(load_config("mock").api, base_url="http://127.0.0.1:8000"),
        )
        session = build_session(config)
        try:
            assert session.trust_env is False, "本地地址必须关闭 trust_env，否则会读代理环境变量"
            assert session.proxies == {}, "本地地址必须清空 proxies"
        finally:
            session.close()

    def test_remote_target_keeps_env_proxy_support(self) -> None:
        config = replace(
            load_config("mock"),
            api=replace(load_config("mock").api, base_url="https://api.deepseek.com"),
        )
        session = build_session(config)
        try:
            # 远端必须保留 trust_env，否则在需要代理的网络环境下直连不通
            assert session.trust_env is True
        finally:
            session.close()

    def test_injected_session_is_configured(self) -> None:
        """外部注入的 session 也应被正确处理（fixture 里就是这么用的）。"""
        config = replace(
            load_config("mock"),
            api=replace(load_config("mock").api, base_url="http://localhost:8000"),
        )
        injected = requests.Session()
        result = build_session(config, injected)
        try:
            assert result is injected
            assert result.trust_env is False
        finally:
            injected.close()
