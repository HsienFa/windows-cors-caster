# NTRIP Caster Native Linux Installation

The current `install.sh` targets Debian/Ubuntu with systemd and 64-bit Python 3.11. Adapt other
distributions manually using the same security rules; do not reuse legacy configuration examples.

## Secure defaults

- NTRIP listens on `0.0.0.0:2101` for remote clients. The installer opens only this TCP port.
- The Web admin service listens only on `127.0.0.1:5757`. The installer does not create a public HTTP proxy.
- The operator must provide an administrator password through hidden interactive input. There is no public default.
- A Flask secret is generated securely and written only to `/etc/2rtk/config.ini`; it is never printed.
- Runtime configuration is restricted to the service account, and the non-root account owns data and logs.

## Requirements

- Debian or Ubuntu with systemd
- Root privileges for packages, the service account, directories, firewall rules, and the systemd unit
- 64-bit Python 3.11 from a trusted system package source
- Access to the project Git repository and Python package source

## Install

```bash
git clone https://github.com/Rampump/NTRIPcaster.git
cd NTRIPcaster
chmod +x install.sh
sudo ./install.sh
```

The script requests the administrator password twice using hidden input. For non-interactive installation,
the operator must supply the required process environment securely. Do not place it in shell history,
deployment scripts, or public CI configuration.

Installed locations:

- Application: `/opt/2rtk`
- Runtime configuration: `/etc/2rtk/config.ini` (mode `0600`)
- Database: `/opt/2rtk/data/2rtk.db`
- Logs: `/var/log/2rtk`
- systemd unit: `2rtk.service`

`config.ini.example` is documentation only and cannot be executed directly. The installer invokes
`scripts/deployment_config.py` to create a runtime file using the current lowercase section/key schema.

## Access and firewall

Publishing NTRIP permits reachable hosts to probe the service and attempt authentication. Restrict the
firewall source networks, use strong credentials, and review accounts, logs, and unusual connections.

The Web admin interface is local-only. For remote maintenance, create an SSH tunnel:

```bash
ssh -L 5757:127.0.0.1:5757 operator@server
```

Then open `http://127.0.0.1:5757` locally. A public deployment requires an operator-managed reverse proxy
with TLS, authentication, source restrictions, and correct WebSocket forwarding. Do not expose the admin UI
over plain HTTP.

## Service management

```bash
sudo systemctl status 2rtk
sudo systemctl restart 2rtk
sudo journalctl -u 2rtk --since today
```

Never publish runtime configuration or full logs. If required credentials are missing, the application
refuses to start and reports an error without echoing credential values.

## Map services

The native Linux configuration defaults to OpenStreetMap. Google Maps is optional; store its API key only
in protected runtime configuration or a controlled process environment. Google mode uses the official Maps
JavaScript API and falls back to OpenStreetMap if the key is absent or loading fails.

Before using an external map provider, review and complete the project [Terms of Use](TERMS-OF-USE.md),
[Privacy Policy](PRIVACY-POLICY.md), and [Third-Party Notices](THIRD-PARTY-NOTICES.md).

## Updates and backups

Back up protected runtime configuration, the database, and required logs before an update, and stop the
service during the maintenance window. Never add backups to Git. After updating code and the virtual
environment, run tests and configuration validation before restarting the systemd service.
