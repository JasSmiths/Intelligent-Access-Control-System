# Backend

FastAPI services turn validated LPR observations into durable access/movement
records, presence and audited gate/garage commands. The backend also owns
notifications, integrations and camera image analysis.

Start with the [backend agent guide](../docs/agent/backend.md) for task-specific
owners and transaction contracts, [architecture guide](../docs/architecture.md)
for extension/retirement rules, and [hardware safety](../docs/agent/hardware-safety.md)
before touching physical effects.

- `app/main.py`: startup, lifecycle and application construction.
- `app/api/router.py`: `/api/v1` route registration.
- `app/services/`: domain policy, transactions and audit.
- `app/modules/`: hardware and provider protocols.
- `app/ai/providers.py`: camera image-analysis providers.
- `app/models/` and `alembic/`: durable schema and migrations.
- `tests/`: focused behavior and contract tests; `../scripts/phase1/` owns isolated
  PostgreSQL validation.

Use the [isolated harness](../docs/validation/phase1.md) for backend validation.
Full-flow simulation is harness-only. Deployment, production migrations and live
provider tests require separate authorization.

Dependencies come from the reviewed repository/image build. There is no runtime
package updater or package overlay. The current UniFi Protect pin is defined in
`pyproject.toml`; its wrapper contract is documented
[here](../docs/unifi-protect-private-api.md).

GitHub Project Checks audits the complete locked dependency set, including
development and transitive packages. Resolve audit failures with targeted
`uv lock --upgrade-package PACKAGE==FIXED_VERSION` updates, then rerun the strict
audit and isolated harness before deploying the rebuilt image.
