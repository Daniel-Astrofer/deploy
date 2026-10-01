#!/usr/bin/env bash
# Offline restore verification; no application processes, keys, or services.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 - "$SCRIPT_DIR" "$@" <<'PY'
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

LABEL = 'backup.kerosene.io/run'
NS = ('kerosene-staging', 'kerosene-staging-vault')
TARGETS = {(NS[0], 'data-staging-' + x + '-0') for x in ('bitcoin', 'lnd', 'postgres', 'redis', 'tor')}
TARGETS |= {(NS[0], 'vault-' + str(x) + '-data') for x in (1, 2, 3)}
TARGETS |= {(NS[1], x) for x in ('data-vault-tor-0', 'vault-data')}

# Sent as the Pod's literal command, never as a ConfigMap or arbitrary operator command.
# stdout contains only aggregate hashes/counts, never filenames, database data or keys.
PROBE = r'''
import base64, hashlib, json, os, pathlib, re, socket, stat, subprocess, sys
def need(ok):
    if not ok: raise RuntimeError('offline integrity or isolation check failed')
def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def digest(x): return 'sha256:' + hashlib.sha256(canonical(x)).hexdigest()
def inventory(root):
    records = []
    def unreadable(error): raise error
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in dirs + files:
            p = pathlib.Path(parent) / name
            mode = p.lstat().st_mode
            need(stat.S_ISDIR(mode) or stat.S_ISREG(mode))
        for name in files:
            p = pathlib.Path(parent) / name
            h = hashlib.sha256()
            with p.open('rb') as f:
                while True:
                    block = f.read(1024 * 1024)
                    if not block: break
                    h.update(block)
            records.append({'path': p.relative_to(root).as_posix(), 'size': p.stat().st_size, 'sha256': h.hexdigest()})
    records.sort(key=lambda x: x['path'])
    need(records and any(x['size'] > 0 for x in records))
    return records
def tool(argv):
    r = subprocess.run(argv, capture_output=True, timeout=config['timeout'], env={'PATH': os.environ['PATH'], 'LC_ALL': 'C'})
    need(r.returncode == 0 and not r.stderr.strip())
    return r.stdout.decode('utf-8', errors='strict')
def check_aof(path):
    # Redis 7.4 redis-check-aof uses fopen("r+") even without --fix. Parse RESP
    # directly instead of copying data or making the restored mount writable.
    multi = False
    with path.open('rb') as f:
        while True:
            line = f.readline(128)
            if not line: break
            if line.startswith(b'#TS:'):
                need(re.fullmatch(rb'#TS:\d+\r\n', line)); continue
            need(re.fullmatch(rb'\*\d+\r\n', line))
            argc = int(line[1:-2]); need(1 <= argc <= 1000000)
            command = b''
            for i in range(argc):
                line = f.readline(128); need(re.fullmatch(rb'\$\d+\r\n', line))
                length = int(line[1:-2]); need(length <= 512 * 1024 * 1024)
                remaining = length
                while remaining:
                    chunk = f.read(min(remaining, 1024 * 1024)); need(chunk)
                    if i == 0 and length <= 16: command += chunk
                    remaining -= len(chunk)
                need(f.read(2) == b'\r\n')
            if command.upper() == b'MULTI': need(not multi and argc == 1); multi = True
            if command.upper() == b'EXEC': need(multi and argc == 1); multi = False
        need(not multi)
def check_manifest(path):
    active = []
    for line in path.read_text().splitlines():
        m = re.fullmatch(r'file ([A-Za-z0-9_.-]+) seq ([1-9][0-9]*) type ([bih])', line); need(m)
        name, seq, typ = m.groups(); need(name not in ('.', '..'))
        if typ != 'h': active.append((name, int(seq), typ))
    need(active and sum(x[2] == 'b' for x in active) == 1)
    need(len({x[0] for x in active}) == len(active))
    increments = [seq for _, seq, typ in active if typ == 'i']
    need(increments == sorted(set(increments)))
    for name, _, _ in active:
        file = path.parent / name; need(file.is_file())
        with file.open('rb') as f: header = f.read(5)
        if header == b'REDIS': tool(['redis-check-rdb', str(file)])
        else: check_aof(file)
try:
    config = json.loads(sys.argv[1]); root = pathlib.Path('/data')
    # Read-only mount verified from kernel state, without attempted writes to restored data.
    mounts = [line.split() for line in pathlib.Path('/proc/mounts').read_text().splitlines()]
    need(any(m[1] == '/data' and 'ro' in m[3].split(',') for m in mounts))
    for endpoint in config['egressTargets']:
        sock = socket.socket(); sock.settimeout(3)
        try:
            sock.connect((endpoint['ip'], endpoint['port']))
        except (TimeoutError, OSError): pass
        else: raise RuntimeError('egress unexpectedly permitted')
        finally: sock.close()
    before = inventory(root)
    need(digest(before) == config['expectedTreeDigest'])
    kind = config['kind']; checks = ['full-file-read', 'approved-tree-sha256', 'read-only-mount', 'egress-denied']
    if kind == 'postgres':
        pg = root / 'pgdata'
        need((pg / 'PG_VERSION').read_text().strip() == '16')
        data = tool(['pg_controldata', '-D', str(pg)])
        need(re.search(r'Database cluster state:\s+shut down\s*$', data, re.M))
        m = re.search(r'Data page checksum version:\s+(\d+)', data); need(m)
        if int(m.group(1)):
            tool(['pg_checksums', '--check', '-D', str(pg)]); checks.append('postgres-page-checksums')
        else: checks.append('postgres-page-checksums-disabled')
        checks.append('postgres16-clean-shutdown-control')
    elif kind == 'redis':
        rdb = list(root.rglob('*.rdb')); manifests = list(root.rglob('*.aof.manifest'))
        aof = list(root.rglob('*.aof'))
        need(rdb or manifests or aof)
        for f in rdb: tool(['redis-check-rdb', str(f)])
        for f in manifests: check_manifest(f)
        for f in aof: check_aof(f)
        checks.append('redis-rdb-readonly-resp-manifest-check')
    elif kind == 'lnd':
        wallet = root / 'data/chain/bitcoin/testnet/wallet.db'
        channels = root / 'data/graph/testnet/channel.db'
        need(wallet.is_file() and channels.is_file())
        for f in (wallet, channels): tool(['bbolt', 'check', str(f)])
        checks.append('lnd-wallet-channel-bbolt-check')
    elif kind == 'bitcoin':
        chain = root / 'testnet3/chainstate'; current = (chain / 'CURRENT').read_text().strip()
        need(re.fullmatch(r'MANIFEST-\d+', current))
        need((chain / current).stat().st_size > 0 and list(chain.glob('*.ldb')))
        need(list((root / 'testnet3/blocks/index').glob('MANIFEST-*')))
        checks.append('bitcoin-testnet3-leveldb-layout')
    elif kind == 'tor':
        hostnames = list(root.rglob('hostname')); need(hostnames)
        for f in hostnames:
            name = f.read_text().strip(); need(re.fullmatch(r'[a-z2-7]{56}\.onion', name))
            address = base64.b32decode(name[:-6].upper())
            need(address[-1] == 3)
            need(address[32:34] == hashlib.sha3_256(b'.onion checksum' + address[:32] + address[-1:]).digest()[:2])
            pub = (f.parent / 'hs_ed25519_public_key').read_bytes()
            sec = (f.parent / 'hs_ed25519_secret_key').read_bytes()
            need(pub[:32] == b'== ed25519v1-public: type0 ==\x00\x00\x00' and pub[32:] == address[:32])
            need(len(sec) == 96 and sec[:32] == b'== ed25519v1-secret: type0 ==\x00\x00\x00')
        checks.append('tor-v3-address-public-key-binding')
    elif kind == 'vault':
        blobs = list((root / 'shares').glob('share-*.bin')); need(blobs)
        for f in blobs:
            need(re.fullmatch(r'share-[0-9a-f]{64}\.bin', f.name) and f.stat().st_size >= 44)
        for f in ('economy.json',):
            if (root / f).exists(): need(isinstance(json.loads((root / f).read_text()), dict))
        checks.append('vault-encrypted-envelope-layout-only')  # No AEAD decryption or signer boot.
    else: raise RuntimeError('unknown probe kind')
    need(inventory(root) == before)
    print(json.dumps({'status': 'passed', 'treeDigest': digest(before), 'filesRead': len(before), 'bytesRead': sum(x['size'] for x in before), 'checks': checks}, sort_keys=True))
except Exception:
    print('offline probe failed; no data disclosed', file=sys.stderr)
    sys.exit(78)
'''

