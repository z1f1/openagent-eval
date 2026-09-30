# 工作记录：AI 大模型对话接口自动化回归测试

> 时间：2026-09-30 17:00 ~ 2026-10-01 00:30
> 产出位置：`llm_regression/`（分支 `test/llm-regression-framework`）
> 本文所有数据均来自仓库真实产物（git 提交、JUnit XML、禅道记录），可逐条复核。

---

## 一、一句话总结

把 `llm_regression/` 从一个**跑不起来**的接口测试草稿（被测服务不存在，4 条用例全部 `ConnectionError`），
改造成**四层架构的自动化回归框架**：
自建 Mock 实现契约、30 条用例双模式全绿、真实接口 18 条全绿、失败自动留痕、运行信息自动归档到禅道。

---

## 二、工作量与规模（可复核）

| 指标 | 数值 | 证据来源 |
| --- | --- | --- |
| 提交数 | 15 | `git log origin/main..HEAD` |
| 变更规模 | 32 文件，+3280 / −819 | `git diff --shortstat` |
| 用例总数 | 30（Mock 全跑）/ 18（真实全跑） | `cases/*.yaml` 与 `reports/junit_*.xml` |
| Mock 回归 | 30 passed / 0 failed | `reports/junit_mock.xml`（time=2.171s） |
| 真实回归 | 18 passed / 0 failed | `reports/junit_real.xml`（time=19.050s） |
| 核心代码 | 约 1500 行 Python + 288 行用例数据 | 文件行数统计 |

### 分层产出清单

| 层 | 文件 | 行数 | 职责 |
| --- | --- | --- | --- |
| 配置层 | `config.yaml` / `config.py` | 39 / 113 | 地址、鉴权、超时、重试、耗时基线；凭证走环境变量 |
| 请求层 | `client.py` | 205 | `requests.Session` 统一封装 + 重试 + 全程留痕 |
| 数据层 | `cases.py` + `cases/*.yaml` | 110 / 288 | 30 条用例数据，加用例只加一行 |
| 断言层 | `assertions.py` | 156 | 8 类可度量断言 |
| 契约实现 | `mock_server.py` | 229 | 双模式 Mock（宽松/严格） |
| 用例层 | `test_llm_api.py` | 82 | 4 个场景函数，只做调用+断言 |
| 留痕与集成 | `conftest.py` / `zentao_run_report.py` / `zentao_plugin.py` | 335 / 279 / 105 | 失败留痕、运行信息归档、缺陷自动提单 |
| 入口与工程 | `run_tests.py` / `pytest.ini` / `requirements.txt` / `.vscode/` / `run.bat` | — | 一键回归与开发环境 |

---

## 三、工作过程与关键技术决策

### 3.1 起点问题诊断

| 现状 | 证据 |
| --- | --- |
| 被测服务不存在 | `BASE_URL = http://127.0.0.1:8000/llm/chat`，实测连接被拒绝 |
| 契约是逆推的 | 400/400/400 只写在 `assert` 里，无文档、无 Mock、无服务端实现 |
| 4 条用例全部无法执行 | `pytest` 跑起来全是 `ConnectionError` |

**决策**：先让被测对象存在，再谈框架。用 Python 标准库 `http.server` 实现 `/llm/chat`，
把契约显式化 —— 这一步做完，项目从"跑不起来"变为"可以回归"。

### 3.2 分层重构

| 改造前 | 改造后 |
| --- | --- |
| `BASE_URL` 是模块级全局变量 | 配置层 `config.yaml`，支持 mock/real 多环境 |
| 断言直接写 `assert resp.status_code == 400` | 断言层 8 类规则，策略与数据分离 |
| 测试数据写死在函数体 | 数据层 `cases/*.yaml`，新增用例零代码改动 |
| 无 fixture、无会话复用 | session 级 fixture 统一管理连接、鉴权、Mock 启停 |
| 只断言状态码 | 状态码 + JSON 结构 + 字段类型 + 业务错误码 + 内容非空 + 耗时基线 + 重试次数 |

### 3.3 关键决策：双模式 Mock

真实服务**几乎不做输入校验**（空值、控制字符、超长内容全部返回 200）。
为了同时表达"它实际怎么表现"和"它应该怎么表现"，用模型名切换契约：

