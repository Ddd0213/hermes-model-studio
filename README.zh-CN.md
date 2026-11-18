# Hermes Model Studio

[English](README.md) · [简体中文](README.zh-CN.md)

在 Hermes 官方 Dashboard 中管理自定义 API 服务和模型参数。

这是一个独立的社区插件，通过 `/model-studio` 页面提供配置界面，不替换官方 Web UI，也不修改 Hermes 核心文件。当前版本 **1.1.0** 已在 **Linux 上的 Hermes v0.23.1** 验证。

## 功能

- 在一个页面管理服务名称、Base URL、接口格式、密钥和备注。
- 支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 兼容接口。
- 获取模型列表，或手动添加模型 ID。
- 显示接口返回或本地 Hermes 模型目录匹配的能力信息；未知参数可以手动覆盖。
- 为每个模型设置上下文、最大输出、思考等级，以及受支持的 Anthropic 思考预算。
- 每个模型的图片识别、工具调用和思考能力均可选择“自动”“支持”或“不支持”。
- 同一 Hermes 安装中的所有 Bot 共享自定义供应商、模型和密钥，以后新建的 Bot 也会自动同步；更换密钥、修改模型同样会同步。
- 跟随官方 Web UI 顶部语言选择，支持中文、英文，其他语言回退到英文；繁体中文等中文变体暂用简体文案。
- 已保存的 API Key 不回显。编辑时留空保留原 Key，输入新值才替换。
- 各 Bot 保留仍然有效的默认模型；“设为 Hermes 默认”只切换主 Hermes Bot 的默认模型。

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

请在主 Hermes 用户目录安装并启用插件，再从主 Dashboard 打开 **Model Studio**。插件会自动为其他 Bot 配置启用共享运行逻辑。如果 Hermes 提示等待激活，请按提示重载或重启。

通过 GitHub 链接安装不需要先加入官方插件目录。本项目不是 Hermes 官方产品，也不表示已获官方目录收录。

## 使用流程

1. 新建服务，填写名称、公网 HTTPS Base URL 和接口格式。
2. 如果上游要求认证，填写 API Key。
3. 点击“获取模型列表”并勾选，或直接填写模型 ID 添加。
4. 展开已添加的模型，查看能力来源，设置图片识别、工具调用、思考能力及其他模型参数。
5. 选择此服务的默认模型，点击“保存服务”。
6. 保存后服务会同步给所有 Bot；需要改变主 Hermes Bot 的默认模型时，点击“设为 Hermes 默认”。

获取模型列表只发送列表请求，不发起推理。“测试连接”会先弹出确认，再发送一条 `Reply with OK.` 请求，输出上限 64 Token，不启用工具；上游可能对这次请求收费。

保存服务会保留各 Bot 当前选择的模型。若所选模型已被移除，则自动改用该服务的默认模型。供应商、密钥和模型参数的修改在后续对话轮次生效，正在生成的回复会继续完成。在已有会话中切换供应商，下一轮使用新选择的供应商。

能力设置用于声明上游模型实际支持的功能，不能让模型获得原本没有的能力。工具调用设为“不支持”时，会移除请求中的工具声明并阻止工具执行；思考能力设为“不支持”时，会省略思考相关字段。同步时，插件会为现有和以后新建的 Bot 设置 `agent.image_input_mode: native`，并将 `vision` 加入 `agent.disabled_toolsets`。图片请求的准备和上游拒收处理仍由 Hermes 负责。

## 配置和密钥存储

插件通过 Hermes 原生配置及密钥管理方法写入数据：

- 主 Hermes 用户目录的 `config.yaml` 是共享自定义供应商和模型参数的来源，插件会同步到现有及以后新建的 Bot 配置，包括 Hermes 原生模型能力覆盖设置。
- 新密钥保存到 Hermes `.env`，使用专用的 `HERMES_CUSTOM_STUDIO_*_API_KEY` 环境变量，并由配置引用。供应商需要的凭据会同步到各 Bot 的 `.env`，不分发无关密钥。这是服务器文件存储，不是独立的加密保险库。
- 插件接口不返回已保存的密钥，只返回 `has_key` 状态。正在输入的新 Key 可见，保存后输入框清空。
- 现有额外认证请求头保留在服务器。需要改变这类服务的地址或接口格式时，应新建服务。
- 插件不创建、删除或迁移聊天记录，也不修改手机连接和推送凭据。
- 各 Bot 保留自己的记忆和其余工具设置。主插件运行期间每三秒检查一次变化；从 Dashboard 保存时立即同步。

后端检查登录认证、请求来源、配置版本、响应大小和操作频率；外部检查会验证全部 DNS 结果，并固定已验证的公网地址建立 TLS 连接。这些限制适用于插件自己的列表获取和连接测试，不替代 Hermes 聊天运行时的网络行为。

本仓库仅包含插件代码和构建文件，不包含 Hermes 用户目录、部署配置、API Key、登录会话或聊天数据库。

## 兼容性和限制

- 1.1.0 在同一 Hermes 用户目录下的所有 Bot 间共享受支持的自定义供应商，暂不支持排除某个 Bot。内置供应商和使用 `key_cmd` 的供应商不在此共享目录内。
- 如果某个 Bot 存在相同供应商 ID 但地址不同，或安装了独立的 Model Studio 副本，需先解决冲突再同步；插件会报告冲突，不直接覆盖。
- 侧栏名称固定为 **Model Studio**，页面内容跟随官方语言选择。
- 模型列表最多显示 200 个。如果供应商已配置超过 200 个模型，页面会设为只读，防止保存时丢掉未显示的模型。目录匹配不代表特定上游接口已验证支持相应能力。
- 插件通过官方 Dashboard 扩展、`llm_request` 中间件和 `pre_llm_call` / `pre_tool_call` 钩子加载；部分参数适配、运行中客户端更新和配置验证使用 Hermes 内部方法，升级 Hermes 后需检查兼容性。
- 删除前会检查各 Bot 的主模型、辅助模型、备用模型和子代理路由。插件生成的能力记录不会单独阻止删除。使用 `model.aliases` 简写别名时，请先核对它是否仍引用该供应商。删除未使用的服务时，插件可能同时删除自己创建且不再被引用的专用密钥。

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

1.1.0 已完成官方插件校验、现有及新建 Bot 的同步检查、密钥和模型变更及回滚检查，以及三种接口格式的 Hermes 原生客户端更新检查。客户端检查未发送模型推理请求。

## 更新和卸载

```bash
hermes plugins update model-studio
hermes plugins remove model-studio
```

卸载插件不会自动清除服务配置和已保存密钥，如需清理请单独通过 Hermes 配置和密钥管理工具操作。

## 许可证

[MIT](LICENSE)，Copyright (c) 2026 Ddd0213。Hermes 是 Nous Research 的独立 MIT 许可项目，参见 [NOTICE](NOTICE)。
