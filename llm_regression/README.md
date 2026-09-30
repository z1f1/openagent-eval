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

## 真实接口回归结果与契约差异（重要）

真实被测对象是一个外部的 OpenAI 兼容开源服务。**它的契约与最初假设差异很大**，
以下都是实测结论（2026-09-30，多轮复现），不是推测：

| 输入 | 服务真实行为 | 与最初假设 |
| --- | --- | --- |
| 正常提问 | 200 + OpenAI 结构 | ✅ 一致 |
| 空串 / 纯空格 / 纯换行 | **200，当普通输入回答** | ❌ 原假设 400 |
| NUL 字符 / 控制字符 | **200，不校验** | ❌ 原假设 400 |
| 1001 / 10000 / 50000 / 200000 字符 | **200，无长度校验** | ❌ 原假设 413 |
| `messages: []` | 400 + `Empty input messages` | ⚠️ 曾观察到 422（两条不同校验路径） |
| `content` 缺失 / 类型错误 | 422 + `invalid_request_error` | ⚠️ 原假设 400 |
| 请求体无法解析成 JSON | **400 + 纯文本**，无结构化错误体 | ❌ 原假设 JSON + `invalid_json` |
| 不存在的模型名 | 400 + `invalid_request_error` | ❌ 原假设 404 |
| 鉴权失败 | 401（有时无 JSON 体） | ✅ 状态码一致 |

### 结论：该服务几乎没有输入校验

空值、控制字符、超长内容全部照单全收。这不是"框架预期写错了"，而是**被测服务的真实现状**。

因此用例数据里刻意保留了两组、口径分开：

- `[契约口径]` + `mock_only: true` + `mock_model: mock-strict` —— 我们认为"应当"有的校验，
  只在自建 Mock 的严格模式下执行，用来做对照；
- `[实测口径]` —— 按服务真实行为断言，在真实环境执行。

这样一眼就能看出：**它到底哪里没做输入校验**。

### 两个必须记录的发现

1. **网络层抖动**：探测中多次出现 `SSLEOFError`（连接被直接断开），
   同样请求重试即成功，且与请求体大小无关（10 万字符成功、1 万字符失败的组合都出现过）。
   它在随机地影响回归稳定性 —— 任何接入 CI 的真实回归都必须考虑重试策略。
2. **XSS 用例的教训**：发送 `<script>alert(1)</script>` 时，服务把该字符串**原样写回**了
   生成内容（"...if that were rendered as HTML on a page, `<script>alert(1)</script>`
   would trigger..."）。本次这是模型在**讨论**这段文本，不是服务端注入。
   但由此得出一个方法论结论：**用关键词扫描（"响应里有没有 `<script`"）判定 XSS 必然误报**，
   因为模型复述用户输入是正常行为。真正的输出编码责任在客户端渲染层，
   需要单独的用例与人工评估。这条已降级为"确认服务未报错且返回可用内容"。

### 双模式 Mock 设计

为了让"契约口径"和"实测口径"能同时验证，Mock 用模型名切换：

| 模型名 | 行为 |
| --- | --- |
| `mock-llm`（默认） | 宽松：对齐被测服务实测行为（空值/超长/控制字符都返回 200） |
| `mock-strict` | 严格：按"应有输入校验"的契约口径拒绝非法输入（400/413） |
| `mock-unknown` | 验证模型白名单校验 |
| `error-500` / `error-500xN` | 故障注入：持续 500 / 瞬时 500 后自愈 |

用例数据里用 `mock_model:` 声明，**仅在 mock 环境生效**；真实环境忽略它。

### 实测通过情况

```bash
python llm_regression/run_tests.py              # Mock：30 passed（含契约口径与实测口径）
python llm_regression/run_tests.py --env real   # 真实：18 passed（连跑 3 次全绿）
```

## 接入 CI

工作流：[`.github/workflows/llm-regression.yml`](../.github/workflows/llm-regression.yml)

| 作业 | 触发 | 判定 | 说明 |
| --- | --- | --- | --- |
| `mock-regression` | push / PR（限 `llm_regression/**` 变更） | **必过门禁** | 2 秒跑完 30 条，离线可跑，结果确定 |
| `real-regression` | 手动 `workflow_dispatch` 选 `real` | 需 Secrets | 未配置 `LLM_API_KEY` 时**自动跳过**，不会让门禁变红 |

产物：JUnit XML 进 CI 结果页（`require_tests: true` 防「假绿」），HTML 报告与运行摘要作为 artifact 归档 30 天。

### 关于随机 SSL 断连（缺陷 id=16）

真实服务存在随机 `SSLEOFError`，不处理会让 CI 随机变红。请求层已按错误类型区分策略：

| 错误类型 | 策略 | 原因 |
| --- | --- | --- |
| 超时 | **不重试** | 重试会把「超时用例」拖成分钟级；客户端已放弃本次调用 |
| 连接类异常（SSLEOFError / ConnectionReset） | **重试 3 次 + 指数退避**（1.5s / 3s） | 应对随机断连 |
| 5xx | **重试 + 指数退避** | 服务端临时故障 |
| 4xx / 2xx | 直接返回 | 4xx 是业务结果，重试无意义 |