| 模型名 | 行为 | 用途 |
| --- | --- | --- |
| `mock-llm` | 宽松：对齐真实服务实测行为 | 验证「它现在是这样」 |
| `mock-strict` | 严格：按应有契约拒绝非法输入 | 验证「它应该这样」，做对照 |
| `mock-unknown` | 走模型白名单校验 | 验证模型名校验 |
| `error-500` / `error-500xN` | 故障注入 | 验证重试策略与自愈 |

用例数据里用 `[契约口径]` / `[实测口径]` 标注，一眼看出差异。

---

## 四、发现并修复的问题（均可复核）

### 4.1 既有代码的问题（接手时发现）

| # | 问题 | 证据 | 处理 |
| --- | --- | --- | --- |
| 1 | 明文密码硬编码在代码里（禅道 admin 口令） | `get_zentao_token.py` 第 6 行（此处不转录密码原文） | 改为读环境变量 / `.env` |
| 2 | 明文禅道 token 硬编码 | 根目录 `conftest.py` 第 6 行 | 同上，并修正 Bug 必填字段 `productID`/`openedBuild` |
| 3 | `llm_regression/_init_.py` 文件名拼写错误（单下划线） | git 提交 `d853305` | 改为正确的 `__init__.py` |
| 4 | `requests-mock` 声明为依赖但从未安装，用例跑不起来 | `python -c "import requests_mock"` → MISSING | 改为自建 Mock，不再依赖它 |
| 5 | `pyproject.toml` 的 `testpaths = [".", "tests"]` 导致根目录误收集 | 配置对比 | 回退为 `["tests"]` |
| 6 | 仓库根 `report.html` 是陈旧产物 | 生成于旧版用例 | 删除 |

### 4.2 开发过程中发现并修复的自有缺陷（真实踩坑）

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 7 | 真实回归时 `pytest` 直接 `INTERNALERROR` 崩掉整份报告 | 失败留痕序列化 `bytes` 请求体时 `json.dumps` 抛 `TypeError` | 加 `_json_safe` 降级处理；Mock 模式全绿时暴露不出来，只有真机才踩到 |
| 8 | `python -m pytest` 直连报 `No module named 'cases'` | `pytest.ini` 的 `pythonpath = .` 相对 rootdir 解析，模块目录没进 `sys.path` | 在 `conftest.py` 注入模块目录到 `sys.path` |
| 9 | 跑 `mock` 环境被 `real` 环境未设置的变量卡住 | 占位符解析先于环境选择 | 改为只解析被选中环境的占位符 |
| 10 | 真实回归全部 404 | `path` 沿用了 Mock 的 `/llm/chat` | 真实环境改为 `/v1/chat/completions` |
| 11 | 真实回归报 `missing field 'messages'` | 请求体发的是 Mock 格式 `{"prompt"}` | 增加 `payload_style` 配置，真实用 OpenAI 格式 |
| 12 | 真实回归结构断言全失败 | 断言按扁平 `content` 写，真实是 `choices[0].message.content` | 新增 `openai_chat` 结构规则，内容提取器兼容两种结构 |
| 13 | 报告表格「场景 / 用例」列恒为 `-` | `report._oae_case` 从未赋值 | 在 `make report` 钩子里补赋值 |
| 14 | MA 引用 `pytest-html` 报 `unrecognized arguments: --html` | 环境里主项目依赖把 pytest 升到 9 并顶掉 `pytest-html` | 重装锁定依赖，并把 pytest 锁定上调到 8.4.2 以兼容主项目的 `pytest-asyncio` |

### 4.3 禅道集成的接口探测（不猜，全部实测）

为了做"运行信息上传禅道"，先做了受控实验确定附件关联规则：

| 假设 | 实验数据 | 结论 |
| --- | --- | --- |
| "uid 相同就能关联附件" | 带 uid 的记录 → **0 附件**；不带 uid → 3 附件全挂上 | ❌ 假设错误 |
| 显式 `objectType` + `objectID` | 先建记录再带 `objectID` 上传 → **精确挂 1 个** | ✅ 采用 |

最终实现顺序：**先建记录拿 id → 再带 `objectType=bug` + `objectID=<id>` 上传附件**。
不依赖"不带 uid 自动全挂"这种隐式行为 —— 有残留文件或并发时会挂错。

