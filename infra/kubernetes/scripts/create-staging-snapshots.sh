#!/usr/bin/env bash
# Actual CSI backup. Deliberately leaves all workloads stopped for operator recovery.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 - "$SCRIPT_DIR" "$@" <<'PY'
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

NS = ('kerosene-staging', 'kerosene-staging-vault')
TARGETS = [(NS[0], 'data-staging-' + x + '-0') for x in ('bitcoin', 'lnd', 'postgres', 'redis', 'tor')]
TARGETS += [(NS[0], 'vault-' + str(x) + '-data') for x in (1, 2, 3)]
TARGETS += [(NS[1], x) for x in ('data-vault-tor-0', 'vault-data')]
LABEL = 'backup.kerosene.io/run'

def need(ok, message):
    if not ok:
        raise RuntimeError(message)

def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

p = argparse.ArgumentParser(description='Create ten retained CSI staging snapshots after stopping all staging workloads. NO automatic restart or signer activation.')
p.add_argument('--context', required=True)
p.add_argument('--run-id', required=True)
p.add_argument('--release-id', required=True)
p.add_argument('--release-lock-digest', required=True)
p.add_argument('--snapshot-class', required=True)
p.add_argument('--output-dir', required=True, help='New private evidence directory outside the repository')
p.add_argument('--approve-stop-staging', action='store_true', help='Authorize downtime in BOTH staging namespaces; workloads stay stopped even on failure')
p.add_argument('--quiesce-hook', help='Optional absolute external executable: called with enter then verify, context and output directory; never used to restart')
p.add_argument('--timeout', type=int, default=600)
a = p.parse_args(sys.argv[2:])
k = [os.environ.get('KUBECTL', 'kubectl'), '--context', a.context, '--request-timeout=30s']
out = Path(a.output_dir)

def kub(*args, obj=None):
    r = subprocess.run(k + list(args), input=json.dumps(obj) if obj is not None else None, text=True, capture_output=True, timeout=40)
    need(r.returncode == 0, 'kubectl failed: ' + ' '.join(args[:4]) + ' (inspect cluster events; no sensitive stderr copied)')
    return r.stdout

def get(resource, name=None, ns=None):
    args = (['-n', ns] if ns else []) + ['get', resource] + ([name] if name else []) + ['-o', 'json']
    return json.loads(kub(*args))

