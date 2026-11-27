# 安装与数据位置

## 装在哪

把 `jazl` 整个文件夹解压到**任意可写目录**都行（下载目录、D 盘、`%LOCALAPPDATA%\Programs` 都可以）：

- 必须**整包解压**：`jazl.exe` 和 `_internal` 要在一起，单拿 exe 会报 `no Qt platform plugin`；
- 不要只解压 exe；
- 路径建议短一点（`D:\jazl`），别套太深，PyInstaller 的 `_internal` 目录本身就很深。

**当前这台机器**的部署位置是 `D:\jazl`（`build.bat` 默认就复制到这里，可用环境变量 `JAZL_DEPLOY` 改）。

## 数据放在哪

`config.json` / `roles.json` / `contacts.json` **不在 exe 旁边**，统一放在：

```
%LOCALAPPDATA%\jazl\
```

好处：

- 程序放哪都能跑（Program Files、只读目录也行，因为不往那儿写）；
- 升级只换 exe，档案和设置不丢；
- 想换位置：设环境变量 `JAZL_HOME` 指到别的目录。

**首次启动会自动迁移**：如果 exe 旁边还有老的 `config.json` / `roles.json` / `contacts.json`，会**复制**一份到新目录（源文件保留，不删）。设置页里有「数据目录」一栏，能看到实际路径并一键打开。

## 两个 key

设置页里填：

| key | 用途 |
|---|---|
| `JEV_API_KEY` | 判断/排序（TypeSafe 直连或 OpenRouter） |
| `LLM_API_KEY` | 起草（DeepSeek / OpenAI 兼容 / Anthropic / Gemini） |

key 只存在 Windows 用户环境变量里，不进任何文件。

## 网络

程序启动时会用一次 CONNECT 探测常见的本地代理端口（7897、7890…），探到就把**本进程**的 `HTTP(S)_PROXY` 指向它；探不到就直连（TUN 模式下多数站点可用）。

- 不读 Windows 系统代理（Python 不认），也不改注册表；
- 想把代理固定下来：直接给进程设 `HTTPS_PROXY` 环境变量，程序会尊重它。

## 权限（填入不动的情况）

「填入」要求本程序的权限**不低于**聊天软件：

- 两边都用普通方式启动 → 正常；
- 聊天软件是「以管理员身份运行」（HIGH），本程序也得用管理员启动，否则光标移不过去（程序会明确报错）。
