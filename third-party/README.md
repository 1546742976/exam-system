# Veyon source snapshot

Source: https://github.com/veyon/veyon — stable release **v4.11.4**, commit `1cebf45f6c1af8bab2c96278a58db1cf2f56685d`.

`veyon-manifest.json` records 10 recursively pinned submodules and SHA-256 hashes for all 2,940 copied files. Upstream Git metadata is excluded. Original license and copyright files remain in `vendor/veyon`; upstream code is unmodified. Veyon is GPL-2.0; subcomponents retain their own notices.

Reproduction (fresh destination):

```sh
git clone --branch v4.11.4 --depth 1 --recurse-submodules --shallow-submodules https://github.com/veyon/veyon.git .source-cache/veyon
node tools/vendor.mjs
```

The snapshot is source provenance, not proof of a successful Windows build. Use the official matching release installer for initial deployment; no Veyon installer is silently executed by project scripts.
