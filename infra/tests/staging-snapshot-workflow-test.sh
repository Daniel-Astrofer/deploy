#!/usr/bin/env bash
# FAKE KUBECTL CONTRACT TESTS. These NEVER prove CSI backups, recovery or CNI enforcement.
set -euo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 - "$REPO_ROOT" <<'PY'
import ast
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1])
create = repo / 'infra/kubernetes/scripts/create-staging-snapshots.sh'
restore = repo / 'infra/kubernetes/scripts/restore-staging-snapshots.sh'
def canonical(x): return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def digest(x): return 'sha256:' + hashlib.sha256(canonical(x)).hexdigest()
def write(path, value): path.write_text(json.dumps(value))

# This executable is generated ONLY inside a private temporary test directory.
# No Kubernetes connection is made; successful logs below are synthetic fixtures.
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
path = pathlib.Path(os.environ['FAKE_STATE']); db = json.loads(path.read_text())
args = sys.argv[1:]; ns = None
while args and args[0].startswith('--'): args = args[2:] if args[0] == '--context' else args[1:]
if args[:1] == ['-n']: ns, args = args[1], args[2:]
verb = args[0]; db['calls'].append([ns] + args)
aliases = {'pvc':'PersistentVolumeClaim','pv':'PersistentVolume','pod':'Pod','pods':'Pod','namespace':'Namespace','serviceaccount':'ServiceAccount','storageclass':'StorageClass','volumesnapshots.snapshot.storage.k8s.io':'VolumeSnapshot','volumesnapshotcontents.snapshot.storage.k8s.io':'VolumeSnapshotContent','volumesnapshotclasses.snapshot.storage.k8s.io':'VolumeSnapshotClass','deployments':'Deployment','deployment':'Deployment','statefulsets':'StatefulSet','statefulset':'StatefulSet','networkpolicies':'NetworkPolicy'}
def key(kind, name, namespace): return kind + '/' + str(namespace) + '/' + name
def put(obj):
    m = obj['metadata']; m.setdefault('uid', 'uid-' + str(m.get('namespace')) + '-' + m['name']); m.setdefault('resourceVersion','1'); m.setdefault('creationTimestamp','2026-10-01T00:00:00Z')
    db['objects'][key(obj['kind'],m['name'],m.get('namespace'))] = obj
