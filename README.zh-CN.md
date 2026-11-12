# Hermes Model Studio

[English](README.md) · [简体中文](README.zh-CN.md)

在 Hermes 官方 Dashboard 中管理自定义 API 服务和模型参数。

这是一个独立的社区插件，通过 `/model-studio` 页面提供配置界面，不替换官方 Web UI，也不修改 Hermes 核心文件。当前版本 **1.0.2** 已在 **Linux 上的 Hermes v0.23.1** 验证。

## 功能

- 在一个页面管理服务名称、Base URL、接口格式、密钥和备注。
- 支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 兼容接口。
- 获取模型列表，或手动添加模型 ID。
- 显示接口返回或本地 Hermes 模型目录匹配的能力信息；未知参数可以手动覆盖。
- 为每个模型设置上下文、最大输出、思考等级，以及受支持的 Anthropic 思考预算。
- 跟随官方 Web UI 顶部语言选择，支持中文、英文，其他语言回退到英文；繁体中文等中文变体暂用简体文案。
- 已保存的 API Key 不回显。编辑时留空保留原 Key，输入新值才替换。
- 通过明确的“设为全局默认”操作切换共享默认模型。

## 使用条件

- Hermes **v0.23.1 或更高版本**；已验证的版本为 v0.23.1，升级 Hermes 后可能需要适配。
- Linux 或具备相应功能的 POSIX 系统。后端目前使用 `fcntl` 和 `O_NOFOLLOW`，此版本不支持 Windows。
- 官方 Dashboard 必须启用登录认证，且运行在 `auth_required` 模式。仅为未启用认证的环回实例配置密码，并不足以启用此插件。
- 使用反向代理时，将 `dashboard.public_url` 设置为外部访问地址，例如 `https://dashboard.example.com`。
- API 必须使用公网 HTTPS、443 端口。插件获取列表和测试连接时拒绝内网、环回、云元数据地址、HTTP 和重定向。

Python 后端使用 Hermes 已有的运行环境和 FastAPI。仓库已包含 `dashboard/dist/`，安装使用不需要 Node.js 或手动编译。

## 安装

打开官方 Web UI 的“插件”页面，输入以下仓库链接，安装后启用插件：

```text
https://github.com/Ddd0213/hermes-model-studio
```

也可以使用命令行：

```bash
hermes plugins install https://github.com/Ddd0213/hermes-model-studio --enable
```

刷新 Dashboard 后，在侧栏打开 **Model Studio**。如果 Hermes 提示等待激活，请按提示重载或重启。

**私有仓库说明：** 仓库处于私有状态时，运行 Hermes 的机器必须具有该仓库的 Git 访问权限。在另一台电脑登录 GitHub，不会自动赋予服务器访问权限。不要在安装链接中嵌入 GitHub Token，也不要把 Token 放进项目。仓库公开后，其他人可以直接通过链接安装，无需私有仓库凭据。

通过 GitHub 链接安装不需要先加入官方插件目录。本项目不是 Hermes 官方产品，也不表示已获官方目录收录。

## 使用流程

1. 新建服务，填写名称、公网 HTTPS Base URL 和接口格式。
2. 如果上游要求认证，填写 API Key。
3. 点击“获取模型列表”并勾选，或直接填写模型 ID 添加。
4. 展开已添加的模型，查看能力来源，按需覆盖参数。
5. 选择此服务的默认模型，点击“保存服务”。
6. 需要改变 Hermes 共享默认路由时，再点击“设为全局默认”。

获取模型列表只发送列表请求，不发起推理。“测试连接”会先弹出确认，再发送一条 `Reply with OK.` 请求，输出上限 64 Token，不启用工具；上游可能对这次请求收费。

保存未启用的服务不会切换全局默认模型。编辑当前正在使用的服务，可能同步更新其路由连接或上下文设置。切换全局默认模型可能影响网页、官方手机端和其他渠道的新会话；已有会话可能继续保留原设置。

## 配置和密钥存储

插件通过 Hermes 原生配置及密钥管理方法写入数据：

- 服务和模型参数保存在当前默认配置的 `config.yaml`。
- 新密钥保存到 Hermes `.env`，使用专用的 `HERMES_CUSTOM_STUDIO_*_API_KEY` 环境变量，并由配置引用。这是服务器文件存储，不是独立的加密保险库。
- 插件接口不返回已保存的密钥，只返回 `has_key` 状态。正在输入的新 Key 可见，保存后输入框清空。
- 现有额外认证请求头保留在服务器。需要改变这类服务的地址或接口格式时，应新建服务。
- 插件不创建、删除或迁移聊天记录，也不修改手机连接和推送凭据。

后端检查登录认证、请求来源、配置版本、响应大小和操作频率；外部检查会验证全部 DNS 结果，并固定已验证的公网地址建立 TLS 连接。这些限制适用于插件自己的列表获取和连接测试，不替代 Hermes 聊天运行时的网络行为。

本仓库仅包含插件代码和构建文件，不包含 Hermes 用户目录、部署配置、API Key、登录会话或聊天数据库。

## 兼容性和限制

- 1.0.2 仅管理默认配置。
- 侧栏名称固定为 **Model Studio**，页面内容跟随官方语言选择。
- 模型列表最多显示 200 个。目录匹配不代表特定上游接口已验证支持相应能力。
- 插件通过官方 Dashboard 扩展和 `llm_request` 中间件入口加载；部分参数适配、配置验证使用 Hermes 内部方法，升级 Hermes 后需检查兼容性。
- 正在使用或被其他路由引用的服务不能删除。删除未使用的服务时，插件可能同时删除自己创建且不再被引用的专用密钥。

## 开发

只在开发副本中安装构建依赖并重新构建：

```bash
npm ci
npm run build
```

构建使用 esbuild 和官方插件 SDK 提供的 React，不重复打包 React。样式源文件为 `dashboard/src/style.css`。请一并提交生成的 `dashboard/dist/index.js` 和 `dashboard/dist/style.css`，保证使用者安装后无需编译。

在 Hermes 环境中运行官方插件校验：

```bash
hermes plugins validate . --json
```

1.0.2 发布准备包含官方插件校验、前端构建一致性检查，以及语言切换、未保存输入保留、弹窗翻译和响应式布局的浏览器验证。这些检查无需付费模型推理。

## 更新和卸载

```bash
hermes plugins update model-studio
hermes plugins remove model-studio
```

卸载插件不会自动清除服务配置和已保存密钥，如需清理请单独通过 Hermes 配置和密钥管理工具操作。

## 许可证

[MIT](LICENSE)，Copyright (c) 2026 Ddd0213。Hermes 是 Nous Research 的独立 MIT 许可项目，参见 [NOTICE](NOTICE)。
