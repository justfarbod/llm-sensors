# Frontend profile validation — 2026-09-11

The research frontend is implemented and has a successful deployable build in
`build`. The complete acceptance criteria are **not yet met**: a successful full
production build, production profile switching and a controlled full/research
memory comparison remain unverified on this WSL machine.

## Passed checks

| Check                             | Result                                                                                                                                 |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Research production build         | Successful with Node 22.22.3, `--max-old-space-size=3328`                                                                              |
| Build exclusion                   | 5,532 client and 1,607 server module IDs checked; no prohibited implementations/packages or runtime assets in the deployable output    |
| Frontend and extension tests      | 52 passed across 10 test files                                                                                                         |
| Profile unit tests                | 6 passed: defaults/validation, synchronization, retained routes/assets, exclusion checks, stale request flags and Python RPC callbacks |
| Development synchronization       | Canonical route addition/edit/deletion and alternate-port forwarding passed                                                            |
| Profile configuration restoration | Full → research → full routes, static directory, cache selection and optional module resolution passed using Vite                      |
| Browser regression                | All 15 checks passed across the final per-spec runs against isolated test data                                                         |
| Backend research tests            | 138 passed; 1 pre-existing failure, described below                                                                                    |
| Targeted strict TypeScript        | Shared profile definition, browser guards and build plugin passed                                                                      |

Browser coverage includes legacy consent/surveys/writing, required extension
gating, essay Markdown, plan essay/question submission and progression,
task-scoped chat history, telemetry flush before submission, chat streaming/stop/
reload, frontend response timing, stale optional preferences, compatibility links,
admin users/groups, task authoring, workflow editing, advanced navigation,
dashboard filters and full-session exports. Workflow/extension responses and timing
persistence are mocked at their external boundaries. Chat uses a local model
fixture through the actual backend. These checks do not establish exhaustive
end-to-end coverage of every workflow, real extension installation or provider.

The browser checks exposed and fixed an unhandled denied settings request during
startup and a legacy post-survey select binding that threw `question is not
defined`. The survey fix keeps numeric payload conversion unchanged. Test setup
now waits for page initialization and allows initial chat-editor focus to settle
before typing into the task pane; that existing focus behavior was not changed.

The backend failure is
`test_not_applicable_response_is_small_and_has_no_nested_collections` in
`backend/open_webui/test/models/test_experiments.py`: its exact response-key
assertion omits the existing `telemetry_extension` field. No backend source or
test files were changed by this work. The same failure occurred before these
final frontend changes.

## Memory and remaining validation

The latest successful research build took **98.02 seconds** under `/usr/bin/time`
and reported **3,756,908 KiB peak RSS (about 3.58 GiB)**. The whole limited service's
sampled RAM-plus-swap charge peaked around **3.98 GiB**, including its other
processes and charged cache. RSS is not a measure of total WSL memory.

Full-profile Pyodide preparation was measured separately: **13.84 seconds** and
**1,346,228 KiB peak RSS (about 1.28 GiB)**. Research commands skip that stage.

Full compilation attempts were stopped by the reserve guard or the service memory
limit. They did not produce a successful full-profile build. There is therefore
**no valid same-settings full/research memory-reduction result**, and no reduction
percentage is claimed. A complete full → research → full production build and
feature-restoration check needs more available memory or another machine.

Research builds contain no Pyodide assets and the generation tests exclude them.
A separate compilation with prepared canonical Pyodide assets temporarily moved
aside was stopped by the memory reserve; those assets were restored intact. A
successful clean checkout build without prepared assets is still an explicit
follow-up check. A clean repository-wide Svelte type check is also not established;
the earlier broad check reported extensive errors, while the targeted profile
helper check passes.

Builds and browser/backend tests ran sequentially. Production compilation used a
4 GiB RAM/512 MiB swap service limit plus checks that stop the build below 900 MiB
combined available RAM/free swap or 12 GiB free Windows disk space. A later research
attempt stopped safely at that reserve; retrying with the smaller Node heap above
succeeded. Test services are stopped after validation. No WSL memory configuration,
production database, saved settings or optional source/dependencies were removed.

Local logs are retained in the ignored `.generated/validation` directory:
`research-3328.*`, `full-preparation.*`, `full-3584.*`,
`browser-tests-verified.log` (chat/admin passed),
`browser-tests-workflow-settled.log` (all workflow checks passed),
`backend-tests-final.log`, `frontend-tests-verified.log`,
`profile-tests-final.log`, `profile-switch.log` and `dev-route-tests-final.log`.
See [Frontend profiles](FRONTEND_PROFILES.md) for commands and fixture setup.