def emit(obj): print(json.dumps(obj))
rc = 0
try:
    if verb == 'get':
        kind = aliases.get(args[1], args[1]); name = args[2] if len(args)>2 and not args[2].startswith('-') else None
        if name:
            obj = db['objects'].get(key(kind,name,ns))
            if obj is None:
                if '--ignore-not-found' not in args: rc = 1
            else: emit(obj)
        else:
            kinds = [aliases.get(x,x) for x in args[1].split(',')]
            items = [v for v in db['objects'].values() if v['kind'] in kinds and v['metadata'].get('namespace')==ns]
            if '-l' in args:
                label,value = args[args.index('-l')+1].split('=',1); items=[x for x in items if x['metadata'].get('labels',{}).get(label)==value]
            emit({'apiVersion':'snapshot.storage.k8s.io/v1' if kind=='VolumeSnapshot' else 'v1','kind':kind+'List','items':items})
    elif verb == 'scale':
        kind,name=args[1].split('/'); obj=db['objects'][key(aliases.get(kind,kind),name,ns)]; obj['spec']['replicas']=0
    elif verb == 'create':
        obj=json.load(sys.stdin); m=obj['metadata']; kind=obj['kind']; name=m['name']; ns=m.get('namespace')
        if key(kind,name,ns) in db['objects']: raise RuntimeError('AlreadyExists')
        put(obj)
        if kind=='VolumeSnapshot':
            if 'persistentVolumeClaimName' in obj['spec']['source']:
                content='content-'+name
                c={'apiVersion':'snapshot.storage.k8s.io/v1','kind':'VolumeSnapshotContent','metadata':{'name':content},'spec':{'deletionPolicy':'Retain','driver':'test.csi.invalid','sourceVolumeMode':'Filesystem','volumeSnapshotRef':{'name':name,'namespace':ns,'uid':m['uid']},'source':{'volumeHandle':'volume-'+obj['spec']['source']['persistentVolumeClaimName']}},'status':{'readyToUse':True,'snapshotHandle':'handle-'+name}}
                put(c)
            else:
                content=obj['spec']['source']['volumeSnapshotContentName']; c=db['objects'][key('VolumeSnapshotContent',content,None)]
                c['spec']['volumeSnapshotRef']['uid']=m['uid']; c['status']={'readyToUse':True,'snapshotHandle':c['spec']['source']['snapshotHandle']}
            obj['status']={'readyToUse':os.environ.get('FAKE_UNREADY')!='1','restoreSize':'1Gi','boundVolumeSnapshotContentName':content}
            if os.environ.get('FAKE_SNAPSHOT_ERROR')=='1':obj['status']['error']={'message':'synthetic CSI error'}
        elif kind=='PersistentVolumeClaim':
            pv='pv-'+name; obj['spec']['volumeName']=pv; obj['status']={'phase':'Bound'}
            put({'kind':'PersistentVolume','metadata':{'name':pv},'spec':{'csi':{'driver':'test.csi.invalid','volumeHandle':'restored-'+name},'claimRef':{'namespace':ns,'name':name,'uid':m['uid']}}})
        elif kind=='Pod':
            if os.environ.get('FAKE_OMITEMPTY')=='1':
                for field in ('hostNetwork','hostPID','hostIPC'):obj['spec'].pop(field,None)
            if os.environ.get('FAKE_HOST_NETWORK')=='1':obj['spec']['hostNetwork']=True
            config=json.loads(obj['spec']['containers'][0]['command'][3])
            result={'status':'passed','treeDigest':config['expectedTreeDigest'],'filesRead':2,'bytesRead':128,'checks':['full-file-read','approved-tree-sha256','read-only-mount','egress-denied']}
            db['logs'][name]=json.dumps(result,sort_keys=True)+'\n'
            exitcode=78 if os.environ.get('FAKE_PROBE_FAIL')=='1' else 0
            obj['status']={'phase':'Failed' if exitcode else 'Succeeded','containerStatuses':[{'restartCount':0,'imageID':'containerd://sha256:'+'a'*64,'state':{'terminated':{'exitCode':exitcode,'finishedAt':'2026-10-01T00:00:00Z'}}}]}
        elif kind=='NetworkPolicy' and os.environ.get('FAKE_OMITEMPTY')=='1':
            for field in ('ingress','egress'):obj['spec'].pop(field,None)
        emit(obj)
    elif verb=='logs': print(db['logs'][args[1]],end='')
    else: rc=1