def need(ok, msg):
    if not ok: raise RuntimeError(msg)
def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def digest(x): return 'sha256:' + hashlib.sha256(canonical(x)).hexdigest()
def now(): return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def kind(s):
    pvc = s['persistentVolumeClaim']
    for x in ('postgres', 'redis', 'bitcoin', 'lnd', 'tor'):
        if pvc.endswith('-' + x + '-0'): return x
    return 'vault'

p = argparse.ArgumentParser(description='Restore ten CSI snapshots into a NEW isolated namespace and verify offline. attest signs only the exact approved, live revalidated check run.')
p.add_argument('mode', choices=('check', 'attest'))
p.add_argument('--context', required=True)
p.add_argument('--request', required=True)
p.add_argument('--namespace', required=True, help='Operator-designated unique namespace starting kerosene-restore-')
p.add_argument('--output-dir', required=True, help='New check directory, or existing successful check directory for attest; outside repository')
p.add_argument('--isolation-approval', required=True, help='External JSON binding request, namespace, image, StorageClass and CSI/CNI operator approval')
p.add_argument('--profiles', required=True, help='External JSON with expected tree digests for all ten quiesced source volumes')
p.add_argument('--timeout', type=int, default=1800)
p.add_argument('--approve-evidence-digest', help='attest only: exact canonical evidence digest reviewed by operator')
p.add_argument('--operator', help='attest only: approving operator identity')
p.add_argument('--provider', help='attest only: trusted provider identifier')
p.add_argument('--provider-key-file', help='attest only: external Ed25519 PEM private key; NEVER generated here or copied to Kubernetes')
p.add_argument('--provider-public-key-file', help='attest only: independently trusted DER public key as base64, same format as kerosene-stack')
p.add_argument('--expires-at', help='attest only: explicit UTC expiry, at most 24 hours from signing')
a = p.parse_args(sys.argv[2:])
k = [os.environ.get('KUBECTL', 'kubectl'), '--context', a.context, '--request-timeout=30s']
out = Path(a.output_dir)
run = a.namespace

