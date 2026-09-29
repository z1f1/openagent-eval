import pytest
import requests

@pytest.fixture(scope="session")
def llm_api_session():
    # 创建session复用连接
    session = requests.Session()
    # 大模型接口地址，面试说明可替换真实LLM地址
    base_url = "https://xxx.xxx.com/v1/chat/completions"
    headers = {
        "Authorization": "Bearer xxx",
        "Content-Type": "application/json"
    }
    yield session, base_url, headers
    session.close()

@pytest.fixture(params=[
    {"prompt": "你好"},
    {"prompt": ""}, #空输入
    {"prompt": "文本"*2000}, #超长文本
])
def llm_case(request):
    return request.param
