# 工作留痕

记录本项目的搭建过程、关键决策与踩过的坑。目的是让每一步都可追溯、可复现。

---

## 2026-10-01　阶段一：项目骨架 + 首个 UI 测试

### 1. 选型决策记录

| 角色 | 选定 | 备选 | 决策理由 |
| --- | --- | --- | --- |
| 靶子（被测系统） | **Gitea v28.0.0** | Vikunja、禅道、Jira、MeterSphere | 单二进制 + 内置 SQLite，**无 Docker 可跑**；MIT 许可；界面规范、测试点全；权限场景天然存在 |
| UI 测试 | **Selenium 4.49.0** | Playwright 1.63.0、SeleniumBase | 本机 miniconda 已装；Selenium 4.6+ 自带驱动管理 |
| 浏览器 | **Microsoft Edge** | Chrome、Chromium | 本机已有，**免下载 130MB+** |
| 解释器 | **miniconda base（3.12.2）** | 项目 `.venv` | 已含 selenium/playwright/pytest |

**排除项及原因**：

| 被排除 | 原因 |
| --- | --- |
| Jira | 商业闭源，镜像需 Atlassian 账号，30 天试用；且本机无 Docker |
| MeterSphere | 是测试平台不是靶子；必须 Docker |
| hexstrike-ai | 攻击性渗透工具，法律风险；且是 MCP 服务器，非测试框架 |
| VulnWhisperer | 已归档；是漏洞数据聚合器，不做扫描；需 ELK 全家桶 |
| qa-automation-portfolio | 26 框架堆砌但基本跑不起来；37 star；仅参考其结构 |

### 2. 靶子部署记录

**下载**（踩坑 1 次）：

| 步骤 | 现象 | 处理 |
| --- | --- | --- |
| 下载 Windows 包 | 原始 exe 116.7 MB，压缩包 `.xz` 40.5 MB | 下压缩版 |
| `tar -xf gitea.xz` | ❌ `Unrecognized archive format` | Windows 的 tar 不认裸 xz 流 → 改用 Python `lzma` 模块解压 |
| 首次启动 | ❌ 退出码 1 | 首次运行需初始化数据库与仓库目录，中途退出；**第二次启动正常** |

