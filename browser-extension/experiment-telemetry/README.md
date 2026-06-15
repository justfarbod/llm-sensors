# Open WebUI Experiment Telemetry Extension

## Local installation

1. Run Open WebUI and apply the backend database migration.
2. Open `chrome://extensions` or `edge://extensions`.
3. Enable Developer mode and choose **Load unpacked**.
4. Select `browser-extension/experiment-telemetry`.
5. Open the extension options and save the exact Open WebUI origin, such as `http://localhost:8080`.
6. Reload the Open WebUI tab.

The extension requests access only to the configured origin. It has no tabs or browsing-history permission.

## Verify the correct session

Sign in as a non-admin participant whose Experiment Mode state is `IN_PROGRESS`. In browser developer tools, confirm:

- `GET /api/v1/experiments/telemetry/status` returns `enabled: true` and the expected session ID.
- Batched `POST /api/v1/experiments/telemetry/events` requests contain that same session ID.
- The `experiment_telemetry_event` and `experiment_telemetry_summary` rows use the authenticated user's ID and expected session ID.

Admins, logged-out users, ordinary users, and participants outside `IN_PROGRESS` should receive no event requests.

## Privacy audit

Inspect event request bodies and `experiment_telemetry_event.payload_json`. They must contain only classified key metadata, numeric clipboard metadata, visibility/focus metadata, and modifier booleans.

Search stored payloads for the forbidden keys `text`, `content`, `raw`, `key`, `clipboard`, `clipboard_text`, and `password`. The backend rejects any batch containing them. Clipboard text is read only transiently to calculate length and line count and is never queued, logged, transmitted, or stored.
