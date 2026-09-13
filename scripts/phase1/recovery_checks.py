"""Required isolated checks shared by the local harness and CI.

This is an explicit inventory, not a second runner. Each PostgreSQL pytest file
gets a fresh clone of the migrated template, its own Redis database and retained
pytest temporary directory. Adding a phase1 test requires classifying it here.
"""

import re


PERSISTENCE_TESTS = [
    'backend/tests/test_access_device_command_journal.py',
    'scripts/phase1/test_persistence.py',
    'scripts/phase1/test_schedule_operations.py',
    'scripts/phase1/test_feature_operations.py',
    'scripts/phase1/test_notification_recovery.py',
    'scripts/phase1/test_access_pipeline.py',
    'scripts/phase1/test_alfred_approval_persistence.py',
    'scripts/phase1/test_authority_mutations.py',
    'scripts/phase1/test_notification_activation.py',
    'scripts/phase1/test_automation_intake.py',
    'scripts/phase1/test_automation_recovery.py',
    'scripts/phase1/test_automation_dispatch.py',
    'scripts/phase1/test_automation_admission_order.py',
    'scripts/phase1/test_automation_webhook_intake.py',
    'scripts/phase1/test_recognition_authorization.py',
    'scripts/phase1/test_camera_evidence.py',
    'scripts/phase1/test_movement_admission.py',
    'scripts/phase1/test_movement_reconciliation_fairness.py',
    'scripts/phase1/test_visitor_reservations.py',
    'scripts/phase1/test_visitor_reservation_recovery.py',
    'scripts/phase1/test_access_delivery_recovery.py',
    'scripts/phase1/test_notification_handoffs.py',
    'scripts/phase1/test_confirmed_notifications.py',
    'scripts/phase1/test_notification_dispatch_truth.py',
    'scripts/phase1/test_actionable_recovery.py',
    'scripts/phase1/test_incoming_messages.py',
    'scripts/phase1/test_messaging_authority.py',
    'scripts/phase1/test_messaging_confirmations.py',
    'scripts/phase1/test_visitor_conversation_authority.py',
    'scripts/phase1/test_whatsapp_inbox.py',
    'scripts/phase1/test_discord_inbox.py',
    'scripts/phase1/test_feedback_recovery.py',
    'scripts/phase1/test_recovery_discovery.py',
    'scripts/phase1/test_recovery_hold_persistence.py',
    'scripts/phase1/test_release_recovery.py',
    'scripts/phase1/test_release_restore_rehearsal.py',
    'scripts/phase1/test_delivery_schema_compatibility.py',
]
DIAGNOSTIC_TESTS = ['scripts/phase1/test_recovery_boundaries.py']
SCHEMA_CHECKS = ['scripts/phase1/test_schema_contract.py']
HOST_TESTS = ['scripts/phase1/test_source_snapshot.py', 'scripts/phase1/test_harness_configuration.py']
REQUIRED_HELPERS = ['scripts/phase1/recovery_checks.py', 'scripts/phase1/database_restore.py']


def persistence_checks(args):
    """Keep defaults mandatory and additional selections deduplicated by path."""
    selected = {path: 'persistence' for path in PERSISTENCE_TESTS}
    selected.update({path: 'diagnostic' for path in DIAGNOSTIC_TESTS})
    for path in args.persistence_test:
        selected.setdefault(path, 'persistence')
    for path in args.diagnostic:
        selected.setdefault(path, 'diagnostic')
    return list(selected.items())


def schema_checks(args):
    return list(dict.fromkeys(SCHEMA_CHECKS + args.schema_check))


def required_sources(args):
    required = list(args.include) + args.diagnostic + args.persistence_test + args.schema_check
    if args.mode == 'full':
        required += HOST_TESTS + REQUIRED_HELPERS + schema_checks(args)
        required += [path for path, _ in persistence_checks(args)]
    return list(dict.fromkeys(required))


def suite_identity(prefix, index):
    if not re.fullmatch(r'iacs-p1-[0-9a-f]{12}', prefix) or not 1 <= index <= 999:
        raise ValueError('A suite requires a harness-owned namespace and bounded positive index')
    name = f'suite-{index:03d}'
    return name, prefix.replace('-', '_') + f'_s{index:03d}'


def pytest_arguments(name, paths=()):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
        raise ValueError('Invalid retained pytest evidence name')
    return ['-m', 'pytest', '-q', '-o', 'junit_family=xunit1', '-p', 'no:cacheprovider',
            *paths, f'--basetemp=/results/{name}/tmp', f'--junitxml=/results/{name}/junit.xml']


def inventory_errors(root):
    """Fail the host check when new tests have no explicit execution class."""
    classified = PERSISTENCE_TESTS + DIAGNOSTIC_TESTS + SCHEMA_CHECKS + HOST_TESTS
    present = {str(path.relative_to(root)) for path in (root / 'scripts/phase1').glob('test_*.py')}
    errors = []
    if len(classified) != len(set(classified)):
        errors.append('A phase1 test is classified more than once')
    errors.extend('Missing classified check: ' + path for path in sorted(set(classified))
                  if not (root / path).is_file())
    errors.extend('Unclassified phase1 test: ' + path for path in sorted(present - set(classified)))
    errors.extend('Missing required helper: ' + path for path in REQUIRED_HELPERS if not (root / path).is_file())
    return errors