**部署位置**：`D:\Users\LX\Documents\GitHub\_sut\`

| 文件 | 说明 |
| --- | --- |
| `gitea.exe` | 116.7 MB |
| `custom\conf\app.ini` | 测试专用配置 |
| `data\` | SQLite 数据库 + Git 仓库 |

**配置要点**（都是为了让自动化测试可行）：

| 配置 | 值 | 原因 |
| --- | --- | --- |
| `INSTALL_LOCK` | true | 跳过 `/install` 安装向导 |
| `DB_TYPE` | sqlite3 | 免安装数据库 |
| `DISABLE_REGISTRATION` | false | UI 测试需要能自建账号 |
| `ENABLE_CAPTCHA` | false | ★ **验证码会直接废掉 UI 自动化** |
| `DISABLE_SSH` | true | 测试用不到，避免占端口 |
| `DEFAULT_THEME` | gitea | 固定主题，避免元素随主题变化 |

**启动命令**：

```powershell
D:\Users\LX\Documents\GitHub\_sut\gitea.exe web --config D:\Users\LX\Documents\GitHub\_sut\custom\conf\app.ini
```

桌面已放置启动器：`启动 Gitea 测试靶子.bat`（GBK 编码，避免 cmd 中文乱码）。

### 3. 环境勘察记录

本机共发现 **4 个可用 Python 环境**（含大量无关的嵌入式 Python）：

| 环境 | Python | 关键库 | 用途 |
| --- | --- | --- | --- |
| `miniconda3`（base） | 3.12.2 | selenium 4.49.0、playwright 1.63.0、pytest 9.1.1 | ★ **本项目使用** |
| `envs\ai_test_env` | 3.9.25 | pytest 8.4.2、requests | 保留 |
| `envs\cognitest` | 3.10.21 | pytest 9.1.1、requests | 保留 |
| `openagent-eval\.venv` | 3.12.14 | pytest 8.4.2 | ★ **上个项目专用，不可动** |

> ⚠️ 环境多是个隐患：曾出现"装了 selenium 但项目里找不到"，原因是装进了 miniconda 而项目用的是 `.venv`。

### 4. 关键技术问题与解决

#### 问题 1：Selenium 下载驱动报 `Bad Gateway`

| 项 | 内容 |
| --- | --- |
| 现象 | `WebDriverException: Message: Bad Gateway`，浏览器无法启动 |
| 原因 | 系统设了代理（`127.0.0.1:7897`），Selenium Manager 下载 msedgedriver 时被代理拦截 |
| 解决 | 清除 `HTTP_PROXY` / `HTTPS_PROXY` 后正常，驱动自动下载成功（Edge 154.0.4258.37） |
| 框架层治理 | `common/browser.py` 的 `clear_proxy_env()`：本地靶子自动清代理；Edge 启动参数加 `--no-proxy-server` |

> 这与上一个项目遇到的"代理导致 Mock 请求 502"是同一类根因：**本地地址绝不能走代理**。

#### 问题 2：配置层解析所有靶子导致启动失败

| 项 | 内容 |
| --- | --- |
| 现象 | 禅道配置含 `${ZENTAO_PASSWORD}`，未设置时抛 `KeyError`，导致跑任何测试都失败 |
| 原因 | 自检脚本遍历所有靶子并逐个解析占位符 |
| 解决 | 只解析默认靶子的占位符；其他靶子解析失败仅提示不中断 |

### 5. 交付产物

**目录结构**：

```
unified-testing/
├── README.md                    项目说明 + 已知环境坑
├── run.py                       统一测试入口
├── pytest.ini                   共用 pytest 配置
├── conftest.py                  共用 fixture + 失败留痕钩子
├── .gitignore / .env.example
├── targets/targets.yaml         靶子清单（gitea / zentao）
├── config/settings.yaml         全局设置与性能门禁阈值
├── common/                      ★ 共用层
│   ├── config.py                配置加载（支持 ${ENV} 占位符）
│   └── browser.py               Selenium 封装（含代理治理）
├── ui/
│   ├── pages/gitea_home.py      页面对象
│   └── tests/test_smoke.py      6 条冒烟用例
└── reports/                     报告与截图
```

### 6. 验证结果（实测）

| 验证项 | 结果 |
| --- | --- |
| 冒烟脚本（Selenium + Edge + Gitea） | ✅ 通过，页面加载 0.4s |
| 配置层自检 | ✅ gitea 正常解析；zentao 缺变量时仅提示 |
| UI 冒烟测试 | ✅ **6 passed in 4.79s** |
| 统一入口 `run.py ui` | ✅ 退出码 0，耗时 8.5s |
| HTML 报告 | ✅ `reports/report_ui.html`（36.7 KB） |
| JUnit XML | ✅ `reports/junit_ui.xml` |
| 浏览器启动耗时 | 1.4s |
| 驱动版本匹配 | ✅ msedgedriver 154.0.4258.37 与 Edge 一致 |

**6 条冒烟用例覆盖**：

| 用例 | 断言内容 |
| --- | --- |
| `test_title_contains_site_name` | 页面标题含站点名（证明加载成功） |
| `test_landing_url_is_target_root` | 地址与配置一致（证明访问的是正确系统） |
| `test_login_entry_visible` | 登录入口存在 |
| `test_signup_entry_visible` | 注册入口存在（UI 测试依赖自注册） |
| `test_main_layout_present` | 主容器/导航栏/页脚存在 |
| `test_page_has_meaningful_content` | 源码长度 > 1000（防"200 但空白"假成功） |

### 7. 下一步计划

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 一 | 项目骨架 + 靶子部署 + 首个 UI 测试 | ✅ **已完成** |
| 二 | UI 功能用例：注册、登录、访问控制 | ✅ **已完成** |
| 三 | 仓库与 Issue 用例（含越权访问） | ⏳ 待做 |
| 四 | 接口测试（requests + pytest） | ⏳ 待做 |
| 五 | 性能测试（Locust） | ⏳ 待做 |
| 六 | 不稳定用例检测 | ⏳ 待做 |
| 七 | 安全测试（被动扫描） | ⏳ 待做 |
| 八 | CI 接入 | ⏳ 待做 |

---

## 2026-10-01　阶段二：UI 功能用例（注册 / 登录 / 访问控制）

### 1. 本阶段目标

从"只读冒烟"进入"真实功能验证"。共新增 21 条用例，分三组：

| 文件 | 用例数 | 覆盖内容 |
| --- | --- | --- |
| `test_smoke.py` | 6 | 靶子可达、首页元素、内容非空 |
| `test_signup.py` | 8 | 正常注册、必填校验、格式校验、密码一致性、用户名重复 |
| `test_login.py` | 7 | 正常登录、错误密码、不存在用户、空凭据、未登录访问受保护页面 |

### 2. ★ 四个真实问题与定位过程

本阶段最大的收获不是"用例跑通了"，而是通过**分层定位**找出了 4 个非显而易见的问题。
每一个都遵循同样的方法：**先探查真实现状，再改代码** —— 而不是靠猜选择器。

#### 问题 1：页面有多个 form，提交按钮点错了

| 项 | 内容 |
| --- | --- |
| 现象 | 11 条用例超时，报 `TimeoutException`，且驱动输出原生崩溃堆栈 |
| 初步误判 | 以为是 Edge 驱动不稳定 |
| 实际原因 | Gitea 页面上有 **3 个 form**：两个"计时器"表单 + 真正的业务表单。通用选择器 `button[type='submit']` 匹配到的是**第一个 form（计时器）的按钮**，点错了目标 |
| 定位手段 | 写探查脚本枚举页面上所有 `form` 及其内部 `input`/`button` |
| 修复 | 把提交按钮限定在 `form.ui.form` 内：`form.ui.form button[type='submit']` |
| 附带发现 | 注册表单字段 id 确认为 `user_name` / `email` / `password` / `retype`；提交按钮文字为「注册帐号」 |

> 方法论：**选择器不能猜**。用一次探查脚本换来准确定位，比反复试错快得多。

#### 问题 2：区分"HTML5 原生校验"与"服务端校验"

| 项 | 内容 |
| --- | --- |
| 现象 | 空表单提交后停留在注册页，但**没有任何错误文本**，原断言 `assert result.error_messages` 失败 |
| 实际原因 | 校验发生在**浏览器原生层**（HTML5 `required` / `type=email`），浏览器直接阻止提交，请求根本没发到服务端，因此没有服务端错误文本 |
| 修复 | 用例同时接受两种"被拦住"的证据：服务端错误文本 **或** 表单处于 `invalid` 状态（用 `form.checkValidity()` 判断） |
| 设计取舍 | 不断言错误文案（措辞会变），只断言"用户能得到反馈"，提高用例稳定性 |

#### 问题 3：隐藏的空提示容器导致误判

| 项 | 内容 |
| --- | --- |
| 现象 | 登录页在**未做任何操作**时就存在 `.ui.negative.message` 元素 |
| 实际原因 | Gitea 预渲染了一个**空且不可见**的错误容器作为占位 |
| 危害 | 若直接判断"元素存在"，会得出"任何操作都报错"的错误结论 |
| 修复 | 新增 `visible_texts()`：过滤不可见元素与空文本，只统计真正可见且有内容的提示 |

#### 问题 4：★ 已登录状态下无法打开注册页（最关键）

| 项 | 内容 |
| --- | --- |
| 现象 | "重复用户名"用例在**第二次**打开注册页时报超时 |
| 初步误判 | 以为是 `driver.get()` 加载页面超时（page_load_timeout） |
| 实际原因 | 注册成功后浏览器处于**登录态**，此时访问 `/user/sign_up` 会被 Gitea **重定向到首页**。页面确实加载完成了，只是**永远等不到注册表单元素** |
| 定位手段 | 打印导航前后的 `current_url` 与 `document.readyState`，发现 `readyState=complete` 但地址是首页 |
| 修复 | `GiteaSignUpPage.open()` 默认先清除会话；若仍被重定向则再清一次并重试 |
| 影响面 | 这条同时修掉了两个用例（重复用户名、注册后可登录） |

> 典型"**报错信息指向错误方向**"的案例：报的是元素等待超时，根因却是导航被重定向。

### 3. 架构级修复：用例状态隔离

**问题**：最初浏览器是**会话级共享**的，导致用例互相污染。

| 表现 | 原因 |
| --- | --- |
| 冒烟用例找不到"登录/注册"链接 | 前一个用例注册后处于登录态，导航栏变成用户菜单 |
| 登录用例跳转异常 | 已登录时访问登录页会重定向 |
| 一条失败引发级联失败 | 一个用例改了会话状态，后续所有用例受影响 |

**实测对比**：

| 方案 | 结果 |
| --- | --- |
| 会话级共享浏览器 | ❌ **8 failed / 7 passed** |
| 每用例独立浏览器 | ✅ **21 passed** |

**改为**：

| 设计 | 说明 |
| --- | --- |
| `driver` → **函数级** fixture | 每用例独立浏览器与会话，从根上消除状态污染 |
| 进入用例前 `delete_all_cookies()` | 确保干净起点 |
| 启动失败自动重试（`max_retries: 2`） | 应对 Edge 驱动偶发启动失败 |
| `is_session_alive()` + `quit_quietly()` | 会话崩溃时安全重建与清理 |
| `session_driver` → 会话级 | 仅供"注册测试账号"这类**一次性前置**使用，不参与普通用例 |

**代价与取舍**：每用例重启浏览器使总耗时升到约 112 秒（21 条）。
**正确性优先于速度** —— 用例之间的隐式耦合是维护成本最高的技术债。

### 4. 本阶段验证结果

| 项 | 结果 |
| --- | --- |
| UI 用例总数 | **21 条** |
| 执行结果 | ✅ **21 passed in 112.21s** |
| 浏览器启动耗时 | 1.2 ~ 1.7 秒/次 |
| 失败留痕 | ✅ 已实测生效（失败用例自动截图到 `reports/screenshots/`） |
| 靶子数据影响 | 注册用例会产生测试账号（`qa*` / `tmp*` / `dup*`），属预期；未修改或删除任何既有数据 |

### 5. 阶段二小结（可写入汇报的要点）

| 维度 | 内容 |
| --- | --- |
| 用例设计方法 | 等价类划分、边界值、场景法 |
| 覆盖场景 | 正常路径 + 负向校验 + 访问控制 |
| 解决的问题 | 4 个真实缺陷/隐患（选择器歧义、校验层次、隐藏容器、登录态重定向） |
| 工程改进 | 用例状态隔离，失败率从 53% 降至 0 |
| 踩坑记录 | 选择器必须探查、报错信息可能指向错误方向、共享状态是测试大忌 |
