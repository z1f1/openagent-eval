# llm_regression —— AI 大模型对话接口自动化回归

把「手工逐个点接口」变成**可重复、可度量、可追溯**的自动化回归。

## 它解决什么问题

| 原方式（手工 / 半自动） | 本项目 |
| --- | --- |
| 接口地址不存在时，全部用例 `ConnectionError`，项目跑不起来 | 自建 Mock 服务实现契约，一条命令 19 条全绿 |
| 断言只查状态码，返回一个句号也能通过 | 五层断言：状态码 / 结构 / 字段类型 / 业务错误码 / 耗时基线 |
| 空、超长、非法字符靠人工想 | 等价类 + 边界值固化进数据，常态化回归 |
| 真实大模型抖动、按量计费，异常分支无法复现 | Mock 注入 500、瞬时 500、超时、非法编码，结果确定 |
| 失败后靠截图和记忆排查 | 失败自动留痕（请求参数 / 响应体 / 耗时）+ 可自动提禅道 |

## 目录结构（四层设计）

```
llm_regression/
├── config.yaml          配置层：地址 / 鉴权占位符 / 超时 / 重试 / 耗时基线
├── config.py            配置层：加载与占位符解析（缺变量直接报错，不拿空地址发请求）
├── client.py            请求层：requests.Session 统一封装 + 重试 + 全程留痕
├── cases.py             数据层：加载 cases/*.yaml，新增用例只加数据
├── cases/               数据层：四类场景用例数据
│   ├── normal.yaml      正常（有效等价类）        TC01 TC05
│   ├── empty.yaml       空输入（边界值 0 字符）   TC02 TC06~TC09
│   ├── oversized.yaml   超长（边界三点 999/1000/1001）TC03 TC10~TC12
│   └── illegal.yaml     非法字符 / 异常编码 / 异常分支 TC04 TC13~TC19
├── assertions.py        断言规则层：五层可度量断言
├── mock_server.py       契约的显式实现：Mock 服务（仅标准库）
├── zentao_plugin.py     禅道提单：凭证走环境变量，失败不影响回归判定
├── conftest.py          fixture（前置/teardown）+ 参数化驱动 + 失败留痕钩子
├── test_llm_api.py      用例层：四个场景函数，只做「取数据 → 调用 → 断言」
├── run_tests.py         一键回归入口
└── pytest.ini           用例发现 / 标记 / 日志格式
```

## 接口契约

```
POST /llm/chat
请求：{"model": "mock-llm", "prompt": "用户输入"}

200 {"content": "...", "usage": {...}}                    正常
400 {"error": {"code": "empty_prompt"}}                   prompt 缺失 / 非字符串 / 空串
400 {"error": {"code": "blank_prompt"}}                   prompt 为纯空白
400 {"error": {"code": "illegal_characters"}}             含 NUL 或不可打印控制字符
400 {"error": {"code": "invalid_json"}}                   请求体非法 JSON / 非 UTF-8
413 {"error": {"code": "context_length_exceeded"}}        prompt 超过 1000 字符
401 {"error": {"code": "invalid_api_key"}}                缺少 / 非法 Authorization
500 {"error": {"code": "model_internal_error"}}           故障注入
```

契约写在 `mock_server.py` 里，**由 Mock 和真实服务共同实现**。真实服务上线后，同一批用例换个 `--env` 就能跑。

## 快速开始

```bash
python -m pip install -r requirements.txt
python run_tests.py                      # Mock 环境跑全部四类场景（19 条）
python run_tests.py --scenario illegal   # 只跑非法 / 异常场景
python run_tests.py --keyword TC1        # 按用例编号过滤
```

产物：`reports/latest_mock.html`（可视化报告）、`reports/junit_mock.xml`（CI 用）。

### 跑真实接口

```powershell
$env:LLM_BASE_URL="https://api.deepseek.com"
$env:LLM_API_KEY="sk-..."
$env:LLM_MODEL="deepseek-chat"
python run_tests.py --env real --scenario normal
```

缺凭证会**立即以退出码 4 中止**并提示缺少的变量，不会用空地址发请求，也不会留下空报告冒充「已执行的真实回归」。

> 注意：真实接口的契约与 Mock 不一定一致（例如真实服务对空输入可能返回 200 而不是 400）。
> **两边跑出来的差异就是结论**——那是要跟后端确认的契约问题，不是框架问题。

## 断言规则（五层）

| 规则 | 层级 | 校验内容 |
| --- | --- | --- |
| R1 | 传输层 | HTTP 状态码精确匹配；传输异常视为失败 |
| R2 | 传输层 | 超时分支：`error_kind == "timeout"` 才算预期内失败 |
| R3 | 传输层 | 重试次数下限，验证重试策略真的生效（TC17/TC18） |
| R4 | 结构层 / 类型层 | 响应结构与关键字段类型（`content: str`、`usage: dict`） |
| R5 | 业务层 | 业务错误码 `error.code` |
| R6 | 内容层 | 生成内容非空且有最小长度——只查 `len() > 0` 会让「返回一个句号」通过 |
| R7 | 性能层 | 耗时阈值（用例级 `latency_max`，或 `auto` 跟随环境基线） |
| R8 | 安全层 | XSS payload 不得在响应中回显可执行片段 |

### 两个刻意的设计决定

1. **TC04（XSS）不再断言 400。** `<script>alert(1)</script>` 是合法字符集内的字符串，后端正确做法是当作普通输入处理并做输出编码，而不是拒绝。把 400 写死进断言等于把未确认的设计当成需求。现在断言的是「正常返回 + 响应不含 `<script` / `javascript:` / `onerror=`」。
2. **`latency_max: auto`** 表示跟随当前环境的耗时基线。Mock 是毫秒级（基线 0.5s），真实大模型是秒级，两者不能共用写死的阈值，否则真实回归会被误判为性能回退。

## 失败留痕与禅道提单

用例失败时 `conftest.py` 的钩子会：
1. 把用例编号 / 场景 / 设计方法 / 期望结果 / 请求参数 / 响应结果写入 HTML 报告（`Authorization` 已掩码）；
2. 若配置了禅道环境变量，自动生成缺陷单并提交。

```powershell
$env:ZENTAO_ENABLED="1"
$env:ZENTAO_BASE_URL="http://127.0.0.1:81/zentao/api.php/v2"
$env:ZENTAO_TOKEN="你的令牌"
$env:ZENTAO_PRODUCT_ID="1"
$env:ZENTAO_MODULE_ID="1"
```

> 凭证全部走环境变量，**代码里不留明文**。提单失败只记日志，不改变用例的通过/失败判定——提单不能污染回归结论。

## 怎么加一个用例

1. 在 `cases/<场景>.yaml` 加一条数据（含 `id` / `title` / `design` / `prompt` / `expect`）；
2. 跑 `python run_tests.py`。

不用改用例代码、不用改断言。新接口接入只需再补一份 `config.yaml` 环境 + 对应 `cases/`。

## 已知待办

- [ ] `illegal_characters` 的错误码契约目前来自 Mock，需与后端确认（真实服务可能对控制字符返回 200）
- [ ] real 环境 `latency_baseline: 20.0` 是经验值，应按实测 P95 重新标定
- [ ] 鉴权域用例尚未自动化（Mock 已实现 401 `invalid_api_key` 分支，补数据即可接入）
- [ ] 接入 CI（Mock 回归作为 push/PR 必过门禁，JUnit XML 进结果页）
