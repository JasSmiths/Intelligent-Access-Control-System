#!/usr/bin/env python3
"""Build/check a final runtime image with its installed renderer and no overlays."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter
import uuid

from source_snapshot import copy_sources, discover_sources, source_fingerprint
from validate import Harness, arguments, validate_resource_paths, validation_run_lock, ValidationRunBusyError, image_references
from storage import StorageGuard, docker_free, validation_interrupts, draining_cleanup

ROOT = Path(__file__).resolve().parents[2]


def image_identity(image):
    row = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    user = row['Config'].get('User', '')
    if not user or user.split(':', 1)[0] in {'root', '0'}:
        raise ValueError('Smoke image must configure its own non-root runtime user')
    return {'id': row['Id'], 'size_bytes': row['Size'], 'user': user}


def smoke_command(image, output, name):
    # The only host mount is a newly owned output directory. Code, environment,
    # template and browsers all come from the final image, with no dependency or
    # source overlays and no application lifespan/provider startup.
    return ['docker', 'run', '--pull=never', '--rm', '--name', name, '--label', 'iacs.validation=' + name,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
            '--mount', f'type=bind,src={output},dst=/results',
            '--env', 'IACS_ENVIRONMENT=testing', '--env', 'IACS_AUTO_CREATE_SCHEMA=false',
            '--env', 'IACS_DATABASE_URL=postgresql+asyncpg://validation:synthetic-only@127.0.0.1:1/synthetic_pdf',
            '--env', 'IACS_REDIS_URL=redis://127.0.0.1:1/0',
            '--env', 'IACS_AUTH_SECRET_KEY=synthetic-pdf-smoke-never-production',
            '--env', 'IACS_DATA_DIR=/tmp/data', '--env', 'IACS_LOG_DIR=/tmp/logs',
            '--entrypoint', 'python', image, '-m', 'tools.report_pdf_smoke', '--output', '/results/report.pdf']


def run_smoke(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--build', action='store_true', help='Build this checkout with the runtime target before checking it')
    args = parser.parse_args(argv)
    evidence = args.evidence_root.expanduser().resolve()
    if evidence.is_relative_to(ROOT) or ROOT.is_relative_to(evidence):
        parser.error('Evidence root must be outside the source checkout')
    inspection = Harness(arguments(['--check-only']), evidence, ROOT)
    before = inspection.production_state()
    validate_resource_paths(evidence, None, before)
    inspection.storage = StorageGuard(evidence, vm_probe=lambda: docker_free(image_references(ROOT)['backend']))
    inspection.storage.check(force=True)
    evidence.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix='iacs-runtime-pdf-', dir=evidence))
    inspection.run = run
    output = run / 'artifacts'
    output.mkdir(mode=0o777)
    output.chmod(0o777)  # image's own runtime UID writes only this owned fixture folder
    name = 'iacs-validation-pdf-' + uuid.uuid4().hex[:12]
    inspection.prefix = name
    inspection.owned.append(name)
    inspection.write('owned-containers.json', inspection.owned)
    record = {'image_reference': args.image, 'build_seconds': None, 'status': 'running'}
    status = 1
    try:
        selected, inventory = discover_sources(ROOT)
        hashes = copy_sources(ROOT, run / 'source', selected, check_storage=inspection.storage.check)
        record.update(source_fingerprint=source_fingerprint(hashes), source_sha256=hashes, source_inventory=inventory)
        if args.build:
            command = ['docker', 'build', '--target', 'runtime', '--tag', args.image, str(run / 'source/backend')]
            record['build_command'] = command
            started = perf_counter()
            inspection.check('build', command, True, timeout=3600)
            record['build_seconds'] = round(perf_counter() - started, 3)
        record['image'] = image_identity(args.image)
        inspection.storage.images[record['image']['id']] = record['image']['size_bytes']
        inspection.storage.check(force=True)
        command = smoke_command(record['image']['id'], output, name)
        record['command'] = command
        started = perf_counter()
        inspection.check('smoke', command, True, timeout=180)
        record['smoke_seconds'] = round(perf_counter() - started, 3)
        pdf = output / 'report.pdf'
        if not pdf.read_bytes().startswith(b'%PDF-'):
            raise RuntimeError('Smoke output is not a PDF')
        record.update(status='passed', pdf_bytes=pdf.stat().st_size, pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
        status = 0
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError, KeyboardInterrupt) as error:
        record.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
                      error=str(error), error_type=type(error).__name__)
    finally:
        with draining_cleanup():
            inspection.cleanup()
            record['cleanup_results']={key:value for key,value in inspection.results.items() if key in {'cleanup','cleanup_evidence'}}
            if any(record['cleanup_results'].values()):
                record['status']='failed'
                status=1
            try:
                after = inspection.production_state()
            except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
                after = None
                record['production_comparison_error']=type(error).__name__
            record['production_unchanged'] = before == after
            if before != after:
                record['status'] = 'failed'
                status = 1
            (run / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
            (run / 'production-before.json').write_text(json.dumps(before, indent=2) + '\n')
            (run / 'production-after.json').write_text(json.dumps(after, indent=2) + '\n')
            print(json.dumps({'evidence': str(run), 'status': record['status'], 'image': record.get('image')}, sort_keys=True))
    return status


def main(argv=None):
    try:
        with validation_run_lock('runtime'), validation_interrupts():
            return run_smoke(argv)
    except (ValidationRunBusyError, OSError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print('Runtime smoke refused: ' + type(error).__name__ + ': ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