def kub(*args, obj=None):
    r = subprocess.run(k + list(args), input=json.dumps(obj) if obj is not None else None, text=True, capture_output=True, timeout=40)
    need(r.returncode == 0, 'kubectl failed: ' + ' '.join(args[:4]) + ' (inspect events; sensitive stderr withheld)')
    return r.stdout
def get(resource, name=None, ns=None):
    return json.loads(kub(*((['-n', ns] if ns else []) + ['get', resource] + ([name] if name else []) + ['-o', 'json'])))
def create(obj): return json.loads(kub('create', '-f', '-', '-o', 'json', obj=obj))
def save(name, obj):
    with (out / name).open('x', encoding='utf-8') as f:
        json.dump(obj, f, sort_keys=True, indent=2); f.write('\n')
def meta(name, namespace=None):
    m = {'name': name, 'labels': {LABEL: run, 'app.kubernetes.io/managed-by': 'staging-restore-check'}}
    if namespace: m['namespace'] = namespace
    return m
def wait(resource, name, ns, predicate):
    deadline = time.monotonic() + a.timeout
    while True:
        obj = get(resource, name, ns)
        need(not obj['metadata'].get('deletionTimestamp') and not obj.get('status', {}).get('error'), 'resource deleted or CSI error: ' + name)
        need(obj.get('status', {}).get('phase') != 'Failed', 'probe/PVC failed: ' + name)
        if predicate(obj): return obj
        need(time.monotonic() < deadline, 'readiness/probe timeout: ' + name)
        time.sleep(2)
def binding(snapshot, content):
    ref = content['spec']['volumeSnapshotRef']; m = snapshot['metadata']
    need((ref.get('namespace'), ref.get('name'), ref.get('uid')) == (m['namespace'], m['name'], m['uid']), 'snapshot/content binding mismatch')
    need(content['spec']['deletionPolicy'] == 'Retain' and content['status'].get('readyToUse') is True and not content['status'].get('error') and not content['metadata'].get('deletionTimestamp'), 'content must be ready and retained')
