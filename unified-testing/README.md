# unified-testing

面向 Web 系统的**统一自动化测试框架**：UI、接口、性能、安全四类测试共用配置、报告与缺陷闭环。

## 这是什么

一个测试框架，用来测**独立运行的 Web 系统**（下称"靶子"）。

| 层 | 内容 |
| --- | --- |
| 靶子（被测对象） | 独立运行的外部系统，如 Gitea、禅道 —— **不属于本项目** |
| 本项目 | 测试框架：配置加载、浏览器封装、断言、报告、缺陷集成 |

## 目录结构

```
unified-testing/
├── targets/targets.yaml     靶子清单（新增靶子只改这里）
├── config/settings.yaml     全局设置（超时、阈值、门禁标准）
├── common/                  共用层 ★ 所有测试类型复用
│   ├── config.py            统一配置加载（支持 ${ENV} 占位符）
│   └── browser.py           Selenium 封装（含代理治理）
├── ui/                      UI 测试
│   ├── pages/               页面对象（Page Object）
│   └── tests/               用例
├── reports/                 测试产物（报告、截图）
└── README.md
```

## 环境要求

| 项 | 值 |
| --- | --- |
| Python | `C:\Users\LX\miniconda3\python.exe`（3.12.2） |
| 已装 | selenium 4.49.0、playwright 1.63.0、pytest 9.1.1、requests |
| 浏览器 | Microsoft Edge（本机已有，免下载 Chromium） |
| 驱动 | Selenium Manager 自动管理，首次运行自动下载 |

## 使用方法

### 1. 确认靶子在运行

```powershell
# 启动被测系统（Gitea），或用桌面快捷方式
D:\Users\LX\Documents\GitHub\_sut\gitea.exe web --config D:\Users\LX\Documents\GitHub\_sut\custom\conf\app.ini
```

访问 <http://127.0.0.1:3000> 能打开即正常。

### 2. 配置本地凭证

账号密码不写在仓库里，统一走环境变量：

```powershell
cd unified-testing
Copy-Item .env.example .env
# 然后编辑 .env，填入 GITEA_ADMIN_PASSWORD / GITEA_USER_PASSWORD 等
```

> `.env` 已被 `.gitignore` 忽略，不会提交。
> 未配置时，靶子仍可加载，仅「依赖账号的用例」会失败 —— 冒烟用例不受影响。

### 3. 运行测试

> ⚠️ **必须先清代理**，否则 Selenium 下载驱动会得到 `502 Bad Gateway`。

```powershell
Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY -ErrorAction SilentlyContinue
cd unified-testing
& "C:\Users\LX\miniconda3\python.exe" -m pytest ui/tests -v

# 或用统一入口（含 HTML 报告）
& "C:\Users\LX\miniconda3\python.exe" run.py ui
```

测试报告输出到 `reports/report_ui.html`。

## ⚠️ 已知环境坑（踩过，务必注意）

| # | 现象 | 原因 | 解决 |
| --- | --- | --- | --- |
| 1 | `WebDriverException: Bad Gateway` | 系统设了代理（`127.0.0.1:7897`），Selenium Manager 下载驱动被代理拦截 | 运行前清除 `HTTP_PROXY` / `HTTPS_PROXY` |
| 2 | `python` 命令无任何输出 | PATH 中的 `python` 是 Microsoft Store 占位程序 | 使用绝对路径 `C:\Users\LX\miniconda3\python.exe` |
| 3 | 找不到 selenium | 装到了其他 Python 环境 | 本机有 4 个 Python 环境，本项目固定用 miniconda base |

> 框架已在代码层处理第 1 条（`common/browser.py` 的 `clear_proxy_env()`），
> 但显式清除环境变量最稳妥。

## 设计原则

1. **靶子与框架分离** —— 靶子是外部系统，本项目不含其代码
2. **配置驱动** —— 新增靶子只改 `targets.yaml`，不改代码
3. **分层复用** —— 配置、浏览器、报告、缺陷集成由四类测试共用
4. **失败留痕** —— 失败自动截图并保留上下文，便于提单与定位
5. **只做合规的事** —— 安全测试采用被动扫描，不发起攻击性请求

## 测试范围说明

| 类型 | 状态 | 说明 |
| --- | --- | --- |
| UI 测试 | 🚧 进行中 | Selenium + Edge |
| 接口测试 | ⏳ 计划中 | requests + pytest |
| 性能测试 | ⏳ 计划中 | Locust |
| 安全测试 | ⏳ 计划中 | 被动扫描 |
| 不稳定用例检测 | ⏳ 计划中 | 重复执行统计成功率 |

## 与另一个项目的关系

本仓库内的 `../llm_regression/` 是**上一个项目**（AI 对话接口回归测试），使用独立的 `.venv` 环境（pytest 8.4.2）。

**两个项目环境隔离，不要混用解释器。**
