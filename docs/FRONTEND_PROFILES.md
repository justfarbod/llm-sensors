# Frontend profiles

See [the validation record](FRONTEND_PROFILE_VALIDATION.md) for measured memory,
completed checks and the full-build checks still awaiting more memory.

Local development and production builds default to the research profile:

| Command                                    | Profile                           |
| ------------------------------------------ | --------------------------------- |
| `npm run dev` / `npm run dev:research`     | Research development              |
| `npm run dev:5050`                         | Research development on port 5050 |
| `npm run build` / `npm run build:research` | Research production               |
| `npm run build:watch`                      | Research production, watch mode   |
| `npm run dev:full`                         | Full development                  |
| `npm run build:full`                       | Full production                   |

Arguments still pass through, for example `npm run dev -- --port 5174` or
`npm run build:full -- --watch`. The existing Vite API/WebSocket proxies and
origin behavior are unchanged. Both production profiles emit the deployable app
to `build`. The launcher clears that generated output before a production build.
Do not run two profiles/builds in the same checkout concurrently.

Stop the current development/watch server before switching profiles. Restart with
`dev:full` or rebuild with `build:full` to restore the optional features. No package
installation, source restoration, database migration or settings reset is required.
Full commands retain the existing Pyodide preparation, which may require network
access on the first run. Research commands skip that preparation entirely.

## Scope

The research frontend excludes voice input/playback/calls and local speech models,
Python/code-interpreter execution, terminals, conversation graphs, and notes,
channels, calendar, automations and playground pages. Saved preferences for these
features are retained but ignored. Old page URLs redirect admins to the research
dashboard and other signed-in users to `/`; authentication remains handled by the
existing app layout.

The research/admin routes, advanced admin navigation, ordinary model connections
and tools, attachments, document search, image generation, Markdown, syntax
highlighting, code editing/copying, diagrams and HTML artifacts remain available.
Admin Python formatting continues to use the server endpoint. Browser Python
formatting is disabled with browser execution. These profiles are frontend build
boundaries, not backend authorization controls.

## Source and generated files

Edit **`src/routes`**, the canonical route source for both profiles. Research
commands mirror it into `.generated/research/routes`, replace only the five
excluded page trees with compatibility redirects, and synchronize changes during
development. Do not edit the generated copies.

`.generated/research/static` mirrors public assets at their existing URLs except
`static/pyodide`. Research builds also skip ONNX runtime copying. Optional source
files and installed dependencies remain untouched. The profile resolver substitutes
small disabled components/modules at optional feature boundaries, including worker
imports. Vite caches are separated into `node_modules/.vite-research` and
`node_modules/.vite-full`. `.generated` is ignored by Git.

`src/lib/features/definition.js` defines the shared profiles and request guards.
`scripts/frontend-profile.mjs` implements route/static synchronization, module
substitution and build graph checks. `FRONTEND_PROFILE` accepts only `research` or
`full` when invoking Vite directly; npm commands select it explicitly.

## Validation and limited-memory machines

Run `npm run test:profiles` for profile/synchronization/request-guard tests.
Run `npm run test:profiles:dev` with other development servers stopped to check
alternate-port forwarding and live canonical route additions, edits and deletions.
The test uses port 15173 (override with `PROFILE_TEST_PORT`) and removes its
temporary route and server when finished.
`npm run test:profiles:switch` checks full → research → full route/asset selection,
separate caches and optional import restoration using Vite's resolver. It does not
replace building and testing both production profiles.
Research compilation fails if an excluded implementation or dependency enters the
client, server or worker graph. Module reports are written under
`.generated/research/audit` for inspection, outside the deployable app.
After a successful research build, `npm run validate:research` checks those reports
and the deployable output for excluded workers and runtime assets.

Run builds and browser/backend tests sequentially on limited-memory machines. Stop
development servers and test backends before benchmarking. Use identical Node
settings for both profiles; a Node heap limit is not a cap on total process or WSL
memory. Measure Pyodide preparation separately from Vite compilation and preserve
logs in the workspace. Do not increase the heap limit to a value that leaves no
memory for WSL, the editor and other processes.

On a systemd-enabled WSL installation, an optional hard limit can contain the
research build (adjust it to the memory actually available):

```sh
systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=512M \
  env NODE_OPTIONS=--max-old-space-size=3328 npm run build
```

This can stop the build if it exceeds the limit; it does not guarantee a successful
build. Leave memory for other WSL processes and sufficient free space on the Windows
drive holding the WSL disks and swap. A full Windows drive can cause swap I/O errors
even when Linux reports free swap or filesystem space. These limits are an optional
local safeguard, not a change to the bundler configuration.

## Browser regression fixture

The research browser specs use test accounts and must run against an isolated
database. Build first, then start test services; stop those services before another
build. The optional streaming test uses a deterministic local model:

```sh
python3 scripts/research-test-model.py
```

In another terminal, with backend dependencies already installed:

```sh
mkdir -p /tmp/open-webui-profile-tests/static
env DATA_DIR=/tmp/open-webui-profile-tests \
  STATIC_DIR=/tmp/open-webui-profile-tests/static \
  DATABASE_URL=sqlite:////tmp/open-webui-profile-tests/test.db \
  WEBUI_SECRET_KEY=isolated-profile-test-secret ENABLE_PERSISTENT_CONFIG=false \
  ENABLE_OPENAI_API=true OPENAI_API_BASE_URLS=http://127.0.0.1:18081/v1 \
  OPENAI_API_KEYS=fixture-key ENABLE_OLLAMA_API=false \
  ENABLE_SIGNUP=true EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=false \
  OFFLINE_MODE=true RAG_EMBEDDING_MODEL= PYTHONPATH=backend \
  backend/venv/bin/python -m uvicorn open_webui.main:app --host 127.0.0.1 --port 18080
```

Then run:

```sh
CYPRESS_researchModelFixture=true npx cypress run \
  --spec cypress/e2e/experiment-access.cy.ts,cypress/e2e/admin-research-dashboard.cy.ts,cypress/e2e/research-profile.cy.ts \
  --config baseUrl=http://127.0.0.1:18080,video=false,numTestsKeptInMemory=0
```

The fixture flag seeds the local model's access for the test participant group and
enables streaming/stop/reload assertions. Without it, that one test is skipped.
The required-extension scenarios mock the extension bridge and workflow API
responses; the other access/dashboard checks use the isolated backend. The browser
fixture does not validate a real extension installation or an external LLM service.
The model fixture emits a synthetic experiment request event to exercise frontend
visible-response timing. Cypress verifies the timing payload and stubs persistence;
backend tests cover request timing and perturbation behavior separately.
