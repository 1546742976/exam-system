# Dependency remediation

HydroOJ remains **5.1.0**. The root `package.json` overrides and `package-lock.json` are part of deployment, not just development.

| Dependency | Fixed selection |
|---|---|
| tar | 7.5.21 |
| adm-zip | 0.6.1 |
| @simplewebauthn/server | 13.3.2 |
| koa-proxies / uuid | 11.1.1 in koa-proxies |
| superagent-proxy / proxy-agent | 6.5.0 plus checked constructor adaptation |
| old PAC / ip / once chain | replaced by modern proxy-agent dependency tree |
| basic-ftp | 6.2.1 (additional advisory discovered during remediation) |
| vitest / @vitest/mocker | 4.1.12 |

Install in this order:

```sh
npm ci --ignore-scripts
npm run patch:dependencies
npm run typecheck
npm test
npm run test:hydro
npm run build:frontend
```

`tools/patch-dependencies.mjs` checks superagent-proxy 3.0.0 and the original SHA-256 before adapting `proxyAgent(uri)` to `new ProxyAgent({getProxyForUrl:()=>uri})`. It is idempotent and rejects changed upstream files. It does not log proxy credentials. The explicit URI remains in control across redirects.

For a separate deployed HydroOJ package root, merge the exact overrides there, regenerate and inspect that root's lockfile, install, then run `node /path/to/project/tools/patch-dependencies.mjs /path/to/hydro/package.json`. Run audits and regression checks against that root. Applying the patch only to the addon directory does **not** patch another installed HydroOJ tree. No NUC deployment has been changed in this session.

Type diagnostics are compared against `reports/typecheck-baseline.json`; new upstream diagnostics fail the check, owned diagnostics must remain zero. Baseline recording is an explicit maintenance operation, not part of normal CI. Existing upstream errors are acknowledged, not fixed or hidden as clean upstream compilation.

Remaining acceptance: HTTPS CONNECT/SOCKS/PAC live proxy fixtures, comprehensive adversarial archive fixtures (symlink traversal, duplicate entries and decompression bombs), valid WebAuthn registration/authentication and failed attestation chains, complete browser/HTTP tests, real Windows Veyon, NUC and wireless tests. The current tests and zero-advisory reports must not be represented as completion of these checks.
