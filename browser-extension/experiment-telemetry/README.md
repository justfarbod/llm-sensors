# Open WebUI Experiment Telemetry Extension

This Manifest V3 Chrome extension records privacy-bounded writing interaction metadata and the full lifecycle metadata of every non-incognito tab in normal browser windows while an authenticated Open WebUI experiment is `IN_PROGRESS`.

It does **not** record typed text, answer text, clipboard contents, screenshots, cookies, request bodies, historical browsing history, or incognito tabs. It does record complete current-tab URLs (including paths, queries, and fragments), titles, favicon URLs, tab state, and tab lifecycle events. Chrome therefore displays its **Read your browsing history** permission warning.

## Fixed-origin build

The Chrome Web Store artifact must be built for one exact HTTPS Open WebUI origin:

```bash
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=https://research.example.edu npm run build:experiment-extension
```

For an unpacked local-development build only, HTTP loopback origins are accepted:

```bash
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=http://localhost:3000 npm run build:experiment-extension
```

Load `browser-extension/experiment-telemetry/dist` for local verification, then zip that directory for an unlisted Chrome Web Store submission. Do not submit the source directory because its manifest contains an intentional origin placeholder.

For this repository's local deployment, leave the store ID and URL empty and configure `.env.deploy` as follows:

```text
EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=http://localhost:3000
EXPERIMENT_TELEMETRY_EXTENSION_ID=
EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL=
EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION=2.0.0
```

Restart `compose.deploy.yaml`, open `chrome://extensions`, enable Developer mode, and load the unpacked
`browser-extension/experiment-telemetry/dist` directory. HTTP is accepted only for loopback development; use the
Chrome Web Store workflow below for any non-loopback deployment.

After the store assigns an extension ID and URL, configure the server:

```text
EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=https://research.example.edu
EXPERIMENT_TELEMETRY_EXTENSION_ID=<chrome-store-extension-id>
EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL=<unlisted-chrome-store-url>
EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION=2.0.0
```

The website blocks experiment Start until a current heartbeat confirms version 2, schema 2, the `tabs` permission, and disabled incognito access. A normal website cannot silently install an extension; participants must confirm installation on the Chrome Web Store page.

## Verification

1. Apply the backend migration and configure the matching local or store server values above.
2. Build and load the fixed-origin extension.
3. Sign in as a non-admin participant and complete consent and the pre-survey.
4. Confirm the Start gate reports the extension as ready.
5. Start the experiment and create, navigate, activate, move, and close several normal tabs.
6. Verify schema-version-2 events at `/api/v1/analytics/experiments/sessions/{session_id}/tab-activity`.
7. Open an incognito window and verify that no event with `incognito: true` is accepted or stored.

Raw tab URLs and titles are sensitive and are retained indefinitely by this implementation. The participant disclosure and institutional data-handling policy must reflect that fact.