重试耗尽后**返回带 `error_kind` 的结构化失败结果**，而不是抛异常 ——
这样「连接失败」会成为一条可断言的失败结果，能被失败留痕与报告记录下来，而不是中断整个会话。

### 配置真实回归的 Secrets

在仓库 `Settings → Secrets and variables → Actions` 配置：

| 名称 | 位置 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | **Secret** | 例如 `https://api.deepseek.com` |
| `LLM_API_KEY` | **Secret** | 密钥（加密存储、日志自动打码） |
| `ENABLE_REAL_REGRESSION` | **Variable** | 设为 `true` 才允许执行真实回归作业（非敏感开关） |

**为什么用两个位置**：GitHub Actions 的 **job 级 `if` 条件不允许引用 `secrets` 上下文**
（只允许 `github` / `inputs` / `needs` / `vars`），所以用一个非敏感的 Variable 作为开关，
真正的密钥仍放在 Secret 里。

> 踩坑记录：最初把 `secrets.LLM_API_KEY != ''` 写在 job 级 `if` 里，导致整个工作流解析失败 ——
> CI 直接 failure 且**作业数为 0**。用 `actionlint` 才定位到：
> `context "secrets" is not allowed here`。

### 本地校验工作流

```bash
actionlint .github/workflows/llm-regression.yml
```

## 失败留痕与禅道联动

用例失败时 `conftest.py` 的钩子会：
1. 把用例编号 / 场景 / 设计方法 / 期望结果 / 请求参数 / 响应结果写入 HTML 报告（`Authorization` 已掩码）；
2. 若启用禅道，自动生成缺陷单并提交。

### 上传整次运行信息到禅道

跑完回归后，把**执行信息 + 报告附件**归档成禅道里的一条记录：

```powershell
# 凭证放本地 .env（已忽略，不进版本库）：复制 .env.example 为 .env 后填写
python llm_regression/run_tests.py --upload-zentao
```

不加 `--upload-zentao` 时只生成本地摘要 `reports/run_summary_<env>.md`，不碰禅道。

**上传内容**：执行环境 / 接口地址 / 模型 / 执行命令 / 耗时基线 / 重试策略，
20 条用例的明细表（编号、场景、结果、耗时、HTTP 状态码、业务错误码、重试次数），
失败清单，以及 4 个附件（pytest-html 报告、JUnit XML、JSON/Markdown 摘要）。

**接口实现顺序**（实测确认，两个坑都踩过）：

```
1. POST /api.php/v2/bugs     {productID, title, steps, openedBuild:["trunk"]}
                             -> {"status":"success","id":N}      先拿到记录 id
2. POST /api.php/v2/files    multipart: file + objectType=bug + objectID=<记录id>
                             -> 附件精确绑定到该记录
```

- ⚠️ 把 `uid` 放进创建记录的 payload 会**阻止**附件关联；
- ⚠️ 不带 uid 时禅道会把该产品下所有「未挂载文件」自动挂到新记录上——隐式行为，
  有残留文件或并发时会挂错，所以**不要依赖它**，必须用 `objectID` 显式绑定。
- `openedBuild` 是 Bug 表单必填项；产品没有版本数据时用 `trunk` 即可。

### 环境变量（全部可从 `.env` 读取）

| 变量 | 说明 |
| --- | --- |
| `ZENTAO_ENABLED` | 置 `1` 才启用禅道相关功能 |
| `ZENTAO_BASE_URL` | 例如 `http://127.0.0.1:81/zentao` |
| `ZENTAO_PRODUCT_ID` | 产品 ID |
| `ZENTAO_ACCOUNT` / `ZENTAO_PASSWORD` | 账号密码，脚本自动换 token |
| `ZENTAO_TOKEN` | 也可直接用 token（优先于账号密码） |
| `ZENTAO_BUILD` | 影响版本，默认 `trunk` |

> 凭证**绝不写进代码**。CI 里走 Secrets / 环境变量；本地走 `.env`（已在 `.gitignore` 中）。

## 怎么加一个用例

1. 在 `cases/<场景>.yaml` 加一条数据（含 `id` / `title` / `design` / `prompt` / `expect`）；
2. 跑 `python run_tests.py`。

不用改用例代码、不用改断言。新接口接入只需再补一份 `config.yaml` 环境 + 对应 `cases/`。

## 已知待办

- [ ] `illegal_characters` 的错误码契约目前来自 Mock，需与后端确认（真实服务可能对控制字符返回 200）
- [ ] real 环境 `latency_baseline: 20.0` 是经验值，应按实测 P95 重新标定
- [ ] 鉴权域用例尚未自动化（Mock 已实现 401 `invalid_api_key` 分支，补数据即可接入）
- [ ] 接入 CI（Mock 回归作为 push/PR 必过门禁，JUnit XML 进结果页）
