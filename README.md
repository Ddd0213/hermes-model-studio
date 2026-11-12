# Hermes Model Studio

[English](README.md) · [简体中文](README.zh-CN.md)

Manage custom API providers and model settings inside the official Hermes Dashboard.

Model Studio is an independent community plugin. It adds a `/model-studio` page without replacing the official Web UI or modifying Hermes core files. Version **1.0.2** has been tested on **Hermes v0.23.1 on Linux**.

## Features

- Manage provider names, Base URLs, API formats, keys, and notes in one page.
- Support OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages compatible APIs.
- Fetch model lists or add model IDs manually.
- Show model capabilities reported by the endpoint or matched from the local Hermes model catalog. Unknown capabilities remain editable.
- Override context windows, output limits, reasoning effort, and supported Anthropic thinking budgets per model.
- Follow the official Dashboard language selector: Chinese and English, with English fallback for other languages. Chinese locale variants currently use Simplified Chinese.
- Keep saved API keys hidden. Leave the key field blank while editing to keep an existing key; enter a new value to replace it.
- Change the shared default model only through the explicit **Set global default** action.

## Requirements

- Hermes **v0.23.1 or later**; v0.23.1 is the tested version. Later releases may need compatibility updates.
- A Linux or other compatible POSIX host. The current backend uses `fcntl` and `O_NOFOLLOW`; Windows is not supported by this release.
- The official Dashboard with login authentication enabled. The plugin intentionally refuses unauthenticated Dashboard mode.
- The Dashboard must run with `auth_required` enabled. A password configured for an otherwise unauthenticated loopback instance is not sufficient.
- Behind a reverse proxy, set `dashboard.public_url` to the external Dashboard origin, for example `https://dashboard.example.com`.
- Public HTTPS API endpoints on port 443. Private networks, localhost, cloud metadata endpoints, HTTP endpoints, and redirects are rejected by the plugin's discovery and connection checks.

The Python backend uses the existing Hermes runtime and its FastAPI installation. End users do not need Node.js: the repository includes the frontend build in `dashboard/dist/`.

## Installation

In the official Web UI, open **Plugins**, enter this repository URL, and enable the plugin after installation:

```text
https://github.com/Ddd0213/hermes-model-studio
```

Or use the CLI:

```bash
hermes plugins install https://github.com/Ddd0213/hermes-model-studio --enable
```

Refresh the Dashboard and open **Model Studio** in the sidebar. If Hermes reports a pending activation, follow its reload or restart instruction.

**Private repository access:** while this repository is private, the machine running Hermes needs Git credentials authorized to read it. Being signed in to GitHub on another computer does not grant the Hermes server access. Do not paste GitHub tokens into the repository URL or store them in this project. A public repository can be installed by URL without private-repository credentials.

Installing this GitHub repository does not require registration in the official plugin catalog. This plugin is not an official Hermes product or a claim of catalog approval.

## Usage

1. Add a provider and enter its name, public HTTPS Base URL, and API format.
2. Enter an API key if the endpoint requires authentication.
3. Click **Fetch models**, select models, or add model IDs manually.
4. Expand an added model to view capability sources and override its parameters.
5. Select the provider's default model and click **Save provider**.
6. Use **Set global default** only when you intend to change the shared Hermes route.

Fetching a model list sends a model-list request, not inference. **Test connection** asks for confirmation before sending one `Reply with OK.` request with a 64-token output limit and no tools. The upstream provider may charge for that request.

Saving an inactive provider does not switch the global default. Editing an active provider can update its active route's connection or context mirror. Switching the shared default may affect new conversations in the Web UI, the official mobile app, and other channels. Existing conversations may retain their original settings.

## Configuration and key storage

The plugin uses Hermes' native configuration and secret lifecycle:

- Provider and per-model settings are written to the current default Hermes profile's `config.yaml`.
- New keys are stored in the Hermes `.env` file under dedicated `HERMES_CUSTOM_STUDIO_*_API_KEY` names, referenced from configuration. This is server-side file storage, not a separate encrypted vault.
- Saved keys are not returned by the plugin API. The browser receives only a `has_key` state. Newly typed keys are visible while entering them and cleared after saving.
- Existing extra authentication headers remain on the server. Changing the URL or API format of such a provider requires creating a new provider.
- The plugin does not create, delete, or migrate chat history or mobile/push credentials.

The backend checks authentication, request origins, configuration revisions, response sizes, and action rate limits. Its outbound checks validate every DNS result and pin the checked public address for TLS connections. This does not replace Hermes' own network behavior when conversations invoke models.

This repository contains plugin code and build files only. It does not include a Hermes home directory, deployment configuration, API keys, authentication sessions, or chat databases.

## Compatibility and limitations

- Version 1.0.2 manages the default profile only.
- The sidebar label is fixed as **Model Studio**; page content follows the official language selector.
- Model discovery is limited to 200 entries. Catalog matches describe known model capabilities and do not prove that a particular upstream endpoint supports them.
- The plugin uses the official Dashboard extension and `llm_request` middleware entry points. Some parameter mapping and native configuration validation use Hermes internal helpers, so compatibility should be checked after upgrading Hermes.
- A provider that is active or still referenced by another route cannot be deleted. Deleting an unused provider may also remove an unreferenced key created by this plugin.

## Development

Install build dependencies in a development checkout, then rebuild the included frontend bundle:

```bash
npm ci
npm run build
```

The build uses esbuild with the React instance supplied by the official plugin SDK; React is not bundled a second time. `dashboard/src/style.css` is the editable stylesheet source. Commit the generated `dashboard/dist/index.js` and `dashboard/dist/style.css` so plugin installations do not require a build step.

Validate the plugin in a Hermes environment:

```bash
hermes plugins validate . --json
```

Release preparation for 1.0.2 included the official Hermes plugin validator, a reproducible frontend build check, and browser checks covering language switching, unsaved input retention, translated dialogs, and responsive layouts. No paid model inference was needed for those checks.

## Updating and uninstalling

```bash
hermes plugins update model-studio
hermes plugins remove model-studio
```

Removing the plugin does not automatically erase provider settings or stored keys. Review those separately through Hermes configuration and key-management tools.

## License

[MIT](LICENSE), Copyright (c) 2026 Ddd0213. Hermes is a separate MIT-licensed project by Nous Research. See [NOTICE](NOTICE).
