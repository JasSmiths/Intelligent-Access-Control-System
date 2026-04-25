# Phase 6: Agent Guide

Phase 6 creates the repository-level orientation document for future AI agents:

- `AGENTS.md`

The guide is the authoritative project map for future implementation work. It
captures the current stack, Docker/LAN/NPM deployment shape, bind-mount storage
rules, backend service boundaries, modular integration contracts, API endpoints,
frontend design language, verification commands, and known unfinished areas.

## Key Rules Captured

- Use `docker compose` for the full system.
- Use bind mounts only; do not introduce Docker named volumes.
- Serve users through the frontend service on host port `8089`.
- Keep backend host port `8088` for direct API/debug access.
- Point Nginx Proxy Manager at `http://<docker-host-ip>:8089` and enable
  WebSocket support.
- Keep LPR, gate control, announcements, and notifications behind module
  interfaces under `backend/app/modules`.
- Keep AI tools grounded in database/API state instead of model invention.
- Preserve the Modern SaaS Clean operational UI style established in Phase 5.

## Verification

Recommended checks after changes:

```bash
python3 -m compileall backend/app
cd frontend && npm run build
docker compose config
curl -fsS http://localhost:8089/api/v1/health
```