def source(s):
    snap = get('volumesnapshots.snapshot.storage.k8s.io', s['volumeSnapshotName'], s['namespace'])
    m, st = snap['metadata'], snap['status']
    need(m['uid'] == s['volumeSnapshotUid'] and m['creationTimestamp'] == s['creationTimestamp'] and not m.get('deletionTimestamp'), 'source snapshot identity changed')
    need(snap['spec'] == {'source': {'persistentVolumeClaimName': s['persistentVolumeClaim']}, 'volumeSnapshotClassName': s['volumeSnapshotClassName']}, 'source snapshot spec mismatch')
    need(st.get('readyToUse') is True and not st.get('error') and st['boundVolumeSnapshotContentName'] == s['boundVolumeSnapshotContentName'] and st.get('restoreSize') == s['restoreSize'], 'source snapshot readiness/size/content mismatch')
    c = get('volumesnapshotcontents.snapshot.storage.k8s.io', s['boundVolumeSnapshotContentName'])
    binding(snap, c)
    need(c['spec']['driver'] == approval['driver'] and c['status'].get('snapshotHandle'), 'source CSI driver/handle mismatch')
    need(c['spec'].get('sourceVolumeMode') == 'Filesystem' and c['spec'].get('source', {}).get('volumeHandle'), 'explicit filesystem mode and original dynamic volume handle required; block/unknown mode unsupported')
    return c
def pod_spec(i, config):
    uid = {'postgres': 70, 'redis': 999, 'bitcoin': 1000, 'tor': 1000, 'lnd': 0, 'vault': 65532}[config['kind']]
    return {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False, 'serviceAccountName': 'restore-check', 'hostNetwork': False, 'hostPID': False, 'hostIPC': False, 'activeDeadlineSeconds': a.timeout, 'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{'name': 'probe', 'image': approval['probeImage'], 'imagePullPolicy': 'IfNotPresent', 'command': ['python3', '-c', PROBE, json.dumps(config, sort_keys=True)], 'securityContext': {'runAsUser': uid, 'runAsGroup': uid, 'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}, 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '2', 'memory': '1Gi'}}, 'volumeMounts': [{'name': 'data', 'mountPath': '/data', 'readOnly': True}]}], 'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'restore-' + str(i), 'readOnly': True}}]}
def subset(expected, actual):
    if isinstance(expected, dict): return isinstance(actual, dict) and all(key in actual and subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list): return isinstance(actual, list) and len(actual) == len(expected) and all(subset(x, y) for x, y in zip(expected, actual))
    return expected == actual
