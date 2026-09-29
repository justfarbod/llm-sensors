# Open WebUI Experiment Telemetry Extension

This Manifest V3 Chrome extension records writing interaction metadata and the full lifecycle metadata of every non-incognito tab in normal browser windows while an authenticated Open WebUI experiment is `IN_PROGRESS`.

It records the exact key pressed for every keystroke in a tracked field (essay, question, and chat inputs), along with key-hold duration, inter-key timing, and modifier state. It does **not** record keystrokes in password-type inputs, which are excluded entirely. It also does not record screenshots, cookies, request bodies, or historical browsing history, and it excludes incognito tabs. It does record complete current-tab URLs (including paths, queries, and fragments), titles, favicon URLs, tab state, and tab lifecycle events. Chrome therefore displays its **Read your browsing history** permission warning.

Because literal keystrokes are recorded for tracked fields, this data can reconstruct the text a participant typed there. The participant disclosure and institutional data-handling policy (e.g. IRB/consent materials) must reflect this plainly, not just the tab-URL retention noted below.

It also records the literal text involved in copy, cut, and paste actions in tracked fields (essay, question, and chat inputs) — the copied/cut selection text, or the pasted clipboard text — truncated to 20,000 characters per event, in addition to the previously recorded text length and line count. As with keystroke capture, this can reconstruct sensitive content a participant copied, cut, or pasted into a tracked field, including content that originated outside Open WebUI (e.g. pasted from another application or document). The participant disclosure and institutional data-handling policy (e.g. IRB/consent materials) must reflect this plainly, alongside the keystroke disclosure above.

## Origin configuration

The extension works with exactly one Open WebUI origin, and that origin is stored only in
`manifest.json` (`host_permissions`, `content_scripts[].matches`, and the toolbar tooltip). The code
reads it at runtime from `chrome.runtime.getManifest().host_permissions`, so the same build can be
pointed at any deployment without rebuilding.

Build the extension (no origin required):

```bash
npm run build:experiment-extension
```

Without an origin, the build keeps the source manifest's default, `http://localhost:5173`. If
`EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN` is set in the frontend's local `.env` or in the build
environment (the environment takes precedence), the build applies it to `dist/manifest.json`:

```bash
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=https://research.example.edu npm run build:experiment-extension
```

To retarget an already built folder (for example, a downloaded or shared `dist`) without npm or a
rebuild, run:

```bash
node browser-extension/experiment-telemetry/set-origin.mjs browser-extension/experiment-telemetry/dist https://research.example.edu
```

`set-origin.mjs` rewrites `host_permissions`, `content_scripts[].matches`, and the action title in
that folder's `manifest.json`. It accepts one exact HTTPS origin, or an HTTP loopback origin
(`localhost`, `127.0.0.1`, `[::1]`) for development; paths and wildcard hosts are rejected. After
changing the origin, click **Reload** on the extension's card in `chrome://extensions`, then check
its tooltip matches the experiment tab's address. Use the exact address shown in the participant's
browser: `http://localhost:5173` and `http://localhost:8080` are different origins, even when both
serve the same application. Restart the backend after changing its `.env` origin.

For this repository's source-development workflow, leave the ID, name, and download URL empty and
configure the root `.env` file as follows:

```text
EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=http://localhost:5173
EXPERIMENT_TELEMETRY_EXTENSION_ID=
EXPERIMENT_TELEMETRY_EXTENSION_NAME=
EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL=
EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION=2.2.0
```

Restart the backend with `cd backend && sh dev.sh`, run the frontend with `npm run dev` from the repository root, open
`chrome://extensions`, enable Developer mode, and load the unpacked
`browser-extension/experiment-telemetry/dist` directory. The Vite server proxies the extension's same-origin `/api`
requests to the backend on port 8080. HTTP is accepted only for loopback development.

## Extension identity

Every heartbeat reports the extension's ID (`chrome.runtime.id`) and manifest name
(`chrome.runtime.getManifest().name`, `Open WebUI Experiment Telemetry`). The server checks
identity as follows:

- If `EXPERIMENT_TELEMETRY_EXTENSION_ID` is set, the reported ID must match it and the name is
  not checked. Use this for a Chrome Web Store listing, whose ID is fixed.
- Otherwise, if `EXPERIMENT_TELEMETRY_EXTENSION_NAME` is set, the reported name must match it.
  Use this for self-distributed unpacked builds, whose ID differs per installation.
- If neither is set, identity is not checked, which is accepted only for loopback origins.

A non-loopback origin requires an ID or a name, plus an HTTPS
`EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL`. That URL is shown to participants as the
**Download extension** button and may point at a Chrome Web Store listing or at a zipped build:

