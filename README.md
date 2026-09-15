# LLM Research and Experiment Platform

This project is a self-hosted environment for conducting structured studies with large language models. It supports
participant accounts, controlled research tasks, surveys, essays, chats, experiment telemetry, full-session exports,
and reproducible analysis without coupling the analysis tools to the running application.

It extends Open WebUI and can use either lightweight local models or larger cloud-backed models through Ollama. The
repository contains three deliberately separate parts:

- **Web application:** the participant and administrator interface.
- **Browser extension:** optional experiment telemetry for consenting participants.
- **Research toolbox:** Python utilities and a notebook for analyzing exported sessions.

The extension and toolbox are standalone source components with their own setup instructions. You can run the web
application from the published Docker image or start the frontend and backend directly for development.

## Table of Contents
- [Run the published Docker image](#run-the-published-docker-image)
- [Development quick start](#development-quick-start)
- [Browser extension (separate, unpacked)](#browser-extension-separate-unpacked)
- [Research toolbox (separate Python environment)](#research-toolbox-separate-python-environment)
- [Admin Panel Guide](#admin-panel-guide)
- [Upstream Open WebUI](#upstream-open-webui)

## Run the published Docker image

Install [Docker Desktop](https://docs.docker.com/desktop/) on Windows or macOS, or
[Docker Engine](https://docs.docker.com/engine/install/) on Linux. Start Ollama, then download the latest image:

```bash
docker pull justfarbod/llm-sensors
```

Run the application with persistent data and connect it to Ollama on the host:

```bash
docker run -d \
  --name llm-sensors \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -v llm-sensors-data:/app/backend/data \
  --restart unless-stopped \
  justfarbod/llm-sensors:latest
```

Open [http://localhost:3000](http://localhost:3000). The first registered account becomes the administrator, and
application data is retained in the `llm-sensors-data` Docker volume.

On Linux, Ollama may need to listen beyond the loopback interface so the container can reach it. Start it with:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Pull a small model in another terminal if you have not already installed one:

```bash
ollama pull llama3.2:1b
```

Useful container commands:

```bash
docker logs -f llm-sensors
docker stop llm-sensors
docker start llm-sensors
```

To update later, pull the image again, replace the container with the same `docker run` command, and keep the
`llm-sensors-data` volume. The browser extension and research toolbox are installed separately using the sections
below.

## Development quick start

The application runs directly from source with the SvelteKit frontend and FastAPI backend in two terminals. Both
processes use hot reload, so frontend and backend changes appear without rebuilding a container.

This workflow follows the upstream
[Open WebUI development guide](https://docs.openwebui.com/getting-started/advanced-topics/development/).

### 1. Install prerequisites

- Git
- Node.js 22.10 or newer within the supported Node 22 release line, with npm
- Python 3.11 (recommended) or Python 3.12
- Ollama, or another compatible model server

On Windows, WSL 2 is recommended. Run the frontend, backend, Python environment, and Ollama in environments that can
reach one another. The steps below use the local Node.js and Python development toolchains.

Clone the project:

```bash
git clone git@gitlab.uni-marburg.de:fb12/ag-becker/theses/practical_2026_llm-sensors.git
cd practical_2026_llm-sensors
```

### 2. Install Ollama and choose a model

Install Ollama on the same computer or development environment as the backend:

- **Windows:** use `OllamaSetup.exe` from the [Ollama download page](https://ollama.com/download/windows).
- **macOS:** install the application from the [Ollama download page](https://ollama.com/download/mac).
- **Linux:** run the official installer:

  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```

Verify that its local API is running:

```bash
ollama --version
curl http://localhost:11434/api/tags
```

Choose one of these starting models:

```bash
# Small local model (about 1B parameters; suitable for modest hardware)
ollama run llama3.2:1b

# Larger cloud model (requires an Ollama account and sends prompts to Ollama Cloud)
ollama signin
ollama run gpt-oss:120b-cloud
```

The small model is convenient for testing but has limited answer quality. Before using a cloud model with research
data, confirm that external processing is allowed by the study's consent and data-handling policy and review the
[current Ollama pricing](https://ollama.com/pricing).

After the application starts, sign in and select the downloaded local or cloud model from the model menu at the top
of a new chat. Models exposed by the configured Ollama server appear there automatically.

The default `.env.example` points the backend to `http://localhost:11434`. To use another compatible server, change
`OLLAMA_BASE_URL` after copying the environment file in the next step.

### 3. Start the frontend

In the first terminal, from the repository root:

```bash
[ -f .env ] || cp .env.example .env
npm install
npm run dev
```

The Vite development server runs at [http://localhost:5173](http://localhost:5173) and reloads frontend changes. On
later runs, only `npm run dev` is required.

### Frontend build profiles

- **Research (default):** `npm run dev` or `npm run build` provides the focused experiment interface with a smaller
  frontend and lower build resource requirements.
- **Full:** `npm run dev:full` or `npm run build:full` provides the complete Open WebUI interface and its optional
  frontend features.

Both production commands write the deployable application to `build`. See
[frontend profiles](docs/FRONTEND_PROFILES.md) for the complete feature scope, validation commands, and switching
guidance.

If dependency compatibility warnings prevent installation, use `npm install --force`. If Node reports a heap-limit
error, set `NODE_OPTIONS=--max-old-space-size=4096` before running the frontend command.

### 4. Start the backend

In a second terminal, from the repository root:

```bash
cd backend
python3.11 -m venv venv
. venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -U
sh dev.sh
```

If `python3.11` is not the executable name on your system, use a Python 3.11 or 3.12 executable. On Windows without
WSL, activate the environment with `venv\Scripts\activate`.

The backend runs with Uvicorn reload at [http://localhost:8080](http://localhost:8080), and its API documentation is
available at [http://localhost:8080/docs](http://localhost:8080/docs). Refresh the frontend after both terminals are
running. The first registered account becomes the administrator.

On later runs, restart the backend with:

```bash
cd backend
. venv/bin/activate
sh dev.sh
```

Use `Ctrl+C` in each terminal to stop the development servers. Local application state is stored under
`backend/data`; do not share this directory with a production installation.

## Browser extension (separate, unpacked)

The Chrome extension records full normal-window tab URLs, titles, and lifecycle events during an active experiment.
Review the privacy disclosure in
[`browser-extension/experiment-telemetry/README.md`](browser-extension/experiment-telemetry/README.md) before enabling
it. It remains disabled by default.

From the repository root, build the extension for the frontend's exact local origin:

```bash
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=http://localhost:5173 npm run build:experiment-extension
```

Then:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select `browser-extension/experiment-telemetry/dist`.
4. Set `EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true` in the root `.env` file.
5. Restart the backend with `cd backend && sh dev.sh`, and run the frontend with `npm run dev` from the repository
   root.
6. Sign in as a non-admin participant and confirm that the experiment Start gate reports the extension as ready.

The Vite development server proxies the extension's same-origin `/api` calls to the backend on port 8080. The unpacked
workflow is intentionally limited to loopback development; a future HTTPS deployment must use the Chrome Web Store
identity and store URL described in the extension README.

## Research toolbox (separate Python environment)

The toolbox processes the admin dashboard's **Full research session JSON export**. It runs independently from the web
application and does not anonymize exported data.

```bash
python3 -m venv .venv-research
. .venv-research/bin/activate
python -m pip install --upgrade pip
python -m pip install -e './research-toolbox[notebook,test]'
jupyter lab research-toolbox/notebooks/full_session_analysis.ipynb
```

See [`research-toolbox/README.md`](research-toolbox/README.md) for the table model, analysis helpers, and privacy notes.
Python 3.11 or newer is required.


---

## Admin Panel Guide

For a complete guide on how to use the Admin Panel to manage studies, workflows, and participants, please refer to the [Admin Panel Guide](ADMIN_PANEL_GUIDE.md).

## Upstream Open WebUI

This project is a fork of Open WebUI. For information about the base platform, features, and community, please visit the [official Open-WebUI repository](https://github.com/open-webui/open-webui).