def isolation():
    namespace = get('namespace', run)
    need(namespace['metadata'].get('labels', {}).get(LABEL) == run and not namespace['metadata'].get('deletionTimestamp'), 'namespace identity/isolation changed')
    policies = get('networkpolicies', ns=run)['items']
    need(len(policies) == 1 and policies[0]['metadata']['name'] == 'deny-all' and policies[0]['spec'] == {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}, 'only default-deny ingress/egress policy is permitted')
    for resource in ('secrets', 'services', 'endpoints', 'endpointslices', 'ingresses', 'deployments', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs', 'rolebindings', 'roles'):
        need(not get(resource, ns=run)['items'], 'unexpected resource in isolated namespace: ' + resource)
    sa = get('serviceaccount', 'restore-check', run)
    need(sa.get('automountServiceAccountToken') is False and not sa.get('secrets') and not sa.get('imagePullSecrets'), 'restore service account acquired credentials')
    return {'namespaceUid': namespace['metadata']['uid'], 'policyUid': policies[0]['metadata']['uid'], 'serviceAccountUid': sa['metadata']['uid']}
def observed(i, s, config):
    c = source(s)
    alias = get('volumesnapshots.snapshot.storage.k8s.io', 'import-' + str(i), run)
    imported = get('volumesnapshotcontents.snapshot.storage.k8s.io', run + '-' + str(i))
    binding(alias, imported)
    need(alias['spec'] == {'source': {'volumeSnapshotContentName': run + '-' + str(i)}, 'volumeSnapshotClassName': s['volumeSnapshotClassName']} and alias['status'].get('readyToUse') is True and alias['status'].get('boundVolumeSnapshotContentName') == imported['metadata']['name'], 'imported snapshot mismatch')
    need(imported['spec']['source'] == {'snapshotHandle': c['status']['snapshotHandle']} and imported['spec']['driver'] == c['spec']['driver'] and imported['spec'].get('sourceVolumeMode') == 'Filesystem', 'imported backend handle/driver/mode mismatch')
    pvc = get('pvc', 'restore-' + str(i), run)
    need(pvc['status'].get('phase') == 'Bound' and pvc['spec'].get('volumeMode', 'Filesystem') == 'Filesystem' and pvc['spec']['storageClassName'] == approval['storageClass'] and pvc['spec']['dataSource'] == {'apiGroup': 'snapshot.storage.k8s.io', 'kind': 'VolumeSnapshot', 'name': 'import-' + str(i)}, 'restore PVC binding/dataSource mismatch')
    pv = get('pv', pvc['spec']['volumeName']); ref = pv['spec']['claimRef']
    need((ref['namespace'], ref['name'], ref['uid']) == (run, pvc['metadata']['name'], pvc['metadata']['uid']) and pv['spec']['csi']['driver'] == approval['driver'], 'restore PV binding/driver mismatch')
    restored_handle = pv['spec']['csi'].get('volumeHandle')
    need(restored_handle and restored_handle not in {original['spec']['source']['volumeHandle'] for original in source_contents}, 'restore must provision a distinct backend volume, never reuse any source')
    pod = get('pod', 'probe-' + str(i), run)
    need(subset(pod_spec(i, config), pod['spec']) and not pod['spec'].get('initContainers') and not pod['spec'].get('ephemeralContainers') and not pod['spec'].get('imagePullSecrets'), 'probe pod mutated or credentials injected')
    container = pod['spec']['containers'][0]
    need(set(container) <= {'name', 'image', 'imagePullPolicy', 'command', 'securityContext', 'resources', 'volumeMounts', 'terminationMessagePath', 'terminationMessagePolicy'}, 'unexpected probe container fields (env/ports/lifecycle/etc)')
    security = container['securityContext']
    need(not security.get('privileged', False) and security.get('procMount', 'Default') == 'Default' and not security.get('capabilities', {}).get('add'), 'probe acquired additional privileges')
    statuses = pod['status'].get('containerStatuses', [])
    need(pod['status'].get('phase') == 'Succeeded' and len(statuses) == 1 and statuses[0].get('restartCount', 0) == 0 and statuses[0]['state']['terminated']['exitCode'] == 0, 'offline probe did not complete successfully')
    log = kub('-n', run, 'logs', pod['metadata']['name'], '-c', 'probe')
    result = json.loads(log)
    need(result.get('status') == 'passed' and result.get('treeDigest') == config['expectedTreeDigest'] and result.get('filesRead', 0) > 0 and result.get('bytesRead', 0) > 0 and 'approved-tree-sha256' in result.get('checks', []), 'probe output invalid')
    return {'target': s['namespace'] + '/' + s['persistentVolumeClaim'], 'sourceContentUid': c['metadata']['uid'], 'backendHandleDigest': 'sha256:' + hashlib.sha256(c['status']['snapshotHandle'].encode()).hexdigest(), 'restoredVolumeHandleDigest': 'sha256:' + hashlib.sha256(restored_handle.encode()).hexdigest(), 'importSnapshotUid': alias['metadata']['uid'], 'importContentUid': imported['metadata']['uid'], 'pvcUid': pvc['metadata']['uid'], 'pvUid': pv['metadata']['uid'], 'podUid': pod['metadata']['uid'], 'podSpecDigest': digest(pod['spec']), 'containerImageId': statuses[0]['imageID'], 'finishedAt': statuses[0]['state']['terminated']['finishedAt'], 'logDigest': 'sha256:' + hashlib.sha256(log.encode()).hexdigest(), 'result': result}

try:
    os.umask(0o077)
    need(re.fullmatch(r'kerosene-restore-[a-z0-9][a-z0-9-]{2,30}', run), 'namespace must be a unique operator-designated kerosene-restore- DNS label')
    need(1 <= a.timeout <= 86400, 'invalid timeout')
    repository = Path(sys.argv[1]).resolve().parents[2]
    need(not out.resolve().is_relative_to(repository), 'evidence output directory must be outside the repository')
    request = load(a.request); approval = load(a.isolation_approval); profiles = load(a.profiles)
    need(request.get('schema') == 'kerosene.snapshot-attestation-request/v1' and request.get('environment') == 'staging-cell' and request.get('kind') == 'VolumeSnapshotAttestationRequest', 'unexpected request schema/environment/kind')
    need(request.get('attestation') == {'externalSignerRequired': True, 'status': 'unsigned-request'} and request.get('selection', {}).get('namespaces') == list(NS), 'unexpected request attestation/scope')
    snapshots = request['snapshots']
    need(len(snapshots) == 10 and {(s['namespace'], s['persistentVolumeClaim']) for s in snapshots} == TARGETS, 'request must cover exactly ten canonical PVCs')
    need(len({s['volumeSnapshotUid'] for s in snapshots}) == 10 and len({s['boundVolumeSnapshotContentName'] for s in snapshots}) == 10, 'duplicate snapshot/content identities')
    need(snapshots == sorted(snapshots, key=lambda s: (s['namespace'], s['persistentVolumeClaim'], s['volumeSnapshotName'], s['volumeSnapshotUid'])) and digest(snapshots) == request['snapshotSetDigest'], 'snapshot digest/order mismatch')
    need(all(s['readyToUse'] is True and s['restoreSize'] and s['volumeSnapshotClassName'] for s in snapshots), 'ready snapshots, explicit class and restore sizes required')
    need(re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,127}', request['release']['id']) and re.fullmatch(r'sha256:[0-9a-f]{64}', request['release']['lockDigest']), 'invalid release binding')
    # Reproduce the complete request through the existing collector; no schema/collector edits
    # and no trusting a locally fabricated request with merely a matching self-digest.
    collector_args = ['bash', str(Path(sys.argv[1]) / 'collect-staging-volumesnapshot-attestation-request.sh'), '--context', a.context, '--release-id', request['release']['id'], '--release-lock-digest', request['release']['lockDigest'], '--requested-at', request['requestedAt']]
    if request['selection']['labelSelector']:
        collector_args += ['--snapshot-selector', request['selection']['labelSelector']]
    collection = subprocess.run(collector_args, capture_output=True, text=True, timeout=90)
    need(collection.returncode == 0 and json.loads(collection.stdout) == request, 'request differs from complete live collector output')
    need(approval['namespace'] == run and approval['requestDigest'] == digest(request) and approval['context'] == a.context, 'isolation approval does not bind context/namespace/request')
    need(approval.get('approvedBy') and approval.get('changeId') and approval.get('networkIsolationVerified') is True and approval.get('retainedHandleImportSupported') is True and approval.get('admissionAllowsOfflineProbeOnly') is True, 'explicit CSI import, CNI isolation and admission-boundary approval required')
    need(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', approval['probeImage']), 'probe image must be operator-approved and digest-pinned')
    targets = approval['egressTargets']
    import ipaddress
    need(len(targets) >= 3, 'provide reachable API, DNS and Vault network endpoints for negative egress probes')
    for e in targets:
        need(ipaddress.ip_address(e['ip']).version == 4 and 1 <= e['port'] <= 65535, 'invalid numeric egress endpoint')
    need(set(profiles) == {s['namespace'] + '/' + s['persistentVolumeClaim'] for s in snapshots}, 'profiles must bind all ten target volumes')
    configs = []
    for s in snapshots:
        profile = profiles[s['namespace'] + '/' + s['persistentVolumeClaim']]
        need(set(profile) == {'expectedTreeDigest'} and re.fullmatch(r'sha256:[0-9a-f]{64}', profile['expectedTreeDigest']), 'profile requires an independently approved quiesced-source tree digest')
        configs.append({'kind': kind(s), 'expectedTreeDigest': profile['expectedTreeDigest'], 'egressTargets': targets, 'timeout': a.timeout})
    storage = get('storageclass', approval['storageClass'])
    need(storage['provisioner'] == approval['driver'], 'restore storage class has wrong CSI driver')
    source_contents = [source(s) for s in snapshots]
    for s in snapshots:
        klass = get('volumesnapshotclasses.snapshot.storage.k8s.io', s['volumeSnapshotClassName'])
        need(klass['driver'] == approval['driver'] and klass['deletionPolicy'] == 'Retain', 'snapshot class no longer retained or driver changed')
    if a.mode == 'check':
        need(not any((a.provider_key_file, a.approve_evidence_digest, a.operator, a.provider, a.provider_public_key_file, a.expires_at)), 'signing arguments are permitted only in separate attest mode after operator review')
        out.mkdir(mode=0o700)
        existing = json.loads(kub('get', 'namespace', run, '--ignore-not-found', '-o', 'json') or 'null')
        need(existing is None, 'restore namespace already exists; choose a fresh unique namespace')
        save('plan.json', {'requestDigest': digest(request), 'approvalDigest': digest(approval), 'profilesDigest': digest(profiles), 'context': a.context, 'namespace': run, 'probeCodeDigest': 'sha256:' + hashlib.sha256(PROBE.encode()).hexdigest(), 'timeout': a.timeout})
        create({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': dict(meta(run), labels={**meta(run)['labels'], 'pod-security.kubernetes.io/enforce': 'baseline'})})
        create({'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': meta('deny-all', run), 'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}})
        create({'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': meta('restore-check', run), 'automountServiceAccountToken': False})
        isolation()
        # Separate content aliases preserve original namespace bindings. Retain prevents deletion
        # of an alias from deleting the shared backend snapshot. Backend support is explicitly approved.
        for i, (s, c) in enumerate(zip(snapshots, source_contents)):
            create({'apiVersion': 'snapshot.storage.k8s.io/v1', 'kind': 'VolumeSnapshotContent', 'metadata': meta(run + '-' + str(i)), 'spec': {'deletionPolicy': 'Retain', 'driver': c['spec']['driver'], 'volumeSnapshotClassName': s['volumeSnapshotClassName'], 'sourceVolumeMode': 'Filesystem', 'source': {'snapshotHandle': c['status']['snapshotHandle']}, 'volumeSnapshotRef': {'name': 'import-' + str(i), 'namespace': run}}})
            create({'apiVersion': 'snapshot.storage.k8s.io/v1', 'kind': 'VolumeSnapshot', 'metadata': meta('import-' + str(i), run), 'spec': {'volumeSnapshotClassName': s['volumeSnapshotClassName'], 'source': {'volumeSnapshotContentName': run + '-' + str(i)}}})
            wait('volumesnapshots.snapshot.storage.k8s.io', 'import-' + str(i), run, lambda x: x.get('status', {}).get('readyToUse') is True)
            create({'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': meta('restore-' + str(i), run), 'spec': {'accessModes': ['ReadWriteOnce'], 'volumeMode': 'Filesystem', 'storageClassName': approval['storageClass'], 'resources': {'requests': {'storage': s['restoreSize']}}, 'dataSource': {'apiGroup': 'snapshot.storage.k8s.io', 'kind': 'VolumeSnapshot', 'name': 'import-' + str(i)}}})
            isolation()
            # Create consumer before waiting for Bound: supports WaitForFirstConsumer storage.
            pod = create({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta('probe-' + str(i), run), 'spec': pod_spec(i, configs[i])})
            need(subset(pod_spec(i, configs[i]), pod['spec']) and not pod['spec'].get('initContainers'), 'admission mutated probe')
            wait('pods', 'probe-' + str(i), run, lambda x: x.get('status', {}).get('phase') == 'Succeeded')
            record = observed(i, s, configs[i])
            save('probe-' + str(i) + '.json', record)
            print('Offline probe passed: ' + record['target'], flush=True)
        boundary = isolation()
        need(len(get('pods', ns=run)['items']) == 10 and len(get('pvc', ns=run)['items']) == 10 and len(get('volumesnapshots.snapshot.storage.k8s.io', ns=run)['items']) == 10, 'unexpected restore resources')
        records = [observed(i, s, configs[i]) for i, s in enumerate(snapshots)]
        need(len({r['restoredVolumeHandleDigest'] for r in records}) == 10, 'restored backend volumes must be distinct')
        evidence = {'schema': 'kerosene.staging-restore-check/v1', 'context': a.context, 'namespace': run, 'boundary': boundary, 'requestDigest': digest(request), 'snapshotSetDigest': request['snapshotSetDigest'], 'approvalDigest': digest(approval), 'profilesDigest': digest(profiles), 'probeCodeDigest': 'sha256:' + hashlib.sha256(PROBE.encode()).hexdigest(), 'timeout': a.timeout, 'verifiedAt': now(), 'restoreTested': True, 'scope': 'offline filesystem integrity; no transactional, application replay, AEAD decrypt or signer recovery claim', 'probes': records}
        save('evidence.json', evidence)
        print('Unsigned evidence canonical digest: ' + digest(evidence) + '. Review before separate attest command. Resources retained for inspection.')
    else:
        need(all((a.approve_evidence_digest, a.operator, a.provider, a.provider_key_file, a.provider_public_key_file, a.expires_at)), 'attest requires exact evidence digest, operator, provider, external key/public key and explicit expiry')
        evidence = load(out / 'evidence.json')
        need(digest(evidence) == a.approve_evidence_digest and evidence['restoreTested'] is True, 'operator approval does not match exact successful evidence')
        need(evidence['context'] == a.context and evidence['namespace'] == run and evidence['requestDigest'] == digest(request) and evidence['snapshotSetDigest'] == request['snapshotSetDigest'] and evidence['approvalDigest'] == digest(approval) and evidence['profilesDigest'] == digest(profiles) and evidence['timeout'] == a.timeout and evidence['probeCodeDigest'] == 'sha256:' + hashlib.sha256(PROBE.encode()).hexdigest(), 'evidence input/run binding changed')
        verified = datetime.strptime(evidence['verifiedAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        current = datetime.now(timezone.utc)
        need(current - timedelta(hours=24) <= verified <= current, 'restore evidence must be from the last 24 hours')
        need(isolation() == evidence['boundary'], 'isolation resource identity changed')
        need(len(get('pods', ns=run)['items']) == 10 and len(get('pvc', ns=run)['items']) == 10 and len(get('volumesnapshots.snapshot.storage.k8s.io', ns=run)['items']) == 10, 'unexpected restore resources')
        need([observed(i, s, configs[i]) for i, s in enumerate(snapshots)] == evidence['probes'], 'live resource identities, commands, successful exits or probe outputs differ from approved evidence')
        need(re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,127}', a.provider), 'invalid provider identifier')
        expiry = datetime.strptime(a.expires_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        need(current < expiry <= current + timedelta(hours=24), 'expiry must be in the future and within 24 hours')
        key = Path(a.provider_key_file)
        need(key.is_absolute() and key.is_file() and not key.is_symlink() and key.stat().st_mode & 0o077 == 0, 'provider key must be an explicit absolute private external file with mode 0600 or stricter')
        repository = Path(sys.argv[1]).resolve().parents[2]
        need(not key.resolve().is_relative_to(repository) and not key.resolve().is_relative_to(out.resolve()), 'provider key must be external to repository and evidence directory')
        def openssl(argv):
            r = subprocess.run(['openssl'] + argv, capture_output=True, timeout=30)
            need(r.returncode == 0, 'external provider key/signature operation failed')
            return r.stdout
        pub = openssl(['pkey', '-in', str(key), '-pubout', '-outform', 'DER'])
        need(len(pub) == 44 and pub[:12] == bytes.fromhex('302a300506032b6570032100'), 'provider key must be Ed25519')
        need(pub == base64.b64decode(Path(a.provider_public_key_file).read_text().strip(), validate=True), 'provider key does not match independently trusted public key')
        receipt = {'schema': 'kerosene.snapshot-receipt/v2', 'releaseId': request['release']['id'], 'environment': 'staging-cell', 'status': 'verified', 'snapshotId': 'restore-' + digest(evidence).split(':')[1], 'provider': a.provider, 'snapshotSetDigest': request['snapshotSetDigest'], 'attestationRequestDigest': digest(request), 'restoreTested': True, 'restoreVerifiedAt': evidence['verifiedAt'], 'createdAt': now(), 'expiresAt': a.expires_at, 'providerPublicKeyDerBase64': base64.b64encode(pub).decode()}
        # pkeyutl Ed25519 needs a sized input file. Temporary payload contains no key material.
        with tempfile.TemporaryDirectory(prefix='staging-attest-') as tmp:
            payload = Path(tmp) / 'payload.json'
            payload.write_bytes(canonical(receipt))
            signature = openssl(['pkeyutl', '-sign', '-rawin', '-inkey', str(key), '-in', str(payload)])
        receipt['signatureBase64'] = base64.b64encode(signature).decode()
        # Signed audit record binds approving identity and exact receipt as well as evidence.
        audit = {'schema': 'kerosene.staging-restore-approval/v1', 'operator': a.operator, 'approvedAt': receipt['createdAt'], 'evidenceDigest': digest(evidence), 'receiptDigest': digest(receipt), 'requestDigest': digest(request)}
        with tempfile.TemporaryDirectory(prefix='staging-approval-') as tmp:
            payload = Path(tmp) / 'approval.json'; payload.write_bytes(canonical(audit))
            audit['signatureBase64'] = base64.b64encode(openssl(['pkeyutl', '-sign', '-rawin', '-inkey', str(key), '-in', str(payload)])).decode()
        save('approval.json', audit)
        save('receipt.json', receipt)
        print('Provider receipt created for exact approved live restore run: ' + str(out / 'receipt.json'))
except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
    print('fail closed: ' + str(exc), file=sys.stderr)
    print('No automatic cleanup or application/signer startup. Failed/partial checks never produce a receipt.', file=sys.stderr)
    sys.exit(78)
PY