def save(name, obj):
    with (out / name).open('x', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write('\n')

def stopped():
    for ns in NS:
        need(not get('pods', ns=ns)['items'], 'quiesce failed: pods remain/reappeared in ' + ns)
        current = get('deployments,statefulsets', ns=ns)['items']
        need({(r['kind'], r['metadata']['name'], r['metadata']['uid']) for r in current} == {(w['kind'], w['name'], w['uid']) for w in workloads if w['namespace'] == ns}, 'workload identities changed during backup')
        for r in current:
            need(r['spec'].get('replicas', 1) == 0, 'workload restarted during snapshot window')

try:
    os.umask(0o077)
    need(a.approve_stop_staging, '--approve-stop-staging is required')
    need(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,39}', a.run_id), 'run-id must be a unique 3-40 character DNS label')
    need(re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,127}', a.release_id), 'invalid release-id')
    need(re.fullmatch(r'sha256:[0-9a-f]{64}', a.release_lock_digest), 'invalid release-lock-digest')
    need(1 <= a.timeout <= 86400, 'timeout must be 1..86400 seconds')
    repository = Path(sys.argv[1]).resolve().parents[2]
    need(not out.resolve().is_relative_to(repository), 'evidence output directory must be outside the repository')
    out.mkdir(mode=0o700)  # Refuse reuse, including symlinks; preserve failure evidence.
    sc = get('volumesnapshotclasses.snapshot.storage.k8s.io', a.snapshot_class)
    need(sc.get('deletionPolicy') == 'Retain' and sc.get('driver'), 'designated VolumeSnapshotClass must use Retain and a CSI driver')
    workloads, pvcs = [], []
    for ns in NS:
        for resource in ('horizontalpodautoscalers', 'daemonsets', 'jobs', 'cronjobs'):
            need(not get(resource, ns=ns)['items'], resource + ' must be removed/suspended under a separate approved maintenance plan before backup')
        for w in get('deployments,statefulsets', ns=ns)['items']:
            workloads.append({'namespace': ns, 'kind': w['kind'], 'name': w['metadata']['name'], 'uid': w['metadata']['uid'], 'replicas': w['spec'].get('replicas', 1)})
        existing = get('volumesnapshots.snapshot.storage.k8s.io', ns=ns)['items']
        need(not any(s['metadata'].get('labels', {}).get(LABEL) == a.run_id for s in existing), 'snapshot run-id already exists')
    for ns, name in TARGETS:
        pvc = get('pvc', name, ns)
        need(pvc.get('status', {}).get('phase') == 'Bound' and pvc['spec'].get('volumeMode', 'Filesystem') == 'Filesystem', 'PVC must be a bound filesystem: ' + name)
        pv = get('pv', pvc['spec']['volumeName'])
        need(pv['spec'].get('csi', {}).get('driver') == sc['driver'], 'PVC CSI driver differs from designated snapshot class: ' + name)
        ref = pv['spec']['claimRef']
        need((ref['namespace'], ref['name'], ref['uid']) == (ns, name, pvc['metadata']['uid']), 'PV claim binding mismatch')
        pvcs.append({'namespace': ns, 'name': name, 'uid': pvc['metadata']['uid'], 'pv': pv['metadata']['name'], 'pvUid': pv['metadata']['uid']})
    save('maintenance.json', {'context': a.context, 'runId': a.run_id, 'startedAt': now(), 'workloads': workloads, 'pvcs': pvcs, 'snapshotClass': {'name': a.snapshot_class, 'uid': sc['metadata']['uid'], 'driver': sc['driver']}, 'automaticResume': False, 'consistency': 'graceful-stop; independent volume snapshots, NOT an atomic multi-service transaction'})
    if a.quiesce_hook:
        hook = Path(a.quiesce_hook)
        need(hook.is_absolute() and hook.is_file() and os.access(hook, os.X_OK), 'quiesce-hook must be an explicit external executable')
        subprocess.run([str(hook), 'enter', a.context, str(out.resolve())], check=True, timeout=a.timeout)
    for w in workloads:
        kub('-n', w['namespace'], 'scale', w['kind'].lower() + '/' + w['name'], '--replicas=0', '--current-replicas=' + str(w['replicas']), '--resource-version=' + get(w['kind'].lower(), w['name'], w['namespace'])['metadata']['resourceVersion'])
    deadline = time.monotonic() + a.timeout
    while any(get('pods', ns=ns)['items'] for ns in NS):
        need(time.monotonic() < deadline, 'quiesce timeout; workloads remain stopped, inspect maintenance.json')
        time.sleep(2)
    stopped()
    if a.quiesce_hook:
        subprocess.run([a.quiesce_hook, 'verify', a.context, str(out.resolve())], check=True, timeout=a.timeout)
    save('quiesced.json', {'verifiedAt': now(), 'hook': a.quiesce_hook, 'podsAbsent': True, 'transactionalConsistency': False})
    snapshot_uids = {}
    for i, (ns, name) in enumerate(sorted(TARGETS)):
        stopped()
        created = json.loads(kub('create', '-f', '-', '-o', 'json', obj={'apiVersion': 'snapshot.storage.k8s.io/v1', 'kind': 'VolumeSnapshot', 'metadata': {'name': a.run_id + '-' + str(i), 'namespace': ns, 'labels': {LABEL: a.run_id, 'app.kubernetes.io/managed-by': 'staging-backup'}, 'annotations': {'backup.kerosene.io/release-id': a.release_id, 'backup.kerosene.io/lock-digest': a.release_lock_digest}}, 'spec': {'volumeSnapshotClassName': a.snapshot_class, 'source': {'persistentVolumeClaimName': name}}}))
        snapshot_uids[i] = created['metadata']['uid']
    deadline = time.monotonic() + a.timeout
    while True:
        stopped()
        ready = True
        for i, (ns, name) in enumerate(sorted(TARGETS)):
            s = get('volumesnapshots.snapshot.storage.k8s.io', a.run_id + '-' + str(i), ns)
            need(s['metadata']['uid'] == snapshot_uids[i] and s['metadata'].get('labels', {}).get(LABEL) == a.run_id and s['metadata'].get('annotations', {}).get('backup.kerosene.io/lock-digest') == a.release_lock_digest, 'snapshot identity/labels changed')
            need(not s['metadata'].get('deletionTimestamp') and s['spec'] == {'volumeSnapshotClassName': a.snapshot_class, 'source': {'persistentVolumeClaimName': name}}, 'snapshot identity/source changed')
            status = s.get('status', {})
            need(not status.get('error'), 'CSI snapshot error: ' + name)
            ready &= status.get('readyToUse') is True and bool(status.get('boundVolumeSnapshotContentName'))
        if ready:
            break
        need(time.monotonic() < deadline, 'snapshot readiness timeout; no attestation request emitted')
        time.sleep(2)
    for original in pvcs:
        current = get('pvc', original['name'], original['namespace'])
        need(current['metadata']['uid'] == original['uid'] and current['spec']['volumeName'] == original['pv'], 'source PVC changed during backup')
    for i, (ns, _) in enumerate(sorted(TARGETS)):
        s = get('volumesnapshots.snapshot.storage.k8s.io', a.run_id + '-' + str(i), ns)
        c = get('volumesnapshotcontents.snapshot.storage.k8s.io', s['status']['boundVolumeSnapshotContentName'])
        ref = c['spec']['volumeSnapshotRef']
        need(c['spec']['deletionPolicy'] == 'Retain' and c['spec']['driver'] == sc['driver'] and (ref['namespace'], ref['name'], ref['uid']) == (ns, s['metadata']['name'], s['metadata']['uid']), 'snapshot content binding/retention mismatch')
        need(c.get('status', {}).get('readyToUse') is True and c['status'].get('snapshotHandle') and not c['status'].get('error'), 'snapshot content not ready')
    stopped()
    collector = str(Path(sys.argv[1]) / 'collect-staging-volumesnapshot-attestation-request.sh')
    collection = subprocess.run(['bash', collector, '--context', a.context, '--release-id', a.release_id, '--release-lock-digest', a.release_lock_digest, '--snapshot-selector', LABEL + '=' + a.run_id], check=True, capture_output=True, text=True, timeout=90)
    request = json.loads(collection.stdout)
    stopped()
    need({s['volumeSnapshotUid'] for s in request['snapshots']} == set(snapshot_uids.values()), 'collector observed a different snapshot set')
    save('request.json', request)
    save('completed.json', {'completedAt': now(), 'requestDigest': digest(request), 'workloadsRemainStopped': True, 'restoreTested': False})
    print('Backup ready: ' + str(out / 'request.json') + '; all workloads remain stopped. Resume requires a separate operator decision; never activate Vault signers automatically.')
except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
    print('fail closed: ' + str(exc), file=sys.stderr)
    print('No automatic restart or cleanup. Inspect the evidence directory and staging state.', file=sys.stderr)
    sys.exit(78)
PY
