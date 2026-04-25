# Backend

FastAPI service for access events, presence, modular integrations, simulation,
and the future AI agent.

The backend is deliberately arranged around ports and adapters:

- Core services own business rules.
- Modules own hardware or third-party protocol details.
- API routers expose stable external contracts.

This keeps future modules such as `modules/lpr/axis.py` or
`modules/gate/relay_board.py` isolated from the rest of the application.
