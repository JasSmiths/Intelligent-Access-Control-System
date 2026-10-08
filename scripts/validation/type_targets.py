"""Single checked-body/strict target policy; pure host-side source inspection.

Package owners include future modules automatically. Finite legacy overrides in
mixed packages preserve the existing checked scope; they are migration debt, not
permission to exempt new owners. Strict targets also enter checked-body checks.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class TypeOwner:
    path: str
    level: str
    exceptions: tuple[tuple[str, str], ...] = ()


# These established boundaries cannot silently become unclassified if a package
# registration is removed. New cohesive owners belong here and in TYPE_OWNERS.
PROTECTED_PACKAGES = (
    'app/services/directory', 'app/services/access',
    'app/services/workflows', 'app/modules/dvsa', 'app/modules/icloud_calendar',
)
TYPE_OWNERS = (
    TypeOwner('app/services/action_confirmations.py', 'strict'),
    TypeOwner('app/modules/icloud_calendar', 'strict'),
    TypeOwner('app/services/auth_root.py', 'strict'),
    TypeOwner('app/services/calendar_accounts.py', 'strict'),
    TypeOwner('app/services/calendar_authentication.py', 'strict'),
    TypeOwner('app/services/calendar_reconciliation.py', 'strict'),
    TypeOwner('app/services/calendar_request_authority.py', 'strict'),
    TypeOwner('app/services/calendar_sync_commit.py', 'strict'),
    TypeOwner('app/services/calendar_sync_contracts.py', 'strict'),
    TypeOwner('app/services/calendar_sync_engine.py', 'strict'),
    TypeOwner('app/services/calendar_sync_sessions.py', 'strict'),
    TypeOwner('app/services/calendar_sync_store.py', 'strict'),
    TypeOwner('app/services/calendar_workflow_authority.py', 'strict'),
    TypeOwner('app/services/icloud_calendar_sdk.py', 'strict'),
    TypeOwner('app/services/icloud_calendar.py', 'strict'),
    TypeOwner('app/services/automation_policy.py', 'strict'),
    TypeOwner('app/services/automation_execution.py', 'strict'),
    TypeOwner('app/api/v1/icloud_calendar.py', 'strict'),
    TypeOwner('tools/report_pdf_smoke.py', 'strict'),
    TypeOwner('app/models/access_pipeline.py', 'strict'),
    TypeOwner('app/modules/access_devices/binding_policies.py', 'strict'),
    TypeOwner('app/services/notification_runs.py', 'checked'),
    TypeOwner('app/services/notification_dispatch.py', 'strict'),
    TypeOwner('app/services/notification_rules.py', 'checked'),
    TypeOwner('app/services/mutation_context.py', 'checked'),
    TypeOwner('app/services/schedule_assignments.py', 'checked'),
    TypeOwner('app/services/schedule_operations.py', 'checked'),
    TypeOwner('app/services/domain_events.py', 'checked'),
    TypeOwner('app/services/event_bus.py', 'strict'),
    TypeOwner('app/services/realtime_contracts.py', 'strict'),
    TypeOwner('app/services/access_events.py', 'checked'),
    TypeOwner('app/services/lpr_ingest.py', 'strict'),
    TypeOwner('app/ai/providers.py', 'checked'),
    TypeOwner('app/services/notification_rendering.py', 'checked'),
    TypeOwner('app/services/vehicle_information.py', 'strict'),
    TypeOwner('app/services/vehicle_information_contracts.py', 'strict'),
    TypeOwner('app/services/vehicle_information_store.py', 'strict'),
    TypeOwner('app/services/vehicle_information_jobs.py', 'strict'),
    TypeOwner('app/services/vehicle_information_authorization.py', 'strict'),
    TypeOwner('app/composition.py', 'strict'),
    TypeOwner('app/services/integration_effects.py', 'strict'),
    TypeOwner('app/services/gate_controller.py', 'strict'),
    TypeOwner('app/services/access_device_outcomes.py', 'strict'),
    TypeOwner('app/services/gate_malfunction_policy.py', 'strict'),
    TypeOwner('app/schemas/directory.py', 'strict'),
    TypeOwner('app/services/reports.py', 'checked'),
    TypeOwner('app/services/report_durations.py', 'strict'),
    TypeOwner('app/services/report_contracts.py', 'strict'),
    TypeOwner('app/services/report_reads.py', 'strict'),
    TypeOwner('app/services/leaderboard_reads.py', 'strict'),
    TypeOwner('app/services/presence_reads.py', 'strict'),
    TypeOwner('app/services/expected_presence.py', 'strict'),
    TypeOwner('app/services/report_images.py', 'strict'),
    TypeOwner('app/services/report_renderer.py', 'strict'),
    TypeOwner('app/services/report_exports.py', 'strict'),
    TypeOwner('app/services/notifications.py', 'strict'),
    TypeOwner('app/services/notification_planning.py', 'strict'),
    TypeOwner('app/services/notification_authorization.py', 'strict'),
    TypeOwner('app/services/notification_recipients.py', 'strict'),
    TypeOwner('app/services/automations.py', 'strict'),
    TypeOwner('app/services/automation_actions.py', 'strict'),
    TypeOwner('app/services/automation_integration_actions.py', 'strict'),
    TypeOwner('app/services/automation_rules.py', 'strict'),
    TypeOwner('app/services/automation_webhooks.py', 'strict'),
    TypeOwner('app/services/automation_time_intake.py', 'strict'),
    TypeOwner('app/services/automation_serialization.py', 'strict'),
    TypeOwner('app/services/workflow_dispatch_ports.py', 'strict'),
    TypeOwner('app/services/workflow_session.py', 'strict'),
    TypeOwner('app/services/automation_dispatch.py', 'strict'),
    TypeOwner('app/services/automation_scheduling.py', 'strict'),
    TypeOwner('app/services/directory', 'strict'),
    TypeOwner('app/services/access', 'checked', (
        ('pipeline_contracts.py', 'strict'),
        ('pipeline_store.py', 'strict'),
        ('read_codec.py', 'strict'),
        ('window_store.py', 'strict'),
        ('window_candidates.py', 'strict'),
        ('observation_tokens.py', 'strict'),
        ('publication.py', 'strict'),
        ('publication_worker.py', 'strict'),
        ('completion_contracts.py', 'strict'),
        ('completion_store.py', 'strict'),
        ('completion_results.py', 'strict'),
        ('visitor_link.py', 'strict'),
        ('__init__.py', 'legacy'),
        ('authorization.py', 'legacy'),
        ('delivery.py', 'legacy'),
        ('historical.py', 'legacy'),
        ('plate_matching.py', 'strict'),
        ('snapshots.py', 'legacy'),
    )),
    TypeOwner('app/services/workflows', 'strict', (
        ('__init__.py', 'legacy'),
        ('automation_definition.py', 'legacy'),
        ('catalog.py', 'legacy'),
        ('context.py', 'legacy'),
        ('notification_payloads.py', 'legacy'),
        ('template_recipients.py', 'legacy'),
        ('vehicle_away.py', 'legacy'),
        ('visitor_notifications.py', 'legacy'),
    )),
    TypeOwner('app/modules/dvsa', 'strict', (
        ('__init__.py', 'legacy'),
    )),
)


def _safe_path(relative):
    path = PurePosixPath(relative)
    return bool(relative) and not path.is_absolute() and '..' not in path.parts


def classified_modules(root, owners=TYPE_OWNERS):
    """Return module levels, including explicit legacy debt, and policy errors."""
    backend = root / 'backend'
    classified = {}
    errors = []
    seen = set()
    for owner in owners:
        if not _safe_path(owner.path) or owner.path in seen:
            errors.append('Invalid or duplicate type owner: ' + owner.path)
            continue
        seen.add(owner.path)
        if owner.level not in {'checked', 'strict'}:
            errors.append('Unknown type owner level: ' + owner.path)
            continue
        path = backend / owner.path
        if not path.exists() or path.is_symlink() or not path.resolve().is_relative_to(backend.resolve()):
            errors.append('Missing or unsafe type owner: ' + owner.path)
            continue
        exceptions = dict(owner.exceptions)
        if len(exceptions) != len(owner.exceptions):
            errors.append('Duplicate type override: ' + owner.path)
        if owner.exceptions and not path.is_dir():
            errors.append('Type overrides require a package: ' + owner.path)
        files = sorted(path.rglob('*.py')) if path.is_dir() else [path]
        members = {file.relative_to(path).as_posix() for file in files} if path.is_dir() else set()
        for relative, level in owner.exceptions:
            if not _safe_path(relative) or relative not in members or level not in {'checked', 'strict', 'legacy'}:
                errors.append('Unknown or orphan type override: ' + owner.path + '/' + relative)
            elif level == owner.level:
                errors.append('Redundant type override: ' + owner.path + '/' + relative)
        for file in files:
            relative = file.relative_to(backend).as_posix()
            level = exceptions.get(file.relative_to(path).as_posix(), owner.level) if path.is_dir() else owner.level
            if file.suffix != '.py' or file.is_symlink():
                errors.append('Unsafe type module: ' + relative)
            if relative in classified:
                errors.append('Overlapping type owners: ' + relative)
            classified[relative] = level
    for package in PROTECTED_PACKAGES:
        path = backend / package
        if not path.is_dir():
            errors.append('Missing protected type package: ' + package)
        for file in sorted(path.rglob('*.py')):
            relative = file.relative_to(backend).as_posix()
            if relative not in classified:
                errors.append('Unclassified protected type module: ' + relative)
    return classified, errors


def type_targets(root, level, owners=TYPE_OWNERS):
    if level not in {'checked', 'strict'}:
        raise ValueError('Unknown type check level: ' + level)
    classified, errors = classified_modules(root, owners)
    if errors:
        raise ValueError('; '.join(errors))
    selected = {'strict'} if level == 'strict' else {'checked', 'strict'}
    targets = []
    for owner in owners:
        path = root / 'backend' / owner.path
        # Use a package argument wherever its coverage is uniform. Mixed existing
        # packages expand to filenames so their finite legacy exceptions remain
        # exact, while newly added files inherit their owner's declared default.
        if not owner.exceptions and owner.level in selected:
            targets.append(owner.path)
        elif path.is_dir():
            targets.extend(relative for relative, mode in sorted(classified.items())
                           if relative.startswith(owner.path + '/') and mode in selected)
    return targets


def type_commands(root, owners=TYPE_OWNERS):
    return {
        'mypy': ['-m', 'mypy', '--cache-dir=/tmp/mypy', '--check-untyped-defs',
                 *type_targets(root, 'checked', owners)],
        'typed-contracts': ['-m', 'mypy', '--cache-dir=/tmp/mypy-contracts',
                            '--check-untyped-defs', '--disallow-untyped-defs',
                            *type_targets(root, 'strict', owners)],
    }