```text
EXPERIMENT_TELEMETRY_EXTENSION_ENABLED=true
EXPERIMENT_TELEMETRY_EXTENSION_ORIGIN=https://research.example.edu
EXPERIMENT_TELEMETRY_EXTENSION_ID=<chrome-store-extension-id>
EXPERIMENT_TELEMETRY_EXTENSION_NAME=
EXPERIMENT_TELEMETRY_EXTENSION_STORE_URL=<https-download-or-store-url>
EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION=2.2.0
```

For a self-distributed build, leave the ID empty and set
`EXPERIMENT_TELEMETRY_EXTENSION_NAME=Open WebUI Experiment Telemetry` instead. A name is not a
secret; it only guards against a mismatched extension, not a deliberately impersonating one.

The website blocks experiment Start until a current heartbeat confirms version 2, schema 2, the `tabs` permission, and disabled incognito access. A normal website cannot silently install an extension; participants must confirm installation themselves (on the Chrome Web Store page, or by loading a downloaded build in `chrome://extensions`).

Version 2.0.1 connects automatically in the participant's existing tab, including after signing in
without a page reload. After installation, return to the experiment tab and wait for **Extension
connected**. There is no need to click the extension icon. The start screen checks immediately,
when the tab regains focus, and every 2.5 seconds while visible. If the check takes longer than
three seconds, **Check again** becomes available; background checks continue.

The toolbar icon is a fallback: it reconnects the current experiment tab without reloading or
changing its URL. From another website, it focuses an existing experiment tab (preferring the same
window, then the most recently accessed tab). It opens a new tab only if no matching tab exists.
An enabled extension with site access is required; Start still requires server confirmation for
the participant's current session as well as a response from the extension in that tab.

Deploy the updated frontend together with the rebuilt extension. For an unpacked installation,
rebuild using the exact origin participants open, then click **Reload** on the extension's card
in `chrome://extensions`. When upgrading from 2.0.0, reload existing experiment pages once to
replace their old content scripts. For store installations, publish the 2.0.1 artifact and ensure
participants have received the update. Schema version and permissions are unchanged.

Version 2.0.2 reads the local origin during builds and shows the built origin in the toolbar tooltip.
The version number alone does not identify the origin: after any rebuild or `set-origin.mjs` run,
reload the unpacked extension in `chrome://extensions`, then check its tooltip matches the
experiment tab's address.

Version 2.1.0 adds the literal key value (`key_value`) to every keystroke event and raises the
schema version to 3, which the server now requires for keystroke telemetry. The extension's
minimum-version gate (`EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION`) must also be raised to `2.1.0`
so the server continues to require it. Rebuild and redeploy the extension together with this
server configuration change; a 2.0.x extension connecting to a server already requiring schema
version 3 will have its keystroke batches rejected, and the start gate will report the extension
as not ready until participants update. Reload existing experiment pages once after upgrading, as
with the 2.0.0 to 2.0.1 upgrade.

Version 2.2.0 adds the literal copied/cut selection text and pasted clipboard text (`text_value`,
truncated to 20,000 characters) to copy, cut, and paste events, and raises the schema version to
4, which the server now requires for copy/cut/paste telemetry. The extension's minimum-version
gate (`EXPERIMENT_TELEMETRY_EXTENSION_MIN_VERSION`) must also be raised to `2.2.0` so the server
continues to require it. Rebuild and redeploy the extension together with this server
configuration change; a 2.1.x extension connecting to a server already requiring schema version 4
will have its copy/cut/paste batches rejected, and the start gate will report the extension as not
ready until participants update. Reload existing experiment pages once after upgrading, as with
prior upgrades.

## Verification

1. Apply the backend migration and configure the matching local or store server values above.
2. Build the extension, set its origin if needed (`set-origin.mjs`), and load it.
3. Sign in as a non-admin participant and complete consent and the pre-survey.
4. Confirm the Start gate reports **Extension connected** without clicking the toolbar icon or
   reloading. Also test installing while the start screen is already open and signing in from `/auth`.
5. Start the experiment and create, navigate, activate, move, and close several normal tabs.
6. Verify schema-version-2 events at `/api/v1/analytics/experiments/sessions/{session_id}/tab-activity`.
7. Open an incognito window and verify that no event with `incognito: true` is accepted or stored.
8. Click the toolbar icon in the experiment tab and from another website. Verify it reuses the
   existing tab and preserves the URL, session, and any entered work. Test multiple tabs/windows
   and verify that a new tab is created only when none matches the configured origin.
9. Disable the extension or disconnect the network at the start screen. Verify Start becomes
   unavailable, then reconnect and verify recovery without navigation.

Raw tab URLs and titles are sensitive and are retained indefinitely by this implementation. The participant disclosure and institutional data-handling policy must reflect that fact.
