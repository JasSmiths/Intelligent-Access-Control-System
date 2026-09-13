# Backend

FastAPI service for access events, presence, modular integrations, simulation,
and the Alfred V3 AI agent.

The backend is deliberately arranged around ports and adapters:

- Core services own business rules.
- Modules own hardware or third-party protocol details.
- API routers expose versioned external contracts.
- Alfred tool contracts, actor context, registry assembly, and handlers have
  explicit owners; see [the backend agent guide](../docs/agent/backend.md#alfred-v3).

Use [the architecture checklist](../docs/architecture.md) when extending a
feature and [isolated regression validation](../docs/validation/phase1.md) before
handoff. Schedules now have shared CRUD, assignment and override operations;
see [their ownership guide](../docs/agent/backend.md#schedule-operations). Extend
that pattern to other features instead of copying API/Alfred business rules.

### Feature mutations and Alfred execution

VisitorPassService and AutomationService own their shared mutation rules/audit;
notification_rules.py owns notification-rule transactions. API and Alfred are
adapters. See `../docs/agent/backend.md` and the milestone 3 validation handoff.

Tool execution uses `ai/tools.py` (`ToolOutcome`, `ToolError`) and
`ai/tool_inputs.py`. Feature catalogs own labels, confirmation presentation and
success flags; chat orchestration consumes that metadata. Domain output payloads
and historical chat/audit records are preserved.

Notification dispatch now uses `services/notification_runs.py` for durable
claims/action checkpoints and `services/notification_dispatch.py` for execution
and recovery. Rendering/providers remain in `services/notifications.py`.
See [recovery, review and rollback](../docs/validation/milestone4-recovery.md).
