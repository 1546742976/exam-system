# Validation evidence

- `tests.json`: automated tests of scheduling, bounded concurrency, mapping policy, HTTPS capture, TLS rejection, shared bandwidth, timeout and partial-file cleanup.
- `hydro-integration.json`: installed HydroOJ 5.1.0 handler methods with a real temporary MongoDB 7.0.14 instance. Includes permissions, import/start/stop/manual request/status and retention cleanup. This is not a full HTTP/browser test.
- `simulation.json`: deterministic 100-device, three-hour scheduler/bandwidth model including large images, unreachable clients and a burst of manual requests. Peak concurrency 4, maximum pending devices 100; p95 queue delay about 62 seconds under this deliberately stressed scenario. Not a real Veyon performance benchmark.
- `dependency-audit.json`: dependency advisory report. Upstream dependencies are not silently force-upgraded or HydroOJ downgraded to resolve advisories.

`npm run typecheck` checks this project's diagnostics using installed dependency types, while separately reporting diagnostics from HydroOJ's shipped TypeScript sources (800 upstream diagnostics at initial verification). It does not claim that upstream source passes this project's strict compiler configuration.

Not yet verified: official Windows Veyon execution, actual screen image readability, complete HydroOJ HTTP/template/browser flow, NUC memory/CPU, real wireless bandwidth and three-hour real classroom acceptance. The automated mock image payload verifies transport framing rather than photographic validity.
