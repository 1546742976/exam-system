# Deployment and acceptance

## Dependencies

Use Node.js 24 and HydroOJ **5.1.0**. Run `npm ci --ignore-scripts`, `npm run build:frontend`, `npm run typecheck`, `npm test`, `npm run simulate`. Upstream HydroOJ ships TypeScript sources rather than declarations; the typecheck command reports owned-code diagnostics separately from upstream diagnostics. It is not a clean upstream build claim.

Run `npm run test:hydro` for isolated real MongoDB integration checks; the first run downloads MongoDB 7.0.14 into `data/hydro-integration/mongodb-binaries` (about 592 MB on Windows). It does not modify your normal HydroOJ profile. `npm run smoke:hydro` only checks runtime loading and route registration. Apply the checked patches described in DEPENDENCY-PATCHES.md after installation. Review the full and production audit reports; a zero-advisory result does not replace integration and real-device acceptance.

Register the absolute `addon` directory using HydroOJ's addon installation mechanism (`hydrooj addon add /absolute/path/exam-system/addon`), then restart the controlled test instance. Preserve the root node_modules directory or install the same locked dependency tree at the deployment location. MongoDB is supplied by HydroOJ; no standalone database server is started by this addon.

## Student computers

Install official Veyon **4.11.4**, matching `vendor/veyon`. Enable WebAPI on each student computer (Local mode), HTTPS and certificate/private-key configuration. Give each computer a certificate whose SAN matches its registered HTTPS hostname. Keep certificate validation enabled.

Create a distinct Veyon authentication key for each device; install its public part on that device and configure Veyon access control. Keep corresponding private files in the restricted NUC credential directory. AuthKeys WebAPI sends that private key over the authenticated HTTPS connection to the registered student endpoint; it is not a remote-signing protocol. Never use an all-classroom master private key.

Allow NUC access to each student's HTTPS WebAPI port; block ordinary student access. Reserve addresses in DHCP. No global disabling of Windows firewall or TLS checks is required. Do not run the full-class Veyon Master screen wall alongside scheduled capture.

## NUC

Copy `devices.example.json` to a private device registry and adjust absolute paths and registered hosts. Do not commit actual private keys.

```sh
export EXAM_MONITOR_DEVICES=/etc/exam-monitor/devices.json
export EXAM_MONITOR_DATA=/var/lib/exam-monitor
export EXAM_MONITOR_COLLECTOR=1
```

Exactly one HydroOJ process may have `EXAM_MONITOR_COLLECTOR=1`; other processes omit it. This is a deployment constraint, not automatic distributed leader election. The collector shares one bandwidth bucket across all exams. Run one worker for the initial deployment. Registry and key changes require restart.

First test one computer:

```sh
npm run probe -- /etc/exam-monitor/seat01.json data/probe
```

The probe expects one device object, while the registry is an array. It outputs a result without credentials and returns nonzero on failure.

Teacher entry: `/d/<domain>/exam-monitor/<contest-id>`. Import e.g. `[{"studentId":2,"deviceId":"seat01"}]`, start monitoring, inspect status and request a screenshot. Student identifiers are HydroOJ numeric account IDs. Stop before changing mappings. Competition end automatically stops acquisition. Retention is competition end plus seven days, with hourly cleanup.

## Required real acceptance (not performed automatically)

1. Verify real TLS identity, Veyon authorization, complete initial image, 1080p/4K, lock screen and disconnect/reconnect.
2. Check teacher domain/contest permissions and cross-exam image denial. Verify screenshot history and expired-file deletion in a disposable exam.
3. Run 10 real clients and measure total Veyon process memory, NUC process memory, CPU, connection time and JPEG size. Width is capped, not longest edge.
4. Run 100 clients for three hours with OJ submissions and judging. Compare application bytes with NIC traffic; protocol overhead and buffered bursts are outside the application token bucket.
5. Confirm persistent queue delays are visible. Do not increase the chosen 2 Mbit/s budget automatically.

`reports/simulation.json` is a deterministic scheduling model, not a wireless, image-quality or actual Veyon benchmark. Mock JPEG payloads exercise framing and transport, not image readability. Real NUC/Windows acceptance remains required.