同时发现禅道删除是**软删除**，实验产生的 9 条记录需要硬删才彻底清理（已清理）。

---

## 五、对被测服务的质量发现（真实接口回归产出）

这些是 Mock 环境**永远发现不了**的，属于这次工作的核心产出：

| # | 发现 | 实测证据 | 性质 |
| --- | --- | --- | --- |
| 1 | **几乎不做输入校验**：空串 / 纯空格 / 纯换行 / NUL 字符 / 控制字符全部返回 200 并正常回答 | 多轮复现，`cases/empty.yaml`、`cases/illegal.yaml` 的 `[实测口径]` 用例 | 契约缺失 |
| 2 | **无长度上限校验**：1001 / 10000 / 50000 / 200000 字符全部 200 | `cases/oversized.yaml` | 契约缺失 |
| 3 | **畸形请求只回纯文本**：请求体无法解析 JSON 时返回 400 + `Failed to parse the request body as JSON`，无结构化错误体 | `cases/illegal.yaml` TC15/TC16/TC32 | 错误体不规范 |
| 4 | **状态码不统一**：结构错误是 422，业务校验是 400，模型名不存在是 400（非 404） | `cases/empty.yaml` TC22~TC24 | 接口设计问题 |
| 5 | **网络层随机抖动**：多次 `SSLEOFError`，同样请求重试即成功，且与请求体大小无关 | 探测记录：10 万字符成功、1 万字符失败的组合都出现过 | 稳定性风险 |
| 6 | **输入被原样写回生成内容**：发 `<script>alert(1)</script>`，回答里出现该字符串 | `cases/illegal.yaml` TC04 的设计说明 | 需评估（本次是模型在讨论文本，非注入） |

### 由发现 6 得出的方法论结论

**用关键词扫描（"响应里有没有 `<script`"）判定 XSS 必然误报** ——
因为模型复述用户输入是正常行为。原断言规则已降级，并在代码注释与 README 中写清原因：
真正的输出编码责任在客户端渲染层，需要单独用例 + 人工评估。

---

## 六、验证证据链（四层留痕）

| 层级 | 产物 | 位置 | 用途 |
| --- | --- | --- | --- |
| 代码级 | 15 个语义化提交 | `git log` | 每一步改动可追溯、可回滚 |
| 用例级 | 30 条参数化用例数据 | `cases/*.yaml` | 新增场景零代码改动 |
| 执行级 | HTML 报告 + JUnit XML + 运行摘要 | `reports/` | 失败带请求参数与响应体，可直接贴缺陷单 |
| 管理级 | 禅道记录 + 4 个附件 | 禅道 `bug-view-11.html` | 团队可见的项目动态 |

### 复现命令

```powershell
# Mock 回归（离线，秒级）
python llm_regression/run_tests.py

# 真实接口回归（走 .env 里的凭证）
python llm_regression/run_tests.py --env real

# 上传运行信息到禅道
python llm_regression/run_tests.py --env real --upload-zentao

# 只看某一类场景
python llm_regression/run_tests.py --scenario illegal
```

### 失败留痕效果（可现场演示）

把任一条用例的期望值改错后重跑，HTML 报告里该用例会展开出：
用例编号、场景分类、设计方法、期望结果、**请求参数**、**响应结果**（含实际状态码、耗时、重试次数）。
`Authorization` 已掩码为 `Bearer ***`。

---

## 七、遗留与下一步

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 契约差异是否提缺陷给服务方 | **待办** | 第五节 5 条发现可整理成缺陷单提交（禅道提单链路已打通） |
| SSL 抖动治理 | **待办** | 真实回归若接入 CI 门禁，需加重试策略或标记为已知不稳定 |
| XSS 输出编码验证 | **待办** | 需要客户端渲染层的用例，不能靠关键词 |
| CI 接入 | 未开始 | Mock 回归可作为 push/PR 必过门禁，JUnit XML 已就绪 |
| 鉴权域用例 | 未开始 | 401 分支已可实现，补数据即可接入 |
| 分支推送 | 未执行 | 15 个提交在 `test/llm-regression-framework`，`main` 未被污染 |
