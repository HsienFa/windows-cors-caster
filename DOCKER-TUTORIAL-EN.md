# NTRIP Caster Docker Installation and Usage

This guide matches the current `Dockerfile`, `docker-compose.yml`, and deployment scripts. The image does
not execute `config.ini.example`. On first start, the entrypoint writes a runtime configuration using the
current lowercase schema into a named volume.

## Security baseline

- Never commit `.env`, runtime configuration, databases, or logs.
- There is no public administrator password. The container stops if the required value is missing or unsafe.
- A Flask secret is securely generated and stored only in the configuration volume. It is not printed.
- Web publishing is bound to `127.0.0.1:5757` on the Docker host by default.
- NTRIP is published on `0.0.0.0:2101` by default. This permits remote probing and login attempts. Restrict
  source networks with a firewall and use strong credentials. For local testing, set `NTRIP_PUBLISH_HOST`
  to `127.0.0.1`.

## Requirements

- Docker Engine 24 or later
- Docker Compose v2 (`docker compose`)
- Python 3.11 to create the ignored `.env` safely

## Recommended startup

Linux/macOS:

```bash
python3 scripts/deployment_config.py prepare-env --env-file .env --example .env.example
chmod 600 .env
```

Before the first start, open `.env` in a text editor and set an administrator password known only to the
operator. Never paste the file into a terminal, log, issue, or chat. Then validate and start the core service:

```bash
docker compose config --quiet
docker compose up -d ntrip-caster
```

The interactive helper is also available:

```bash
chmod +x quick-start.sh docker-deploy.sh docker-entrypoint.sh
./quick-start.sh
```

Windows CMD:

```bat
docker-deploy.bat --check
docker-deploy.bat up
```

`docker-deploy.bat up` creates the ignored `.env` and required directories without printing credentials.

## Ports and listeners

| Service | Container listener | Default host publishing | Notes |
|---|---|---|---|
| NTRIP | `0.0.0.0:2101` | `0.0.0.0:2101` | Intended for NTRIP clients; potentially public by default |
| Web | `0.0.0.0:5757` | `127.0.0.1:5757` | Listens inside the container but is host-local by default |
| Nginx | Internal HTTP/HTTPS | `127.0.0.1:80/443` | Nginx profile only; harden before publishing |
| Grafana | Internal service port | `127.0.0.1:3000` | Monitoring profile only |
| Prometheus | Internal service port | `127.0.0.1:9090` | Monitoring profile only |
| Redis | Internal service port | `127.0.0.1:6379` | Cache profile only; Redis is not required by the core service |

For remote administration, use a VPN or a hardened reverse proxy with TLS, authentication, and source
restrictions. Do not publish the Web port globally until those controls are in place.

## Configuration and persistence

- Compose stores `/app/config/config.ini` in the `ntrip-config` volume.
- Data and logs use the `ntrip-data` and `ntrip-logs` volumes.
- The first start requires administrator credentials from `.env`. The entrypoint never substitutes a public
  example value.
- Existing runtime configuration is not overwritten automatically. Change credentials through the admin UI
  or an operator-controlled maintenance procedure.

## Monitoring profile

Prepare monitoring credentials before starting the profile:

```bash
python3 scripts/deployment_config.py prepare-env --env-file .env --example .env.example --monitoring --profiles monitoring
docker compose --profile monitoring config --quiet
docker compose --profile monitoring up -d
```

Grafana validates its administrator credential at startup. Blank, example, known-default, or short values
cause it to stop.

## Maps and outbound requests

- `MAP_PROVIDER=osm` is the default; browsers request tiles from OpenStreetMap.
- To opt in to Google Maps, set the provider to `google` and place the API key only in the ignored `.env`.
- Google mode uses the official Maps JavaScript API. A missing key or load failure falls back to OpenStreetMap.
- Review [Terms of Use](TERMS-OF-USE.md), [Privacy Policy](PRIVACY-POLICY.md), and
  [Third-Party Notices](THIRD-PARTY-NOTICES.md) before using an external map service.

## Operations

```bash
docker compose ps
docker compose logs --tail 100 ntrip-caster
docker compose exec ntrip-caster python /app/healthcheck.py
docker compose down
```

Do not publish `.env`, runtime configuration, or full logs. If the first start stops, confirm that `.env`
exists and that required credentials were safely set, then run `docker compose config --quiet`.
