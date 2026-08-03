# VVV client-support branch

This branch is the isolated distribution channel for VVV client recognition and client configuration rendering.

The only runtime payload is `client_upgrade.py`. It is intentionally pure Python data/rendering code. It must not:

- install packages or change the operating system;
- read or write VPS configuration/state files;
- start, stop or restart services;
- download or replace Xray, sing-box, Caddy or cloudflared;
- change UUIDs, passwords, keys, nodes, ports, routes, certificates or systemd units.

Installed VPSes download this file through the fixed local engine at `/usr/local/lib/vvv/client_upgrade_engine.py`. The engine validates syntax, the restricted import/call surface, the version, the rendering contract, protected-file hashes and proxy process identity before accepting an update.

To add a client:

1. Add or adjust a specific `CLIENT_RULES` entry.
2. Reuse an existing renderer whenever the client requests a compatible format.
3. Add a renderer and `LOCAL_OUTPUTS` entry only when a genuinely different format is required.
4. Increase `VERSION`.
5. Run `python3 client_upgrade.py` and the repository client-support tests.

Default upgrade URL:

```text
https://raw.githubusercontent.com/weizhiok/vvv/client-support/client_upgrade.py
```