except Exception:rc=1
path.write_text(json.dumps(db))
sys.exit(rc)
'''

with tempfile.TemporaryDirectory(prefix='FAKE-staging-workflow-') as tmp:
    t=Path(tmp); fake=t/'kubectl'; fake.write_text(FAKE); fake.chmod(0o700)
    state=t/'state.json'; env={**os.environ,'KUBECTL':str(fake),'FAKE_STATE':str(state)}
    objects={}
    def add(kind,name,spec=None,namespace=None,**extra):
        obj={'kind':kind,'apiVersion':'v1','metadata':{'name':name,'uid':'uid-'+str(namespace)+'-'+name,'resourceVersion':'1'},**extra}
        if namespace:obj['metadata']['namespace']=namespace
        if spec is not None:obj['spec']=spec
        objects[kind+'/'+str(namespace)+'/'+name]=obj
        return obj
    add('VolumeSnapshotClass','approved',driver='test.csi.invalid',deletionPolicy='Retain')
    add('StorageClass','restore',provisioner='test.csi.invalid')
    targets=[('kerosene-staging','data-staging-'+x+'-0') for x in ('bitcoin','lnd','postgres','redis','tor')]+[('kerosene-staging','vault-'+str(x)+'-data') for x in (1,2,3)]+[('kerosene-staging-vault',x) for x in ('data-vault-tor-0','vault-data')]
    for ns,name in targets:
        pvc=add('PersistentVolumeClaim',name,{'volumeName':'pv-'+name,'volumeMode':'Filesystem'},ns,status={'phase':'Bound'})
        add('PersistentVolume','pv-'+name,{'csi':{'driver':'test.csi.invalid'},'claimRef':{'namespace':ns,'name':name,'uid':pvc['metadata']['uid']}})
    for ns in ('kerosene-staging','kerosene-staging-vault'):add('Deployment','synthetic-vault',{'replicas':1},ns)
    initial={'objects':objects,'calls':[],'logs':{}}; write(state,initial)
    def command(script,args,ok=True,extra=None):
        result=subprocess.run(['bash',str(script)]+args,text=True,capture_output=True,env={**env,**(extra or {})})
        assert (result.returncode==0)==ok,(args,result.stdout,result.stderr)
        return result
    backup=t/'backup'; base=['--context','FAKE','--run-id','fake-run','--release-id','synthetic-release','--release-lock-digest','sha256:'+'0'*64,'--snapshot-class','approved','--output-dir',str(backup)]
    command(create,base,False)
    assert not backup.exists()
    command(create,base+['--approve-stop-staging'])
    req=json.loads((backup/'request.json').read_text());assert len(req['snapshots'])==10 and req['snapshotSetDigest']==digest(req['snapshots'])
    db=json.loads(state.read_text()); assert all(x['spec']['replicas']==0 for x in db['objects'].values() if x['kind']=='Deployment')
    assert not any('secret' in str(x).lower() for x in db['calls'])
    backup_state=json.loads(state.read_text())
    for rejection in ('Delete','wrong-driver','HPA'):
        bad=json.loads(json.dumps(initial))
        if rejection=='Delete':bad['objects']['VolumeSnapshotClass/None/approved']['deletionPolicy']='Delete'
        elif rejection=='wrong-driver':bad['objects']['PersistentVolume/None/pv-data-staging-postgres-0']['spec']['csi']['driver']='wrong.csi.invalid'
        else:bad['objects']['horizontalpodautoscalers/kerosene-staging/unsafe']={'kind':'horizontalpodautoscalers','metadata':{'name':'unsafe','namespace':'kerosene-staging'}}
        write(state,bad)
        rejected=t/('rejected-'+rejection)
        command(create,[str(rejected) if x==str(backup) else x for x in base]+['--approve-stop-staging'],False)
        assert not (rejected/'request.json').exists()
        assert not any('scale' in call for call in json.loads(state.read_text())['calls'])
    write(state,backup_state)
    ns='kerosene-restore-fake-one'; approval=t/'isolation.json'; profiles=t/'profiles.json'; checked=t/'checked'
    image='registry.invalid/offline-tools@sha256:'+'a'*64
    approval_data={'context':'FAKE','namespace':ns,'requestDigest':digest(req),'approvedBy':'synthetic-operator','changeId':'FAKE-ONLY','driver':'test.csi.invalid','storageClass':'restore','probeImage':image,'networkIsolationVerified':True,'retainedHandleImportSupported':True,'admissionAllowsOfflineProbeOnly':True,'egressTargets':[{'ip':'192.0.2.'+str(x),'port':443} for x in (1,2,3)]}
    write(approval,approval_data);write(profiles,{n+'/'+v:{'expectedTreeDigest':'sha256:'+'b'*64} for n,v in targets})
    restore_args=['--context','FAKE','--request',str(backup/'request.json'),'--namespace',ns,'--output-dir',str(checked),'--isolation-approval',str(approval),'--profiles',str(profiles)]
    command(restore,['check']+restore_args)
    evidence=json.loads((checked/'evidence.json').read_text());assert evidence['restoreTested'] and len(evidence['probes'])==10
    db=json.loads(state.read_text());success_state=json.loads(state.read_text())
    created=[x for x in db['objects'].values() if x['metadata'].get('namespace')==ns]
    assert {x['kind'] for x in created}=={'Pod','ServiceAccount','NetworkPolicy','VolumeSnapshot','PersistentVolumeClaim'}
    for obj in created:
        if obj['kind']=='Pod':
            spec=obj['spec'];assert not spec['automountServiceAccountToken'] and not spec['hostNetwork'] and spec['containers'][0]['volumeMounts'][0]['readOnly']
            assert len(spec['containers'])==1 and len(spec['volumes'])==1 and 'env' not in spec['containers'][0]
    assert not (checked/'receipt.json').exists()
    command(restore,['check']+restore_args,False)  # Existing output/namespace fail closed.
    write(state,backup_state)
    omitted=t/'api-omitempty'
    omitted_args=[str(omitted) if x==str(checked) else x for x in restore_args]
    command(restore,['check']+omitted_args,True,{'FAKE_OMITEMPTY':'1'})
    assert len(json.loads((omitted/'evidence.json').read_text())['probes'])==10
    write(state,backup_state)
    host_network=t/'api-host-network'
    command(restore,['check']+[str(host_network) if x==str(checked) else x for x in restore_args],False,{'FAKE_HOST_NETWORK':'1'})
    assert not (host_network/'evidence.json').exists()
    for failure in ('FAKE_PROBE_FAIL','FAKE_UNREADY','FAKE_SNAPSHOT_ERROR'):
        write(state,backup_state)
        target=t/failure
        args=[target.as_posix() if x==str(checked) else x for x in restore_args]
        command(restore,['check']+args+['--timeout','1'],False,{failure:'1'})
        assert not (target/'evidence.json').exists() and not (target/'receipt.json').exists()
    # Backup failure never produces a request.
    for failure in ('FAKE_UNREADY','FAKE_SNAPSHOT_ERROR'):
        write(state,initial)
        target=t/('backup-'+failure)
        args=[str(target) if x==str(backup) else x for x in base]
        command(create,args+['--approve-stop-staging','--timeout','1'],False,{failure:'1'})
        assert not (target/'request.json').exists()
    write(state,success_state)
    # Wrong digest, unsupported import and changed live identities are rejected before key access.
    key=t/'external-provider.pem';pub=t/'provider.b64'
    subprocess.run(['openssl','genpkey','-algorithm','Ed25519','-out',str(key)],check=True,capture_output=True);key.chmod(0o600)
    pub.write_text(base64.b64encode(subprocess.check_output(['openssl','pkey','-in',str(key),'-pubout','-outform','DER'])).decode())
    expiry=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    sign=['--operator','synthetic-operator','--provider','synthetic-provider','--provider-key-file',str(key),'--provider-public-key-file',str(pub),'--expires-at',expiry]
    command(restore,['attest']+restore_args+sign+['--approve-evidence-digest','sha256:'+'0'*64],False)
    trusted_pub=pub.read_text();pub.write_text(base64.b64encode(b'WRONG SYNTHETIC PUBLIC KEY').decode())
    command(restore,['attest']+restore_args+sign+['--approve-evidence-digest',digest(evidence)],False)
    pub.write_text(trusted_pub)
    modified=json.loads(state.read_text());modified['objects']['Pod/'+ns+'/probe-0']['metadata']['uid']='replacement';write(state,modified)
    command(restore,['attest']+restore_args+sign+['--approve-evidence-digest',digest(evidence)],False)
    assert not (checked/'receipt.json').exists()
    write(state,success_state)
    # Synthetic fake run can exercise cryptography, but conveys NO real restore authority.
    command(restore,['attest']+restore_args+sign+['--approve-evidence-digest',digest(evidence)])
    receipt=json.loads((checked/'receipt.json').read_text());signature=base64.b64decode(receipt.pop('signatureBase64'))
    assert receipt['snapshotId']=='restore-'+digest(evidence).split(':')[1] and receipt['attestationRequestDigest']==digest(req)
    payload=t/'payload';sig=t/'sig';der=t/'pub.der';payload.write_bytes(canonical(receipt));sig.write_bytes(signature);der.write_bytes(base64.b64decode(pub.read_text()))
    subprocess.run(['openssl','pkeyutl','-verify','-rawin','-pubin','-keyform','DER','-inkey',str(der),'-in',str(payload),'-sigfile',str(sig)],check=True,capture_output=True)
    # Execute the actual probe functions on ordinary SYNTHETIC files, not just fake logs.
    embedded=restore.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    tree=ast.parse(embedded);probe=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='PROBE' for x in node.targets))
    observer_names={'subset','pod_spec_matches','deny_all_spec'}
    observer_scope={}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in observer_names],type_ignores=[]),'<actual-restore-serialization>','exec'),observer_scope)
    deny=observer_scope['deny_all_spec']; match=observer_scope['pod_spec_matches']
    base_policy={'podSelector':{},'policyTypes':['Ingress','Egress']}
    assert deny(base_policy) and deny({**base_policy,'ingress':[],'egress':[]})
    for bad in ({**base_policy,'ingress':[{}]},{**base_policy,'egress':None},{**base_policy,'egress':{}},{**base_policy,'unexpected':True},{'podSelector':{}}):
        assert not deny(bad)
    spec={'hostNetwork':False,'hostPID':False,'hostIPC':False,'automountServiceAccountToken':False}
    assert match(spec,{'automountServiceAccountToken':False})
    for bad in ({},{'automountServiceAccountToken':0},{**spec,'hostNetwork':True},{**spec,'hostPID':None},{**spec,'hostIPC':0}):
        assert not match(spec,bad)
    functions=probe.split('\ntry:\n',1)[0];scope={};exec(functions,scope)
    files=t/'synthetic-files';files.mkdir();(files/'data').write_bytes(b'SYNTHETIC NONSECRET BYTES')
    records=scope['inventory'](files);assert records[0]['sha256']==hashlib.sha256(b'SYNTHETIC NONSECRET BYTES').hexdigest()
    aof=files/'appendonly.aof';aof.write_bytes(b'*1\r\n$5\r\nMULTI\r\n*3\r\n$3\r\nSET\r\n$1\r\nx\r\n$1\r\ny\r\n*1\r\n$4\r\nEXEC\r\n')
    scope['check_aof'](aof)
    for broken in (b'*1\r\n$5\r\nMULTI\r\n', b'*2\r\n$3\r\nSET\r\n$10\r\ntruncated', b'REDIS0009old-preamble', b'#TS:invalid\r\n'):
        aof.write_bytes(broken)
        try:scope['check_aof'](aof)
        except RuntimeError:pass
        else:raise AssertionError('malformed or unsupported AOF must fail closed')
    aof.write_bytes(b'*1\r\n$4\r\nPING\r\n')
    manifest=files/'appendonly.aof.manifest';manifest.write_text('file appendonly.aof seq 1 type b\n')
    scope['check_manifest'](manifest)
    manifest.write_text('file ../escape seq 1 type b\n')
    try:scope['check_manifest'](manifest)
    except RuntimeError:pass
    else:raise AssertionError('manifest traversal must fail closed')
    (files/'escape').symlink_to(t/'external-provider.pem')
    try:scope['inventory'](files)
    except RuntimeError:pass
    else:raise AssertionError('symlink must fail closed')
    # Run the literal probe on real synthetic Tor/Vault files. ONLY mount/network APIs
    # are mocked here; filesystem reads, expected hashes and format checks are actual.
    import contextlib, io, pathlib, socket
    original_path=pathlib.Path;original_socket=socket.socket;original_argv=sys.argv
    class Mounts:
        def read_text(self):return '/dev/SYNTHETIC /data ext4 ro 0 0\n'
    class BlockedSocket:
        def settimeout(self,_):pass
        def connect(self,_):raise OSError('SYNTHETIC blocked connection')
        def close(self):pass
    def run_probe(root,which,ok):
        expected=digest(scope['inventory'](root))
        cfg={'kind':which,'expectedTreeDigest':expected,'egressTargets':[{'ip':'192.0.2.1','port':443}],'timeout':10}
        pathlib.Path=lambda value: root if str(value)=='/data' else Mounts() if str(value)=='/proc/mounts' else original_path(value)
        socket.socket=BlockedSocket;sys.argv=['SYNTHETIC',json.dumps(cfg)]
        stdout=io.StringIO();stderr=io.StringIO();passed=True
        try:
            with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):exec(probe,{})
        except SystemExit as e:passed=e.code==0
        finally:pathlib.Path=original_path;socket.socket=original_socket;sys.argv=original_argv
        assert passed==ok,(which,stdout.getvalue(),stderr.getvalue())
        if ok:assert json.loads(stdout.getvalue())['treeDigest']==expected
    tor=t/'SYNTHETIC-tor';service=tor/'synthetic_service';service.mkdir(parents=True)
    public=bytes(range(32));version=b'\x03';checksum=hashlib.sha3_256(b'.onion checksum'+public+version).digest()[:2]
    (service/'hostname').write_text(base64.b32encode(public+checksum+version).decode().lower()+'.onion\n')
    (service/'hs_ed25519_public_key').write_bytes(b'== ed25519v1-public: type0 ==\x00\x00\x00'+public)
    (service/'hs_ed25519_secret_key').write_bytes(b'== ed25519v1-secret: type0 ==\x00\x00\x00'+b'SYNTHETIC-NOT-A-REAL-KEY'.ljust(64,b'x'))
    run_probe(tor,'tor',True)
    (service/'hostname').write_text('a'*56+'.onion\n');run_probe(tor,'tor',False)
    vault=t/'SYNTHETIC-vault';shares=vault/'shares';shares.mkdir(parents=True)
    envelope=shares/('share-'+'c'*64+'.bin');envelope.write_bytes(b'SYNTHETIC-NOT-AN-ENCRYPTED-SHARE'.ljust(64,b'x'))
    (vault/'economy.json').write_text('{}');run_probe(vault,'vault',True)
    envelope.write_bytes(b'truncated');run_probe(vault,'vault',False)
    # Compiling the embedded programs catches Python syntax regressions in Bash heredocs.
    for script in (create,restore):compile(script.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0],str(script),'exec')
print('PASS: FAKE KUBECTL contract/negative tests and synthetic file/Ed25519 checks ONLY. No real CSI/CNI/application recovery was tested.')
PY
