import requests

def test_llm_api(llm_api_session, llm_case, requests_mock):
    session, base_url, headers = llm_api_session
    # mock模拟接口返回，不访问外网
    mock_response_data = {
        "choices": [{"message": {"content": "mock回复"}}]
    }
    requests_mock.post(base_url, json=mock_response_data, status_code=200)

    payload = {
        "messages": [{"role":"user", "content": llm_case["prompt"]}],
        "model": "test-model"
    }
    try:
        resp = session.post(base_url, json=payload, headers=headers, timeout=10)
    except requests.exceptions.Timeout:
        assert False, "接口请求超时"

    # 校验响应状态码
    assert resp.status_code == 200

    res_json = resp.json()
    assert "choices" in res_json
    assert len(res_json["choices"]) > 0
    assert "message" in res_json["choices"][0]
