# LLM Research and Experiment Platform

This project is a self-hosted environment for conducting structured studies with large language models. It supports
participant accounts, controlled research tasks, surveys, essays, chats, experiment telemetry, full-session exports,
and reproducible analysis without coupling the analysis tools to the running application.

It extends Open WebUI and can use either lightweight local models or larger cloud-backed models through Ollama. The
repository contains three deliberately separate parts:

- **Web application:** the participant and administrator interface.
- **Browser extension:** optional experiment telemetry for consenting participants.
- **Research toolbox:** Python utilities and a notebook for analyzing exported sessions.

The extension and toolbox remain standalone source components. Neither is copied into the application image.

## 1. Install Docker

Docker runs the web application and preserves its data in a named volume.

### Windows or macOS

1. Install [Docker Desktop](https://docs.docker.com/desktop/). It includes Docker Engine and Docker Compose.
2. On Windows, enable the WSL 2 backend and this distribution under Docker Desktop's WSL integration settings.
3. Start Docker Desktop and verify the installation:

   ```bash
   docker version
   docker compose version
   ```

### Ubuntu or Debian Linux

1. Follow Docker's official instructions to [configure its package repository](https://docs.docker.com/engine/install/ubuntu/).
2. Install Docker Engine and the Compose plugin:

   ```bash
   sudo apt update
   sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo docker run hello-world
   ```

For other Linux distributions, use the matching [Docker Engine installation guide](https://docs.docker.com/engine/install/).

## 2. Install Ollama and choose a model

Install Ollama on the same computer that will run Docker:

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

### Make Ollama reachable from Docker

The application connects to `http://host.docker.internal:11434`. Docker Desktop normally provides that hostname, and
the deployment file adds the equivalent host-gateway mapping on Linux. Ollama binds to `127.0.0.1` by default, so a
native Linux installation may also need this systemd override:

```ini
# sudo systemctl edit ollama.service
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Then restart and verify Ollama:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
curl http://localhost:11434/api/tags
```

Do not expose port `11434` to an untrusted network. To use another Ollama server, change `OLLAMA_BASE_URL` in
`.env.deploy`.

## 3. Start the application

Clone the project, authenticate to its private image registry with a token that has `read_registry`, and run setup:

```bash
git clone git@gitlab.uni-marburg.de:fb12/ag-becker/theses/practical_2026_llm-sensors.git
cd practical_2026_llm-sensors
docker login gitlab.uni-marburg.de:5050
./setup.sh
```

The setup script creates `.env.deploy` without overwriting an existing file, generates a persistent
`WEBUI_SECRET_KEY`, pulls the image, and starts the application at
[http://localhost:3000](http://localhost:3000). Application data is kept in the
`practical-2026-llm-sensors_open-webui-data` Docker volume.

If the image does not exist yet, a maintainer must complete the [first GitLab push](#image-publishing) and allow its
pipeline to finish.

## Common operations

```bash
# Pull the newest main-branch image and recreate the service
docker compose --env-file .env.deploy -f compose.deploy.yaml pull
docker compose --env-file .env.deploy -f compose.deploy.yaml up -d

# View status and logs
docker compose --env-file .env.deploy -f compose.deploy.yaml ps
docker compose --env-file .env.deploy -f compose.deploy.yaml logs -f open-webui

# Stop the service without deleting its data volume
docker compose --env-file .env.deploy -f compose.deploy.yaml down
```

Never run `docker compose down -v` unless the persistent Open WebUI database and uploaded data should be deleted.

## Browser extension (separate, unpacked)

The Chrome extension records full normal-window tab URLs, titles, and lifecycle events during an active experiment.
Review the privacy disclosure in
[`browser-extension/experiment-telemetry/README.md`](browser-extension/experiment-telemetry/README.md) before enabling
it. It remains disabled by default.

For the local Docker deployment, install Node.js 22 and build the fixed-origin extension directly from its source:

```bash
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=http://localhost:3000 \
  node browser-extension/experiment-telemetry/build.mjs
```

Then:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select `browser-extension/experiment-telemetry/dist`.
4. Set `EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true` in `.env.deploy`.
5. Restart with `docker compose --env-file .env.deploy -f compose.deploy.yaml up -d`.
6. Sign in as a non-admin participant and confirm that the experiment Start gate reports the extension as ready.

The unpacked workflow is intentionally limited to loopback deployments. A future HTTPS deployment must use the
Chrome Web Store identity and store URL described in the extension README.

## Research toolbox (separate Python environment)

The toolbox processes the admin dashboard's **Full research session JSON export**. It is not installed in the Docker
image and does not anonymize exported data.

```bash
python3 -m venv .venv-research
. .venv-research/bin/activate
python -m pip install --upgrade pip
python -m pip install -e './research-toolbox[notebook,test]'
jupyter lab research-toolbox/notebooks/full_session_analysis.ipynb
```

See [`research-toolbox/README.md`](research-toolbox/README.md) for the table model, analysis helpers, and privacy notes.
Python 3.11 or newer is required.

## Image publishing

The GitLab pipeline uses rootless BuildKit and only GitLab's predefined registry credentials. Every pipeline publishes
an immutable commit-SHA tag. Branches also receive their branch-slug tag, `main` publishes `latest`, and Git tags
publish a matching version tag. Build layers are cached in the project registry.

Before the first push, confirm under the GitLab project settings that the Container Registry is enabled and an active
runner is available. Then preserve the upstream GitHub remote and add this project as a separate remote:

```bash
git remote add gitlab git@gitlab.uni-marburg.de:fb12/ag-becker/theses/practical_2026_llm-sensors.git
git push -u gitlab main
```

If the build remains pending, the project has no eligible runner. If it fails with a rootless user-namespace or mount
permission error, ask the GitLab administrator to enable the system calls required by rootless BuildKit;
do not switch the project to privileged Docker-in-Docker merely to bypass that policy.

---

# Upstream Open WebUI README

# Open WebUI 👋

![GitHub stars](https://img.shields.io/github/stars/open-webui/open-webui?style=social)
![GitHub forks](https://img.shields.io/github/forks/open-webui/open-webui?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/open-webui/open-webui?style=social)
![GitHub repo size](https://img.shields.io/github/repo-size/open-webui/open-webui)
![GitHub language count](https://img.shields.io/github/languages/count/open-webui/open-webui)
![GitHub top language](https://img.shields.io/github/languages/top/open-webui/open-webui)
![GitHub last commit](https://img.shields.io/github/last-commit/open-webui/open-webui?color=red)
[![Discord](https://img.shields.io/badge/Discord-Open_WebUI-blue?logo=discord&logoColor=white)](https://discord.gg/5rJgQTnV4s)
[![](https://img.shields.io/static/v1?label=Sponsor&message=%E2%9D%A4&logo=GitHub&color=%23fe8e86)](https://github.com/sponsors/tjbck)

![Open WebUI Banner](./banner.png)

**Open WebUI is an [extensible](https://docs.openwebui.com/features/extensibility/plugin), feature-rich, and user-friendly self-hosted AI platform designed to operate entirely offline.** It supports various LLM runners like **Ollama** and **OpenAI-compatible APIs**, with **built-in inference engine** for RAG, making it a **powerful AI deployment solution**.

Passionate about open-source AI? [Join our team →](https://careers.openwebui.com/)

![Open WebUI Demo](./demo.png)

> [!TIP]  
> **Looking for an [Enterprise Plan](https://docs.openwebui.com/enterprise)?** – **[Speak with Our Sales Team Today!](https://docs.openwebui.com/enterprise)**
>
> Get **enhanced capabilities**, including **custom theming and branding**, **Service Level Agreement (SLA) support**, **Long-Term Support (LTS) versions**, and **more!**

For more information, be sure to check out our [Open WebUI Documentation](https://docs.openwebui.com/).

## Key Features of Open WebUI ⭐

- 🚀 **Effortless Setup**: Install seamlessly using Docker or Kubernetes (kubectl, kustomize or helm) for a hassle-free experience with support for both `:ollama` and `:cuda` tagged images.

- 🤝 **Ollama/OpenAI API Integration**: Effortlessly integrate OpenAI-compatible APIs for versatile conversations alongside Ollama models. Customize the OpenAI API URL to link with **LMStudio, GroqCloud, Mistral, OpenRouter, and more**.

- 🛡️ **Granular Permissions and User Groups**: By allowing administrators to create detailed user roles and permissions, we ensure a secure user environment. This granularity not only enhances security but also allows for customized user experiences, fostering a sense of ownership and responsibility amongst users.

- 📱 **Responsive Design**: Enjoy a seamless experience across Desktop PC, Laptop, and Mobile devices.

- 📱 **Progressive Web App (PWA) for Mobile**: Enjoy a native app-like experience on your mobile device with our PWA, providing offline access on localhost and a seamless user interface.

- ✒️🔢 **Full Markdown and LaTeX Support**: Elevate your LLM experience with comprehensive Markdown and LaTeX capabilities for enriched interaction.

- 🎤📹 **Hands-Free Voice/Video Call**: Experience seamless communication with integrated hands-free voice and video call features using multiple Speech-to-Text providers (Local Whisper, OpenAI, Deepgram, Azure) and Text-to-Speech engines (Azure, ElevenLabs, OpenAI, Transformers, WebAPI), allowing for dynamic and interactive chat environments.

- 🛠️ **Model Builder**: Easily create Ollama models via the Web UI. Create and add custom characters/agents, customize chat elements, and import models effortlessly through [Open WebUI Community](https://openwebui.com/) integration.

- 🐍 **Native Python Function Calling Tool**: Enhance your LLMs with built-in code editor support in the tools workspace. Bring Your Own Function (BYOF) by simply adding your pure Python functions, enabling seamless integration with LLMs.

- 💾 **Persistent Artifact Storage**: Built-in key-value storage API for artifacts, enabling features like journals, trackers, leaderboards, and collaborative tools with both personal and shared data scopes across sessions.

- 📚 **Local RAG Integration**: Dive into the future of chat interactions with groundbreaking Retrieval Augmented Generation (RAG) support using your choice of 9 vector databases and multiple content extraction engines (Tika, Docling, Document Intelligence, Mistral OCR, PaddleOCR-vl, External loaders). Load documents directly into chat or add files to your document library, effortlessly accessing them using the `#` command before a query.

- 🔍 **Web Search for RAG**: Perform web searches using 15+ providers including `SearXNG`, `Google PSE`, `Brave Search`, `Kagi`, `Mojeek`, `Tavily`, `Perplexity`, `serpstack`, `serper`, `Serply`, `DuckDuckGo`, `SearchApi`, `SerpApi`, `Bing`, `Jina`, `Exa`, `Sougou`, `Azure AI Search`, and `Ollama Cloud`, injecting results directly into your chat experience.

- 🌐 **Web Browsing Capability**: Seamlessly integrate websites into your chat experience using the `#` command followed by a URL. This feature allows you to incorporate web content directly into your conversations, enhancing the richness and depth of your interactions.

- 🎨 **Image Generation & Editing Integration**: Create and edit images using multiple engines including OpenAI's DALL-E, Gemini, ComfyUI (local), and AUTOMATIC1111 (local), with support for both generation and prompt-based editing workflows.

- ⚙️ **Many Models Conversations**: Effortlessly engage with various models simultaneously, harnessing their unique strengths for optimal responses. Enhance your experience by leveraging a diverse set of models in parallel.

- 🔐 **Role-Based Access Control (RBAC)**: Ensure secure access with restricted permissions; only authorized individuals can access your Ollama, and exclusive model creation/pulling rights are reserved for administrators.

- 🗄️ **Flexible Database & Storage Options**: Choose from SQLite (with optional encryption), PostgreSQL, or configure cloud storage backends (S3, Google Cloud Storage, Azure Blob Storage) for scalable deployments.

- 🔍 **Advanced Vector Database Support**: Select from 9 vector database options including ChromaDB, PGVector, Qdrant, Milvus, Elasticsearch, OpenSearch, Pinecone, S3Vector, and Oracle 23ai for optimal RAG performance.

- 🔐 **Enterprise Authentication**: Full support for LDAP/Active Directory integration, SCIM 2.0 automated provisioning, and SSO via trusted headers alongside OAuth providers. Enterprise-grade user and group provisioning through SCIM 2.0 protocol, enabling seamless integration with identity providers like Okta, Azure AD, and Google Workspace for automated user lifecycle management.

- ☁️ **Cloud-Native Integration**: Native support for Google Drive and OneDrive/SharePoint file picking, enabling seamless document import from enterprise cloud storage.

- 📊 **Production Observability**: Built-in OpenTelemetry support for traces, metrics, and logs, enabling comprehensive monitoring with your existing observability stack.

- ⚖️ **Horizontal Scalability**: Redis-backed session management and WebSocket support for multi-worker and multi-node deployments behind load balancers.

- 🌐🌍 **Multilingual Support**: Experience Open WebUI in your preferred language with our internationalization (i18n) support. Join us in expanding our supported languages! We're actively seeking contributors!

- 🧩 **Pipelines, Open WebUI Plugin Support**: Seamlessly integrate custom logic and Python libraries into Open WebUI using [Pipelines Plugin Framework](https://github.com/open-webui/pipelines). Launch your Pipelines instance, set the OpenAI URL to the Pipelines URL, and explore endless possibilities. [Examples](https://github.com/open-webui/pipelines/tree/main/examples) include **Function Calling**, User **Rate Limiting** to control access, **Usage Monitoring** with tools like Langfuse, **Live Translation with LibreTranslate** for multilingual support, **Toxic Message Filtering** and much more.

- 🌟 **Continuous Updates**: We are committed to improving Open WebUI with regular updates, fixes, and new features.

Want to learn more about Open WebUI's features? Check out our [Open WebUI documentation](https://docs.openwebui.com/features) for a comprehensive overview!

---

We are incredibly grateful for the generous support of our sponsors. Their contributions help us to maintain and improve our project, ensuring we can continue to deliver quality work to our community. Thank you!

## How to Install 🚀

### Installation via Python pip 🐍

Open WebUI can be installed using pip, the Python package installer. Before proceeding, ensure you're using **Python 3.11** to avoid compatibility issues.

1. **Install Open WebUI**:
   Open your terminal and run the following command to install Open WebUI:

   ```bash
   pip install open-webui
   ```

2. **Running Open WebUI**:
   After installation, you can start Open WebUI by executing:

   ```bash
   open-webui serve
   ```

This will start the Open WebUI server, which you can access at [http://localhost:8080](http://localhost:8080)

### Quick Start with Docker 🐳

> [!NOTE]  
> Please note that for certain Docker environments, additional configurations might be needed. If you encounter any connection issues, our detailed guide on [Open WebUI Documentation](https://docs.openwebui.com/) is ready to assist you.

> [!WARNING]
> When using Docker to install Open WebUI, make sure to include the `-v open-webui:/app/backend/data` in your Docker command. This step is crucial as it ensures your database is properly mounted and prevents any loss of data.

> [!TIP]  
> If you wish to utilize Open WebUI with Ollama included or CUDA acceleration, we recommend utilizing our official images tagged with either `:cuda` or `:ollama`. To enable CUDA, you must install the [Nvidia CUDA container toolkit](https://docs.nvidia.com/dgx/nvidia-container-runtime-upgrade/) on your Linux/WSL system.

### Installation with Default Configuration

- **If Ollama is on your computer**, use this command:

  ```bash
  docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

- **If Ollama is on a Different Server**, use this command:

  To connect to Ollama on another server, change the `OLLAMA_BASE_URL` to the server's URL:

  ```bash
  docker run -d -p 3000:8080 -e OLLAMA_BASE_URL=https://example.com -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

- **To run Open WebUI with Nvidia GPU support**, use this command:

  ```bash
  docker run -d -p 3000:8080 --gpus all --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:cuda
  ```

### Installation for OpenAI API Usage Only

- **If you're only using OpenAI API**, use this command:

  ```bash
  docker run -d -p 3000:8080 -e OPENAI_API_KEY=your_secret_key -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main
  ```

### Installing Open WebUI with Bundled Ollama Support

This installation method uses a single container image that bundles Open WebUI with Ollama, allowing for a streamlined setup via a single command. Choose the appropriate command based on your hardware setup:

- **With GPU Support**:
  Utilize GPU resources by running the following command:

  ```bash
  docker run -d -p 3000:8080 --gpus=all -v ollama:/root/.ollama -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:ollama
  ```

- **For CPU Only**:
  If you're not using a GPU, use this command instead:

  ```bash
  docker run -d -p 3000:8080 -v ollama:/root/.ollama -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:ollama
  ```

Both commands facilitate a built-in, hassle-free installation of both Open WebUI and Ollama, ensuring that you can get everything up and running swiftly.

After installation, you can access Open WebUI at [http://localhost:3000](http://localhost:3000). Enjoy! 😄

### Other Installation Methods

We offer various installation alternatives, including non-Docker native installation methods, Docker Compose, Kustomize, and Helm. Visit our [Open WebUI Documentation](https://docs.openwebui.com/getting-started/) or join our [Discord community](https://discord.gg/5rJgQTnV4s) for comprehensive guidance.

### Troubleshooting

Encountering connection issues? Our [Open WebUI Documentation](https://docs.openwebui.com/troubleshooting/) has got you covered. For further assistance and to join our vibrant community, visit the [Open WebUI Discord](https://discord.gg/5rJgQTnV4s).

#### Open WebUI: Server Connection Error

If you're experiencing connection issues, it’s often due to the WebUI docker container not being able to reach the Ollama server at 127.0.0.1:11434 (host.docker.internal:11434) inside the container . Use the `--network=host` flag in your docker command to resolve this. Note that the port changes from 3000 to 8080, resulting in the link: `http://localhost:8080`.

**Example Docker Command**:

```bash
docker run -d --network=host -v open-webui:/app/backend/data -e OLLAMA_BASE_URL=http://127.0.0.1:11434 --name open-webui --restart always ghcr.io/open-webui/open-webui:main
```

### Keeping Your Docker Installation Up-to-Date

Check our Updating Guide available in our [Open WebUI Documentation](https://docs.openwebui.com/getting-started/updating).

### Using the Dev Branch 🌙

> [!WARNING]
> The `:dev` branch contains the latest unstable features and changes. Use it at your own risk as it may have bugs or incomplete features.

If you want to try out the latest bleeding-edge features and are okay with occasional instability, you can use the `:dev` tag like this:

```bash
docker run -d -p 3000:8080 -v open-webui:/app/backend/data --name open-webui --add-host=host.docker.internal:host-gateway --restart always ghcr.io/open-webui/open-webui:dev
```

### Offline Mode

If you are running Open WebUI in an offline environment, you can set the `HF_HUB_OFFLINE` environment variable to `1` to prevent attempts to download models from the internet.

```bash
export HF_HUB_OFFLINE=1
```

## What's Next? 🌟

Discover upcoming features on our roadmap in the [Open WebUI Documentation](https://docs.openwebui.com/roadmap/).

## License 📜

This project contains code under multiple licenses. The current codebase includes components licensed under the Open WebUI License with an additional requirement to preserve the "Open WebUI" branding, as well as prior contributions under their respective original licenses. For a detailed record of license changes and the applicable terms for each section of the code, please refer to [LICENSE_HISTORY](./LICENSE_HISTORY). For complete and updated licensing details, please see the [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY) files.

## Support 💬

If you have any questions, suggestions, or need assistance, please open an issue or join our
[Open WebUI Discord community](https://discord.gg/5rJgQTnV4s) to connect with us! 🤝

## Star History

<a href="https://star-history.com/#open-webui/open-webui&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=open-webui/open-webui&type=Date" />
  </picture>
</a>

---

Created by [Timothy Jaeryang Baek](https://github.com/tjbck) - Let's make Open WebUI even more amazing together! 💪
