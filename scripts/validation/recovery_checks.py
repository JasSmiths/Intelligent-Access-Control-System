"""Required isolated checks shared by the local harness and CI.

This is an explicit inventory, not a second runner. Each PostgreSQL pytest file
gets a fresh clone of the migrated template, its own Redis database and retained
pytest temporary directory. Adding a validation test requires classifying it here.
"""

import re


PERSISTENCE_TESTS = [
    "scripts/validation/test_vehicle_information.py",
    "scripts/validation/test_plate_lookup_index.py",
    "scripts/validation/test_directory_operations.py",
    "scripts/validation/test_resident_recovery.py",
    "scripts/validation/test_resident_recovery_automatic.py",
    'backend/tests/test_access_device_command_journal.py',
    'scripts/validation/test_persistence.py',
    'scripts/validation/test_schedule_operations.py',
    'scripts/validation/test_history_persistence.py',
    'scripts/validation/test_feature_operations.py',
    'scripts/validation/test_notification_recovery.py',
    'scripts/validation/test_access_pipeline.py',
    'scripts/validation/test_authority_mutations.py',
    'scripts/validation/test_notification_activation.py',
    'scripts/validation/test_automation_intake.py',
    'scripts/validation/test_automation_recovery.py',
    'scripts/validation/test_automation_dispatch.py',
    'scripts/validation/test_automation_admission_order.py',
    'scripts/validation/test_automation_webhook_intake.py',
    'scripts/validation/test_recognition_authorization.py',
    'scripts/validation/test_camera_evidence.py',
    'scripts/validation/test_movement_admission.py',
    'scripts/validation/test_movement_reconciliation_fairness.py',
    'scripts/validation/test_visitor_reservations.py',
    'scripts/validation/test_visitor_reservation_recovery.py',
    'scripts/validation/test_access_delivery_recovery.py',
    'scripts/validation/test_notification_handoffs.py',
    'scripts/validation/test_confirmed_notifications.py',
    'scripts/validation/test_notification_dispatch_truth.py',
    'scripts/validation/test_actionable_recovery.py',
    'scripts/validation/test_recovery_discovery.py',
    'scripts/validation/test_recovery_hold_persistence.py',
    'scripts/validation/test_delivery_schema_compatibility.py',
    'scripts/validation/test_feature_retirement.py',
]
DIAGNOSTIC_TESTS = ['scripts/validation/test_recovery_boundaries.py']
SCHEMA_CHECKS = ['scripts/validation/test_schema_contract.py']
HOST_TESTS = ['scripts/validation/test_source_snapshot.py', 'scripts/validation/test_harness_configuration.py']
REQUIRED_HELPERS = ['scripts/validation/recovery_checks.py', 'scripts/validation/database_restore.py']


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
    if not re.fullmatch(r'iacs-validation-[0-9a-f]{12}', prefix) or not 1 <= index <= 999:
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
    present = {str(path.relative_to(root)) for path in (root / 'scripts/validation').glob('test_*.py')}
    errors = []
    if len(classified) != len(set(classified)):
        errors.append('A validation test is classified more than once')
    errors.extend('Missing classified check: ' + path for path in sorted(set(classified))
                  if not (root / path).is_file())
    errors.extend('Unclassified validation test: ' + path for path in sorted(present - set(classified)))
    errors.extend('Missing required helper: ' + path for path in REQUIRED_HELPERS if not (root / path).is_file())
    return errors
