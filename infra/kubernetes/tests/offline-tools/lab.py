"""Real disposable qualification; never a provider receipt or a production release gate."""
import argparse
import ast
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import yaml

CLUSTER='kerosene-cell-audit-20261001'
CONTEXT='kind-'+CLUSTER
NODE=CLUSTER+'-control-plane'
CONFIG=Path('/tmp/kerosene-cell-verification.EHFAhI/kubeconfig')
CLASS='synthetic-audit-retain'
STORAGE='synthetic-audit-hostpath'
LAB='kerosene-csi-qualification'
LABEL='qualification.kerosene.io/run'
CLASSIFICATION='REAL-DISPOSABLE-SYNTHETIC-LAB-NOT-RELEASE-EVIDENCE'
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[3]
SNAP='a6a3d9d6b5f6f1e6fa0d311148b696556e4499e8'
HOST='8b88d04ec388312df1b910bf1cb48926c63c2660'
ROUTER='85e429e9c72b2bc7de93b5f1bcce20e7c924386d'
NS=('kerosene-staging','kerosene-staging-vault')
TARGETS=sorted([(NS[0],'data-staging-'+x+'-0') for x in ('bitcoin','lnd','postgres','redis','tor')]+[(NS[0],'vault-'+str(x)+'-data') for x in (1,2,3)]+[(NS[1],x) for x in ('data-vault-tor-0','vault-data')])

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def digest(x): return 'sha256:'+hashlib.sha256(canonical(x)).hexdigest()
def need(ok,msg):
    if not ok: raise RuntimeError(msg)
def stamp(): return datetime.now(timezone.utc).isoformat()

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--kubeconfig',required=True)
p.add_argument('--output-dir',required=True)
p.add_argument('--phase',choices=('preflight','prepare','install','repair-network','repair-admission','repair-admission-server','build','release-import-staging','recover-collector','negative-tools','packets','qualify','revalidate-stock'),required=True)
p.add_argument('--run-id',default='audit-20261002')
p.add_argument('--reviewed-plan-digest')
p.add_argument('--timeout',type=int,default=300)
a=p.parse_args()
os.umask(0o077)
out=Path(a.output_dir).resolve()
need(Path(a.kubeconfig).resolve()==CONFIG and not Path(a.kubeconfig).is_symlink(),'only exact authorized kubeconfig allowed')
need(not out.is_relative_to(Path('/home/astrofer/workspaces')),'evidence must be outside all checkouts')
need(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,20}',a.run_id),'invalid lab run ID')
need(30<=a.timeout<=1800,'bounded timeout required')
out.mkdir(mode=0o700,exist_ok=True)
need(out.stat().st_mode & 0o077 == 0,'evidence directory must be private')
K=['kubectl','--kubeconfig',str(CONFIG),'--context',CONTEXT,'--request-timeout=30s']
ENV={**os.environ,'KUBECONFIG':str(CONFIG),'KUBECTL':str(HERE/'kubectl-lab.py'),'KEROSENE_LAB_API_EVIDENCE':str(out/'raw-api-lists')}

def save(name,obj):
    path=out/name
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    path.write_bytes(json.dumps(obj,sort_keys=True,indent=2).encode()+b'\n')
def load(name): return json.loads((out/name).read_text())
def cmd(argv,data=None,ok=True,timeout=60):
    r=subprocess.run(argv,input=data,text=True,capture_output=True,env=ENV,timeout=timeout)
    need(not ok or r.returncode==0,'command failed: '+' '.join(argv[:7])+'\n'+r.stderr[-3000:])
    return r
def kub(*args,obj=None,ok=True): return cmd(K+list(args),json.dumps(obj) if obj is not None else None,ok)
def get(resource,name=None,ns=None):
    return json.loads(kub(*((['-n',ns] if ns else [])+['get',resource]+([name] if name else [])+['-o','json'])).stdout)
def meta(name,ns=None):
    m={'name':name,'labels':{LABEL:a.run_id,'app.kubernetes.io/managed-by':'synthetic-kind-qualification'}}
    if ns: m['namespace']=ns
    return m
def create(obj):
    m=obj['metadata']; resource=obj['kind'].lower()
    r=kub(*((['-n',m['namespace']] if m.get('namespace') else [])+['get',resource,m['name'],'--ignore-not-found','-o','json']))
    if r.stdout.strip():
        current=json.loads(r.stdout)
        need(current['metadata'].get('labels',{}).get(LABEL)==a.run_id,'refuse existing unowned resource: '+m['name'])
        expected=copy.deepcopy(obj.get('spec',{})); actual=copy.deepcopy(current.get('spec',{}))
        if obj['kind']=='NetworkPolicy':
            for field in ('ingress','egress'):
                if expected.get(field)==[]: expected.pop(field)
                if actual.get(field)==[]: actual.pop(field)
        need('spec' not in obj or subset(expected,actual),'existing resource spec differs: '+m['name'])
        return current
    return json.loads(kub('create','-f','-','-o','json',obj=obj).stdout)
def subset(x,y):
    if isinstance(x,dict): return isinstance(y,dict) and all((k in y and subset(v,y[k])) or (k in ('hostNetwork','hostPID','hostIPC') and v is False and k not in y) for k,v in x.items())
    if isinstance(x,list): return isinstance(y,list) and len(x)==len(y) and all(subset(v,w) for v,w in zip(x,y))
    return x==y
def wait(resource,name,ns,fn):
    deadline=time.monotonic()+a.timeout
    while True:
        o=get(resource,name,ns)
        need(not o.get('status',{}).get('error') and not o['metadata'].get('deletionTimestamp'),'CSI error/deleted: '+name)
        need(o.get('status',{}).get('phase')!='Failed','failed resource: '+name)
        if fn(o): return o
        need(time.monotonic()<deadline,'timeout: '+resource+'/'+name)
        time.sleep(2)
def namespace(name): return create({'apiVersion':'v1','kind':'Namespace','metadata':meta(name)})
def sc(name,mode):
    return {'apiVersion':'storage.k8s.io/v1','kind':'StorageClass','metadata':meta(name),'provisioner':'hostpath.csi.k8s.io','reclaimPolicy':'Retain','volumeBindingMode':mode}
def pvc(name,ns,storage=STORAGE,snapshot=None):
    spec={'storageClassName':storage,'volumeMode':'Filesystem','accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':'128Mi'}}}
    if snapshot: spec['dataSource']={'apiGroup':'snapshot.storage.k8s.io','kind':'VolumeSnapshot','name':snapshot}
    return {'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':meta(name,ns),'spec':spec}
def pod(name,ns,code,image,claim=None,uid=0,readonly=True,args=None):
    c={'name':'lab','image':image,'imagePullPolicy':'IfNotPresent','command':['python3','-c',code]+(args or []),'securityContext':{'runAsUser':uid,'runAsGroup':uid,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':readonly,'capabilities':{'drop':['ALL']}},'resources':{'requests':{'cpu':'100m','memory':'64Mi'},'limits':{'cpu':'2','memory':'1Gi'}}}
    spec={'restartPolicy':'Never','automountServiceAccountToken':False,'enableServiceLinks':False,'activeDeadlineSeconds':a.timeout,'securityContext':{'seccompProfile':{'type':'RuntimeDefault'}},'containers':[c]}
    if claim:
        spec['volumes']=[{'name':'data','persistentVolumeClaim':{'claimName':claim,'readOnly':readonly}}]
        c['volumeMounts']=[{'name':'data','mountPath':'/data','readOnly':readonly}]
    return {'apiVersion':'v1','kind':'Pod','metadata':meta(name,ns),'spec':spec}
def runpod(obj,record):
    m=obj['metadata']; create(obj)
    result=wait('pod',m['name'],m['namespace'],lambda x:x.get('status',{}).get('phase')=='Succeeded')
    log=kub('-n',m['namespace'],'logs',m['name']).stdout
    save(record+'.json',result); (out/(record+'.log')).write_text(log)
    return log
def identity():
    c=json.loads(cmd(['kubectl','--kubeconfig',str(CONFIG),'config','view','-o','json']).stdout)
    need(len(c['clusters'])==len(c['contexts'])==1 and c['contexts'][0]['name']==CONTEXT and c['contexts'][0]['context']['cluster']==CONTEXT,'refuse other cluster/context')
    need(c['clusters'][0]['cluster']['server']=='https://127.0.0.1:46523','authorized API address changed')
    nodes=get('nodes')['items']; need(len(nodes)==1 and nodes[0]['metadata']['name']==NODE,'unexpected node inventory')
    node=json.loads(cmd(['docker','inspect',NODE]).stdout)[0]
    need(node['Config']['Labels'].get('io.x-k8s.kind.cluster')==CLUSTER,'container identity mismatch')
    o={'context':CONTEXT,'node':NODE,'nodeUid':nodes[0]['metadata']['uid'],'clusterUid':get('namespace','kube-system')['metadata']['uid'],'containerId':node['Id'],'classification':CLASSIFICATION}
    if (out/'cluster-identity.json').exists(): need(load('cluster-identity.json')==o,'lab identity replaced')
    else: save('cluster-identity.json',o)

def upstream(owner,ref,path,assets):
    url='https://raw.githubusercontent.com/'+owner+'/'+ref+'/'+path
    data=urllib.request.urlopen(url,timeout=30).read()
    name='upstream/'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.yaml'
    (out/name).parent.mkdir(mode=0o700,exist_ok=True); (out/name).write_bytes(data)
    assets.append({'url':url,'file':name,'sha256':hashlib.sha256(data).hexdigest()})
    return [x for x in yaml.safe_load_all(data) if x]
def pin(image):
    if '@sha256:' in image: return image
    result=cmd(['docker','buildx','imagetools','inspect',image],timeout=90).stdout
    match=re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})$',result,re.M)
    need(match,'cannot resolve image digest: '+image)
    return image.split(':')[0]+'@'+match[1]
def prepare():
    need(not (out/'install-plan.json').exists(),'plan already prepared; inspect retained plan, do not redownload')
    assets=[]; objects=[]
    objects+=upstream('cloudnativelabs/kube-router',ROUTER,'daemonset/kube-router-firewall-daemonset.yaml',assets)
    # Keep Kindnet. Remove all upstream init shell and CNI writes; use in-cluster API credentials only for this infrastructure agent.
    objects=[o for o in objects if o['kind']=='DaemonSet']
    router=objects[0]['spec']['template']['spec']; router.pop('initContainers'); router.pop('hostPID')
    c=router['containers'][0]; c['image']='docker.io/cloudnativelabs/kube-router:v2.5.0'
    c['args']=['--run-router=false','--run-firewall=true','--run-service-proxy=false','--run-loadbalancer=false','--iptables-sync-period=1s']
    c['env']=[c['env'][0]]; c['volumeMounts']=[{'name':'xtables-lock','mountPath':'/run/xtables.lock'}]
    router['volumes']=[{'name':'xtables-lock','hostPath':{'path':'/run/xtables.lock','type':'FileOrCreate'}}]
    router['serviceAccountName']='synthetic-kube-router'
    objects += [
      {'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('synthetic-kube-router','kube-system')},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':meta('synthetic-kube-router'),'rules':[{'apiGroups':[''],'resources':['nodes','pods','namespaces','services','endpoints'],'verbs':['get','list','watch']},{'apiGroups':['networking.k8s.io'],'resources':['networkpolicies'],'verbs':['get','list','watch']},{'apiGroups':['discovery.k8s.io'],'resources':['endpointslices'],'verbs':['get','list','watch']}]},
      {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':meta('synthetic-kube-router'),'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':'synthetic-kube-router'},'subjects':[{'kind':'ServiceAccount','name':'synthetic-kube-router','namespace':'kube-system'}]}]
    for resource in ('volumesnapshotclasses','volumesnapshotcontents','volumesnapshots'):
        objects+=upstream('kubernetes-csi/external-snapshotter',SNAP,'client/config/crd/snapshot.storage.k8s.io_'+resource+'.yaml',assets)
    for file in ('rbac-snapshot-controller.yaml','setup-snapshot-controller.yaml'):
        objects+=upstream('kubernetes-csi/external-snapshotter',SNAP,'deploy/kubernetes/snapshot-controller/'+file,assets)
    for owner,version,path in (('external-provisioner','v5.2.0','deploy/kubernetes/rbac.yaml'),('external-attacher','v4.8.0','deploy/kubernetes/rbac.yaml'),('external-snapshotter','v8.2.0','deploy/kubernetes/csi-snapshotter/rbac-csi-snapshotter.yaml')):
        tag=json.loads(urllib.request.urlopen('https://api.github.com/repos/kubernetes-csi/'+owner+'/git/ref/tags/'+version,timeout=30).read())['object']
        if tag['type']=='tag': tag=json.loads(urllib.request.urlopen(tag['url'],timeout=30).read())['object']
        objects+=upstream('kubernetes-csi/'+owner,tag['sha'],path,assets)
    plugin=upstream('kubernetes-csi/csi-driver-host-path',HOST,'deploy/kubernetes-1.30/hostpath/csi-hostpath-plugin.yaml',assets)
    for o in plugin:
        if o['kind'] in ('RoleBinding','ClusterRoleBinding') and any(x in o['metadata']['name'] for x in ('health-monitor','resizer','snapshot-metadata')): continue
        if o['kind']=='StatefulSet':
            o['spec']['template']['spec']['containers']=[c for c in o['spec']['template']['spec']['containers'] if c['name'] not in ('csi-external-health-monitor-controller','csi-resizer')]
            next(c for c in o['spec']['template']['spec']['containers'] if c['name']=='hostpath')['image']='registry.k8s.io/sig-storage/hostpathplugin:v1.17.0'
        objects.append(o)
    objects+=upstream('kubernetes-csi/csi-driver-host-path',HOST,'deploy/kubernetes-1.30/hostpath/csi-hostpath-driverinfo.yaml',assets)
    images={}
    for o in objects:
        if o['kind'] in ('DaemonSet','Deployment','StatefulSet'):
            s=o['spec']['template']['spec']
            need(not s.get('initContainers'),'downloaded init commands forbidden')
            for c in s['containers']:
                original=c['image']; images.setdefault(original,pin(original)); c['image']=images[original]; c['imagePullPolicy']='IfNotPresent'
            if o['metadata']['name']=='snapshot-controller': o['spec']['replicas']=1
    objects += [sc(STORAGE,'Immediate'),sc(STORAGE+'-wffc','WaitForFirstConsumer'),{'apiVersion':'snapshot.storage.k8s.io/v1','kind':'VolumeSnapshotClass','metadata':meta(CLASS),'driver':'hostpath.csi.k8s.io','deletionPolicy':'Retain'}]
    plan={'classification':CLASSIFICATION,'context':CONTEXT,'assets':assets,'images':images,'objects':objects,'transformations':['Kindnet retained; firewall-only kube-router, no init shell/CNI mounts, API read-only role','hostpath v1.17.0; health/resizer/snapshot-metadata bindings omitted','snapshot controller one replica; immutable registry images','lab StorageClasses and snapshot class Retain']}
    save('install-plan.json',plan)
    save('upstream-review-inventory.json',[{'kind':o['kind'],'name':o['metadata']['name'],'namespace':o['metadata'].get('namespace'),'rules':o.get('rules'),'spec':o.get('spec') if o['kind']!='CustomResourceDefinition' else {'group':o['spec']['group'],'names':o['spec']['names'],'versions':[v['name'] for v in o['spec']['versions']],'schemaDigest':digest(o['spec'])}} for o in objects])
    print('Prepared immutable upstream plan for review: '+digest(plan),flush=True)

def install():
    plan=load('install-plan.json'); need(a.reviewed_plan_digest==digest(plan),'exact reviewed plan digest required')
    for asset in plan['assets']: need(hashlib.sha256((out/asset['file']).read_bytes()).hexdigest()==asset['sha256'],'upstream asset altered')
    if not (out/'installed.json').exists():
        # Refuse repeating an uncertain apply after failure. Diagnose retained state instead.
        need(not (out/'install-started.json').exists(),'installation already attempted; inspect retained state before resuming')
        save('install-started.json',{'planDigest':digest(plan),'at':stamp()})
        for o in plan['objects']:
            kub('apply','--server-side','--field-manager=synthetic-csi-qualification','-f','-',obj=o)
            if o['kind']=='CustomResourceDefinition': wait('crd',o['metadata']['name'],None,lambda x:any(c['type']=='Established' and c['status']=='True' for c in x.get('status',{}).get('conditions',[])))
        save('installed.json',{'planDigest':digest(plan),'at':stamp()})
    wait('daemonset','kube-router','kube-system',lambda x:x.get('status',{}).get('numberReady')==1)
    wait('statefulset','csi-hostpathplugin','default',lambda x:x.get('status',{}).get('readyReplicas')==1)
    wait('deployment','snapshot-controller','kube-system',lambda x:x.get('status',{}).get('availableReplicas',0)==1)
    save('installed-runtime.json',get('pods')['items'] if False else json.loads(kub('get','pods','-A','-o','json').stdout))
    print('Lab CSI and packet filtering ready; resources retained',flush=True)

def repair_network():
    old=get('clusterrole','synthetic-kube-router')
    need(old['metadata']['labels'][LABEL]==a.run_id,'refuse unowned network role')
    new=copy.deepcopy(old['rules'])
    if 'endpoints' not in new[0]['resources']: new[0]['resources'].append('endpoints')
    rule={'apiGroups':['discovery.k8s.io'],'resources':['endpointslices'],'verbs':['get','list','watch']}
    if rule not in new: new.append(rule)
    if new!=old['rules']:
        save('network-rbac-correction.json',{'before':old,'reviewedRules':new,'reason':'v2.5.0 starts shared endpoint informers even in firewall-only mode','at':stamp()})
        kub('patch','clusterrole','synthetic-kube-router','--type=json','-p',json.dumps([{'op':'replace','path':'/rules','value':new}]))
    wait('daemonset','kube-router','kube-system',lambda x:x.get('status',{}).get('numberReady')==1)
    print('Lab-only read RBAC corrected; packet filter ready',flush=True)

def repair_admission():
    old=get('validatingwebhookconfiguration','synthetic-offline-boundary')
    need(old['metadata']['labels'][LABEL]==a.run_id,'refuse unowned admission configuration')
    before=old['webhooks'][0]['matchConditions'][1]['expression']
    fixed="!has(request.subResource) || request.subResource != 'status'"
    need(before in ("request.subResource != 'status'",fixed),'unexpected admission predicate')
    if before!=fixed:
        save('admission-cel-correction.json',{'before':old,'reviewedExpression':fixed,'reason':'Kubernetes CEL request omits subResource for root resources','at':stamp()})
        kub('patch','validatingwebhookconfiguration','synthetic-offline-boundary','--type=json','-p',json.dumps([{'op':'replace','path':'/webhooks/0/matchConditions/1/expression','value':fixed}]))
        save('admission-webhook.json',get('validatingwebhookconfiguration','synthetic-offline-boundary'))

def repair_admission_server():
    # Tiny admission-code layer on a cached image; no registry or archive copy.
    info=load('tools-image.json'); tag='docker.io/kerosene-lab/admission:'+a.run_id
    base_tag='docker.io/kerosene-lab/offline-tools:'+a.run_id
    need(json.loads(cmd(['docker','image','inspect',base_tag]).stdout)[0]['Id']==info['dockerImage'],'cached base tag identity changed')
    if not (out/'admission-server-image.json').exists():
        r=cmd(['docker','build','--pull=false','--network=none','--progress=plain','--build-arg','BASE_IMAGE='+base_tag,'-f',str(HERE/'Dockerfile.admission'),'-t',tag,str(HERE)],timeout=180,ok=False)
        (out/'admission-image-build.log').write_text(r.stdout+r.stderr)
        need(r.returncode==0,'cached admission-only build failed')
        producer=subprocess.Popen(['docker','save',tag],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV)
        consumer=subprocess.run(['docker','exec','-i',NODE,'ctr','-n','k8s.io','images','import','--digests','-'],stdin=producer.stdout,capture_output=True,timeout=180,env=ENV)
        producer.stdout.close(); stderr=producer.stderr.read(); code=producer.wait()
        (out/'admission-image-import.log').write_bytes(consumer.stdout+consumer.stderr+stderr)
        need(code==consumer.returncode==0,'streamed admission image import failed')
        listing=cmd(['docker','exec',NODE,'ctr','-n','k8s.io','images','ls']).stdout
        line=next(x for x in listing.splitlines() if x.startswith(tag+' ')); manifest=next(x for x in line.split() if re.fullmatch(r'sha256:[0-9a-f]{64}',x))
        immutable='docker.io/kerosene-lab/admission@'+manifest
        cmd(['docker','exec',NODE,'ctr','-n','k8s.io','images','tag',tag,immutable],ok=False)
        save('admission-server-image.json',{'image':immutable,'baseDockerImage':info['dockerImage'],'baseProbeImage':info['image'],'admissionSourceDigest':'sha256:'+hashlib.sha256((HERE/'admission.py').read_bytes()).hexdigest(),'reason':'allow exact scheduler selected-node metadata update for reviewed WFFC PVC','registryPulls':0,'at':stamp()})
    image=load('admission-server-image.json')['image']; deployment=get('deployment','offline-admission',LAB)
    if deployment['spec']['template']['spec']['containers'][0]['image']!=image:
        save('admission-server-before-repair.json',deployment)
        kub('-n',LAB,'patch','deployment','offline-admission','--type=json','-p',json.dumps([{'op':'replace','path':'/spec/template/spec/containers/0/image','value':image}]))
    wait('deployment','offline-admission',LAB,lambda x:x.get('status',{}).get('observedGeneration')==x['metadata']['generation'] and x.get('status',{}).get('updatedReplicas')==1 and x.get('status',{}).get('availableReplicas')==1)
    print('Admission scheduler binding repair ready; unchanged probe image, zero registry pulls',flush=True)

def build():
    need(not (out/'tools-image.json').exists(),'image already built/imported; use retained immutable image')
    bases=load('build-inputs.json')['bases'] if (out/'build-inputs.json').exists() else {k:pin(v) for k,v in {'GO_IMAGE':'docker.io/library/golang:1.24.7-alpine3.22','REDIS_IMAGE':'docker.io/library/redis:7.4.2-alpine3.21','POSTGRES_IMAGE':'docker.io/library/postgres:16.10-alpine3.22'}.items()}
    tag='docker.io/kerosene-lab/offline-tools:'+a.run_id
    args=['docker','build','--network=host','--progress=plain','-t',tag]
    for k,v in bases.items(): args+=['--build-arg',k+'='+v]
    archive=out/'offline-tools.tar'
    if not archive.exists():
        save('build-inputs.json',{'bases':bases,'sources':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in HERE.iterdir() if f.is_file()}})
        r=cmd(args+[str(HERE)],timeout=1800,ok=False); (out/'image-build.log').write_text(r.stdout+r.stderr)
        need(r.returncode==0,'image build failed; inspect image-build.log')
        cmd(['docker','save','-o',str(archive),tag],timeout=180)
    # No registry, extra running container, or other Kind node. Import only into exact node.
    node_archive='/var/kerosene-offline-tools-'+a.run_id+'.tar'
    if cmd(['docker','exec',NODE,'test','-f',node_archive],ok=False).returncode:
        cmd(['docker','cp',str(archive),NODE+':'+node_archive],timeout=180)
    r=cmd(['docker','exec',NODE,'ctr','-n','k8s.io','images','import','--digests',node_archive],timeout=180)
    (out/'image-import.log').write_text(r.stdout+r.stderr)
    listing=cmd(['docker','exec',NODE,'ctr','-n','k8s.io','images','ls']).stdout
    line=next(x for x in listing.splitlines() if x.startswith(tag+' '))
    manifest=next(x for x in line.split() if re.fullmatch(r'sha256:[0-9a-f]{64}',x))
    immutable='docker.io/kerosene-lab/offline-tools@'+manifest
    cmd(['docker','exec',NODE,'ctr','-n','k8s.io','images','tag',tag,immutable],ok=False)
    save('tools-image.json',{'image':immutable,'dockerImage':json.loads(cmd(['docker','image','inspect',tag]).stdout)[0]['Id'],'manifestDigest':manifest,'archiveSha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bases':bases})
    print('Actual offline tools built/imported: '+immutable,flush=True)

def release_import_staging():
    archive='/var/kerosene-offline-tools-'+a.run_id+'.tar'
    info=load('tools-image.json')
    external=out/'offline-tools.tar'
    need(hashlib.sha256(external.read_bytes()).hexdigest()==info['archiveSha256'],'external retained archive checksum changed')
    present=cmd(['docker','exec',NODE,'test','-f',archive],ok=False)
    if present.returncode==0:
        actual=cmd(['docker','exec',NODE,'sha256sum',archive]).stdout.split()[0]
        need(actual==info['archiveSha256'],'refuse deleting unverified import copy')
        save('released-import-staging.json',{'removedExactNodeFile':archive,'sha256':actual,'retainedExternalArchive':str(external),'reason':'reclaim own redundant import staging copy after successful import; no cluster resource teardown','at':stamp()})
        cmd(['docker','exec',NODE,'rm','--',archive])
    # Docker cp writes beneath Kind's /tmp tmpfs. The first import attempt left
    # an otherwise invisible redundant copy in THIS container's writable layer.
    # A private mount namespace exposes it without unmounting the live node's /tmp.
    inspect=cmd(['docker','exec',NODE,'unshare','--mount','--propagation','private','sh','-c','umount /tmp && if test -f /tmp/kerosene-offline-tools.tar; then sha256sum /tmp/kerosene-offline-tools.tar; fi'])
    if inspect.stdout.strip():
        actual=inspect.stdout.split()[0]
        need(actual==info['archiveSha256'],'refuse deleting unverified hidden import copy')
        save('released-hidden-import-staging.json',{'removedExactNodeFile':'/tmp/kerosene-offline-tools.tar beneath private tmpfs','sha256':actual,'retainedExternalArchive':str(external),'bytes':external.stat().st_size,'at':stamp(),'liveNodeMountsUnchanged':True})
        script='umount /tmp && actual=$(sha256sum /tmp/kerosene-offline-tools.tar) && test "$actual" = "$1  /tmp/kerosene-offline-tools.tar" && rm -- /tmp/kerosene-offline-tools.tar'
        cmd(['docker','exec',NODE,'unshare','--mount','--propagation','private','sh','-c',script,'sh',info['archiveSha256']])
    print('Redundant verified import copy released; external archive/resources retained',flush=True)

def recover_collector():
    need((out/'backup/maintenance.json').exists() and (out/'backup/quiesced.json').exists(),'no original stopped backup window to recover')
    need(not (out/'backup/request.json').exists(),'collector request already exists; do not duplicate recovery')
    maintenance=load('backup/maintenance.json')
    need(maintenance['context']==CONTEXT and maintenance['runId']==a.run_id,'wrong backup identity')
    need(not maintenance['workloads'],'collector-only recovery restricted to empty synthetic fixture namespaces')
    for ns in NS: need(not get('pods',ns=ns)['items'],'source writers reappeared')
    for original in maintenance['pvcs']:
        current=get('pvc',original['name'],original['namespace'])
        need(current['metadata']['uid']==original['uid'] and current['spec']['volumeName']==original['pv'],'source PVC changed')
    r=cmd(['bash',str(REPO/'infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh'),'--context',CONTEXT,'--release-id','synthetic-kind-audit-only','--release-lock-digest','sha256:'+'0'*64,'--snapshot-selector','backup.kerosene.io/run='+a.run_id],ok=False)
    (out/'collector-recovery-command.log').write_text(r.stderr)
    need(r.returncode==0,'real raw API collector failed')
    request=json.loads(r.stdout); need(len(request['snapshots'])==10,'incomplete snapshot recovery')
    for s in request['snapshots']:
        snap=get('volumesnapshot',s['volumeSnapshotName'],s['namespace']); c=get('volumesnapshotcontent',s['boundVolumeSnapshotContentName']); ref=c['spec']['volumeSnapshotRef']
        need(snap['spec']['volumeSnapshotClassName']==CLASS and snap['metadata']['labels']['backup.kerosene.io/run']==a.run_id,'wrong source run')
        need(c['spec']['deletionPolicy']=='Retain' and c['status'].get('readyToUse') is True and not c['status'].get('error') and ref['uid']==s['volumeSnapshotUid'] and ref['namespace']==s['namespace'] and ref['name']==s['volumeSnapshotName'],'invalid retained source binding')
    save('backup/request.json',request)
    save('backup/collector-recovered.json',{'at':stamp(),'requestDigest':digest(request),'originalBackupInvocationSucceeded':False,'reason':'actual kubectl v1.32.3 generic List rejected; exact raw API typed lists accepted by unchanged collector','rawApiEvidence':str(out/'raw-api-lists'),'newSnapshotsCreated':0})
    print('Actual API collector recovered existing ten snapshots; no duplicate backup mutation',flush=True)

def probe_contract():
    path=REPO/'infra/kubernetes/scripts/restore-staging-snapshots.sh'
    source=path.read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    tree=ast.parse(source)
    probe=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='PROBE' for x in n.targets))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='pod_spec')
    return probe,fn

def toolcheck(image):
    code="import subprocess,json\nr={}\nfor key,argv in {'pg':['pg_controldata','--version'],'checksums':['pg_checksums','--version'],'redis':['redis-server','--version']}.items():\n p=subprocess.run(argv,capture_output=True,text=True,check=True);r[key]=p.stdout.strip()\nassert '16.10' in r['pg'] and '16.10' in r['checksums'] and '7.4.2' in r['redis']\nprint(json.dumps(r))"
    return json.loads(runpod(pod('tool-versions',LAB,code,image),'tool-versions'))

def negative_tools():
    image=load('tools-image.json')['image']; namespace(LAB)
    source=(HERE/'utility-negative-tests.py').read_text()
    results=[]
    for kind in ('postgres','redis','lnd'):
        o=pod('utility-negative-'+kind,LAB,source,image,readonly=False,args=[kind])
        o['spec']['containers'][0]['securityContext']={'runAsUser':0,'allowPrivilegeEscalation':False}
        o['spec']['containers'][0]['volumeMounts']=[{'name':'scratch','mountPath':'/data'}]
        o['spec']['volumes']=[{'name':'scratch','emptyDir':{'medium':'Memory','sizeLimit':'256Mi'}}]
        results.append(json.loads(runpod(o,'utility-negative-'+kind).strip().splitlines()[-1]))
    need(all(x['passed'] for x in results),'utility corruption tests incomplete')
    save('utility-negative-tests.json',{'results':results,'image':image,'codeDigest':'sha256:'+hashlib.sha256(source.encode()).hexdigest(),'at':stamp()})

def deny_policy(ns):
    return {'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':meta('deny-all',ns),'spec':{'podSelector':{},'policyTypes':['Ingress','Egress'],'ingress':[],'egress':[]}}

def inventory_code(probe):
    tree=ast.parse(probe)
    functions=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef)) and (not isinstance(n,ast.FunctionDef) or n.name in ('need','canonical','digest','inventory'))]
    return ast.unparse(ast.Module(body=functions,type_ignores=[]))

def offline_pod(i,ns,config,image,probe,fn):
    scope={'a':a,'approval':{'probeImage':image},'PROBE':probe,'json':json}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<reviewed-production-pod-spec>','exec'),scope)
    return {'apiVersion':'v1','kind':'Pod','metadata':meta('probe-'+str(i),ns),'spec':scope['pod_spec'](i,config)}

def snapshot_obj(name,ns,source):
    return {'apiVersion':'snapshot.storage.k8s.io/v1','kind':'VolumeSnapshot','metadata':meta(name,ns),'spec':{'volumeSnapshotClassName':CLASS,'source':source}}

def import_pair(ns,index,source):
    c=get('volumesnapshotcontent',source['boundVolumeSnapshotContentName'])
    content={'apiVersion':'snapshot.storage.k8s.io/v1','kind':'VolumeSnapshotContent','metadata':meta(ns+'-'+str(index)),'spec':{'deletionPolicy':'Retain','driver':c['spec']['driver'],'volumeSnapshotClassName':CLASS,'sourceVolumeMode':'Filesystem','source':{'snapshotHandle':c['status']['snapshotHandle']},'volumeSnapshotRef':{'name':'import-'+str(index),'namespace':ns}}}
    snap=snapshot_obj('import-'+str(index),ns,{'volumeSnapshotContentName':ns+'-'+str(index)})
    return content,snap

def production_helpers(request,approval,probe,strict=False):
    source=(REPO/'infra/kubernetes/scripts/restore-staging-snapshots.sh').read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    names={'source','binding','observed','pod_spec','pod_spec_matches','deny_all_spec','subset','isolation'}
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
    scope={'a':a,'approval':approval,'PROBE':probe,'json':json,'re':re,'hashlib':hashlib,'need':need,'get':get,'kub':lambda *args,**kw:kub(*args,**kw).stdout,'digest':digest,'subset':subset,'run':approval['namespace'],'LABEL':'backup.kerosene.io/run'}
    scope['source_contents']=[get('volumesnapshotcontent',s['boundVolumeSnapshotContentName']) for s in request['snapshots']]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<reviewed-production-observers>','exec'),scope)
    return scope

def lab_boundary(ns):
    namespace=get('namespace',ns)
    need(namespace['metadata']['labels'].get('backup.kerosene.io/run')==ns,'restore namespace identity changed')
    policies=get('networkpolicies',ns=ns)['items']
    need(len(policies)==1 and policies[0]['metadata']['name']=='deny-all','unexpected isolation policies')
    spec=copy.deepcopy(policies[0]['spec'])
    # API omitempty: an absent rule list and [] both contain zero allowances.
    for field in ('ingress','egress'):
        if spec.get(field)==[]: spec.pop(field)
    need(spec=={'podSelector':{},'policyTypes':['Ingress','Egress']},'network policy allows traffic')
    for resource in ('secrets','services','endpoints','endpointslices','ingresses','deployments','statefulsets','daemonsets','jobs','cronjobs','rolebindings','roles'):
        need(not get(resource,ns=ns)['items'],'unexpected isolated resource: '+resource)
    sa=get('serviceaccount','restore-check',ns)
    need(sa.get('automountServiceAccountToken') is False and not sa.get('secrets') and not sa.get('imagePullSecrets'),'restore account acquired credentials')
    return {'namespaceUid':namespace['metadata']['uid'],'policyUid':policies[0]['metadata']['uid'],'serviceAccountUid':sa['metadata']['uid']}

def production_restore(request,approval,configs,probe,fn):
    # No derived executor or response adapter can substitute for the actual CLI.
    directory=out/'restore'
    need(not directory.exists(),'partial/existing production restore retained; explicit recovery required')
    ns=approval['namespace']
    need(not kub('get','namespace',ns,'--ignore-not-found','-o','json').stdout.strip(),'restore namespace already exists; refuse replay')
    argv=['bash',str(REPO/'infra/kubernetes/scripts/restore-staging-snapshots.sh'),'check',
          '--context',CONTEXT,'--request',str(out/'backup/request-stock-collector.json'),
          '--namespace',ns,'--output-dir',str(directory),
          '--isolation-approval',str(out/'lab-isolation-approval.json'),
          '--profiles',str(out/'source-profiles.json'),'--timeout',str(a.timeout)]
    environment=dict(ENV); environment.pop('KUBECTL',None)
    r=subprocess.run(argv,capture_output=True,text=True,env=environment,timeout=10*a.timeout+300)
    (out/'production-restore-command.log').write_text(r.stdout+r.stderr)
    need(r.returncode==0,'production restore CLI failed; retained resources require explicit recovery')
    evidence=load('restore/evidence.json')
    scope=production_helpers(request,approval,probe,strict=True)
    need(scope['isolation']()==evidence['boundary'],'live production isolation differs from evidence')
    records=[scope['observed'](i,s,configs[i]) for i,s in enumerate(request['snapshots'])]
    need(records==evidence['probes'],'live production observers differ from evidence')
    need(len({r['restoredVolumeHandleDigest'] for r in records})==10,'restored backend volume aliases overlap')
    save('production-restore-invocation.json',{'classification':CLASSIFICATION,'productionCliInvoked':True,
         'stockKubectl':True,'mode':'check','evidenceDigest':digest(evidence),
         'scriptSha256':hashlib.sha256((REPO/'infra/kubernetes/scripts/restore-staging-snapshots.sh').read_bytes()).hexdigest()})
    return evidence

def revalidate_stock():
    need((out/'qualification.json').exists(),'complete lab qualification first')
    request=load('backup/request.json'); approval=load('lab-isolation-approval.json'); probe,_=probe_contract()
    # Use unmodified stock CLI, with only KUBECONFIG/context binding. No output adaptation.
    argv=['bash',str(REPO/'infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh'),'--context',CONTEXT,'--release-id',request['release']['id'],'--release-lock-digest',request['release']['lockDigest'],'--snapshot-selector',request['selection']['labelSelector'],'--requested-at',request['requestedAt']]
    environment=dict(ENV); environment.pop('KUBECTL',None)
    r=subprocess.run(argv,capture_output=True,text=True,env=environment,timeout=60)
    (out/'stock-revalidation-collector.log').write_text(r.stderr)
    need(r.returncode==0 and json.loads(r.stdout)==request,'stock collector differs from retained request')
    (out/'backup/request-stock-revalidated.json').write_text(r.stdout)
    scope=production_helpers(request,approval,probe,strict=True)
    boundary=scope['isolation']()
    evidence=load('restore/evidence.json'); profiles=load('source-profiles.json'); network=load('real-packet-controls.json')
    records=[]
    for s in request['snapshots']:
        name=s['persistentVolumeClaim']; kind=next((x for x in ('postgres','redis','bitcoin','lnd','tor') if name.endswith('-'+x+'-0')),'vault')
        cfg={'kind':kind,'expectedTreeDigest':profiles[s['namespace']+'/'+name]['expectedTreeDigest'],'egressTargets':network['targets'],'timeout':a.timeout}
        records.append(scope['observed'](len(records),s,cfg))
    need(records==evidence['probes'] and boundary==evidence['boundary'],'stock observers differ from retained live evidence')
    save('stock-revalidation.json',{'at':stamp(),'stockCollectorPassed':True,'stockRestoreObserversPassed':True,'stockCliResponsesUnmodified':True,'restoreScriptSha256':hashlib.sha256((REPO/'infra/kubernetes/scripts/restore-staging-snapshots.sh').read_bytes()).hexdigest(),'collectorScriptSha256':hashlib.sha256((REPO/'infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh').read_bytes()).hexdigest(),'restoreEvidenceDigest':digest(evidence),'resourcesMutated':0,'probeExecutionsRepeated':0,'fullFreshRestoreCliInvocation':False})
    print('Stock collector and production restore observers revalidated retained live resources',flush=True)

def admission_install(allowed,namespaces,image):
    if (out/'admission-installed.json').exists():
        need(load('admission-installed.json')['allowDigest']==digest(allowed),'admission allowlist changed; retained run cannot be replayed')
        return
    save('admission-allow.json',allowed)
    cert=out/'admission.crt'; key=out/'admission.key'
    if not cert.exists():
        cmd(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','2','-subj','/CN=offline-admission.'+LAB+'.svc','-addext','subjectAltName=DNS:offline-admission.'+LAB+'.svc'])
    create({'apiVersion':'v1','kind':'Secret','metadata':meta('offline-admission-tls',LAB),'type':'kubernetes.io/tls','data':{'tls.crt':base64.b64encode(cert.read_bytes()).decode(),'tls.key':base64.b64encode(key.read_bytes()).decode()}})
    create({'apiVersion':'v1','kind':'ConfigMap','metadata':meta('offline-admission-policy',LAB),'immutable':True,'data':{'allow.json':json.dumps(allowed,sort_keys=True)}})
    create({'apiVersion':'v1','kind':'Service','metadata':meta('offline-admission',LAB),'spec':{'selector':{'app':'synthetic-offline-admission'},'ports':[{'port':443,'targetPort':8443}]}})
    spec={'replicas':1,'selector':{'matchLabels':{'app':'synthetic-offline-admission'}},'template':{'metadata':{'labels':{'app':'synthetic-offline-admission'}},'spec':{'automountServiceAccountToken':False,'enableServiceLinks':False,'securityContext':{'seccompProfile':{'type':'RuntimeDefault'}},'containers':[{'name':'webhook','image':image,'imagePullPolicy':'IfNotPresent','command':['python3','/opt/lab/admission.py'],'securityContext':{'runAsUser':65532,'runAsGroup':65532,'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},'volumeMounts':[{'name':'tls','mountPath':'/tls','readOnly':True},{'name':'policy','mountPath':'/policy','readOnly':True}],'readinessProbe':{'httpGet':{'path':'/','port':8443,'scheme':'HTTPS'}}}],'volumes':[{'name':'tls','secret':{'secretName':'offline-admission-tls'}},{'name':'policy','configMap':{'name':'offline-admission-policy'}}]}}}
    create({'apiVersion':'apps/v1','kind':'Deployment','metadata':meta('offline-admission',LAB),'spec':spec})
    wait('deployment','offline-admission',LAB,lambda x:x.get('status',{}).get('availableReplicas')==1)
    hook={'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingWebhookConfiguration','metadata':meta('synthetic-offline-boundary'),'webhooks':[{'name':'offline.qualification.kerosene.io','admissionReviewVersions':['v1'],'sideEffects':'None','failurePolicy':'Fail','matchPolicy':'Equivalent','timeoutSeconds':5,'clientConfig':{'service':{'name':'offline-admission','namespace':LAB,'path':'/','port':443},'caBundle':base64.b64encode(cert.read_bytes()).decode()},'matchConditions':[{'name':'exact-lab-namespace-names','expression':'request.namespace in '+json.dumps(namespaces)},{'name':'exclude-status-only','expression':"!has(request.subResource) || request.subResource != 'status'"}],'rules':[{'apiGroups':['*'],'apiVersions':['*'],'resources':['*/*'],'operations':['CREATE','UPDATE','CONNECT','DELETE'],'scope':'Namespaced'}]}]}
    create(hook); save('admission-webhook.json',get('validatingwebhookconfiguration','synthetic-offline-boundary'))
    save('admission-installed.json',{'allowDigest':digest(allowed),'image':image,'namespaces':namespaces,'at':stamp()})

def allow_entry(allow,obj,resource,normalize_ns):
    normalized=copy.deepcopy(obj)
    normalized['metadata']['namespace']=normalize_ns
    normalized['metadata']['name']='normalize-'+obj['metadata']['name']
    result=json.loads(kub('create','--dry-run=server','-f','-','-o','json',obj=normalized).stdout)
    spec=result.get('spec',{})
    spec.pop('volumeName',None)
    key='/'.join((obj['metadata']['namespace'],resource,obj['metadata']['name']))
    allow['objects'][key]={'spec':spec}

def admission_tests(ns,base):
    if (out/'admission-tests.json').exists(): return load('admission-tests.json')
    warm=copy.deepcopy(base); warm['spec']['containers'][0]['command']=['python3','-c','print(1)']
    deadline=time.monotonic()+30
    while True:
        r=kub('create','--dry-run=server','-f','-','-o','json',obj=warm,ok=False)
        if r.returncode and 'KEROSENE_OFFLINE_BOUNDARY' in r.stderr: break
        need(time.monotonic()<deadline,'offline admission did not become active')
        time.sleep(1)
    cases=[]
    for label,change in (
      ('writable-data-mount',lambda s:s['containers'][0]['volumeMounts'][0].update(readOnly=False)),
      ('writable-pvc',lambda s:s['volumes'][0]['persistentVolumeClaim'].update(readOnly=False)),
      ('credentials-token',lambda s:s.update(automountServiceAccountToken=True)),
      ('credentials-env',lambda s:s['containers'][0].update(env=[{'name':'TOKEN','valueFrom':{'secretKeyRef':{'name':'credentials','key':'token'}}}])),
      ('credentials-pull',lambda s:s.update(imagePullSecrets=[{'name':'credentials'}])),
      ('projected-token',lambda s:s['volumes'].append({'name':'token','projected':{'sources':[{'serviceAccountToken':{'path':'token'}}]}})),
      ('sidecar',lambda s:s['containers'].append({**s['containers'][0],'name':'sidecar'})),
      ('init-container',lambda s:s.update(initContainers=[{**s['containers'][0],'name':'init'}])),
      ('daemon-command',lambda s:s['containers'][0].update(command=['redis-server'])),
      ('unreviewed-python',lambda s:s['containers'][0].update(command=['python3','-c','print(1)'])),
      ('mutable-image',lambda s:s['containers'][0].update(image='redis:7.4')),
      ('host-network',lambda s:s.update(hostNetwork=True)),
      ('privileged',lambda s:s['containers'][0]['securityContext'].update(privileged=True,allowPrivilegeEscalation=True)),
      ('extra-writable-volume',lambda s:s['volumes'].append({'name':'extra','emptyDir':{}})),
      ('writable-root',lambda s:s['containers'][0]['securityContext'].update(readOnlyRootFilesystem=False))):
        o=copy.deepcopy(base); change(o['spec']); cases.append((label,o))
    for kind,api,spec in (
      ('DaemonSet','apps/v1',{'selector':{'matchLabels':{'app':'denied'}},'template':{'metadata':{'labels':{'app':'denied'}},'spec':{'containers':[{'name':'daemon','image':base['spec']['containers'][0]['image']}]}}}),
      ('Deployment','apps/v1',{'selector':{'matchLabels':{'app':'denied'}},'template':{'metadata':{'labels':{'app':'denied'}},'spec':{'containers':[{'name':'daemon','image':base['spec']['containers'][0]['image']}]}}}),
      ('Job','batch/v1',{'template':{'spec':{'restartPolicy':'Never','containers':[{'name':'daemon','image':base['spec']['containers'][0]['image']}]}}}),
      ('Service','v1',{'ports':[{'port':80}]}),
      ('Role','rbac.authorization.k8s.io/v1',None)):
        o={'apiVersion':api,'kind':kind,'metadata':meta('denied-'+kind.lower(),ns)}
        if spec: o['spec']=spec
        if kind=='Role': o['rules']=[{'apiGroups':[''],'resources':['secrets'],'verbs':['get']}]
        cases.append((kind.lower(),o))
    cases.append(('secret',{'apiVersion':'v1','kind':'Secret','metadata':meta('denied-secret',ns),'stringData':{'synthetic':'not-a-secret'}}))
    results=[]
    for name,o in cases:
        # Actual CREATE requests, not dry-run. Every expected denial must identify this webhook.
        record='admission-negative/'+name+'.json'
        if (out/record).exists() and 'KEROSENE_OFFLINE_BOUNDARY' in load(record)['stderr']:
            prior=load(record); need(prior['attempt']==o,'denied admission test changed: '+name)
        else:
            if (out/record).exists(): save('admission-negative/invalid-request-'+name+'.json',load(record))
            r=kub('create','-f','-','-o','json',obj=o,ok=False)
            save(record,{'attempt':o,'returncode':r.returncode,'stderr':r.stderr,'stdout':r.stdout})
            need(r.returncode!=0 and 'KEROSENE_OFFLINE_BOUNDARY' in r.stderr,'admission negative test did not deny at boundary: '+name)
        results.append({'case':name,'deniedByOfflineBoundary':True})
    # Admit the real, exact pilot consumer. It remains Pending until its restore
    # PVC is created; no arbitrary daemon can start while subresource denial is tested.
    positive=create(base); save('admission-positive-pod.json',positive)
    ephemeral={'spec':{'ephemeralContainers':[{'name':'debug','image':base['spec']['containers'][0]['image'],'command':['python3','-c','print(1)']}]}}
    record='admission-negative/ephemeral-container.json'
    if not (out/record).exists():
        r=kub('-n',ns,'patch','pod',base['metadata']['name'],'--subresource=ephemeralcontainers','--type=merge','-p',json.dumps(ephemeral),ok=False)
        save(record,{'attempt':ephemeral,'returncode':r.returncode,'stderr':r.stderr,'stdout':r.stdout,'podUid':positive['metadata']['uid']})
    prior=load(record)
    need(prior['returncode']!=0 and 'KEROSENE_OFFLINE_BOUNDARY' in prior['stderr'] and prior['podUid']==positive['metadata']['uid'],'ephemeral subresource denial required')
    results.append({'case':'ephemeral-container','deniedByOfflineBoundary':True})
    result={'at':stamp(),'negative':results,'positiveExactProbeUid':positive['metadata']['uid'],'positiveExactProbeSpecDigest':digest(positive['spec']),'policyUid':get('validatingwebhookconfiguration','synthetic-offline-boundary')['metadata']['uid']}
    save('admission-tests.json',result); return result

def packet_controls(image):
    if (out/'real-packet-controls.json').exists(): return load('real-packet-controls.json')
    netns='kerosene-network-'+a.run_id; namespace(netns)
    listener_code="import socket,json,threading,time\ns=socket.socket();s.bind(('0.0.0.0',8443));s.listen();print('SYNTHETIC LISTENING',flush=True)\nwhile True:\n c,a=s.accept();c.sendall(b'SYNTHETIC');c.close()"
    listener=pod('synthetic-peer',LAB,listener_code,image); listener['spec'].pop('activeDeadlineSeconds')
    create(listener)
    peer=wait('pod','synthetic-peer',LAB,lambda x:x.get('status',{}).get('phase')=='Running')['status']['podIP']
    targets=[{'ip':get('service','kubernetes','default')['spec']['clusterIP'],'port':443},{'ip':get('service','kube-dns','kube-system')['spec']['clusterIP'],'port':53},{'ip':peer,'port':8443}]
    def network_code(expected):
        return "import socket,json,time\ntime.sleep(5)\ntargets="+repr(targets)+"\nresults=[]\nfor e in targets:\n s=socket.socket();s.settimeout(3)\n try:s.connect((e['ip'],e['port']));results.append('connected')\n except OSError as e:results.append('denied')\n finally:s.close()\nprint(json.dumps(results),flush=True)\nassert results=="+repr(expected)
    positive=json.loads(runpod(pod('positive-packet-control',LAB,network_code(['connected']*3),image),'positive-packet-control'))
    # Same endpoints known listening; deny egress in fresh namespace.
    create(deny_policy(netns))
    negative=json.loads(runpod(pod('negative-packet-control',netns,network_code(['denied']*3),image),'negative-packet-control'))
    # Independent ingress canary: listen before policy, connect positively, then select only it for ingress deny.
    incoming=pod('ingress-listener',LAB,listener_code,image); incoming['spec'].pop('activeDeadlineSeconds'); incoming['metadata']['labels']['ingress-canary']='yes'
    create(incoming); ip=wait('pod','ingress-listener',LAB,lambda x:x.get('status',{}).get('phase')=='Running')['status']['podIP']
    check="import socket,json,time\ntime.sleep(5)\ns=socket.socket();s.settimeout(3)\ntry:s.connect(('"+ip+"',8443));result='connected'\nexcept OSError:result='denied'\nfinally:s.close()\nprint(json.dumps(result))\nassert result=="
    ingress_positive=json.loads(runpod(pod('ingress-positive',LAB,check+repr('connected'),image),'ingress-positive'))
    policy=deny_policy(LAB); policy['metadata']['name']='deny-ingress-canary'; policy['spec']={'podSelector':{'matchLabels':{'ingress-canary':'yes'}},'policyTypes':['Ingress'],'ingress':[]}
    create(policy)
    ingress_negative=json.loads(runpod(pod('ingress-negative',LAB,check+repr('denied'),image),'ingress-negative'))
    result={'targets':targets,'positive':positive,'egressNegative':negative,'ingressPositive':ingress_positive,'ingressNegative':ingress_negative,'at':stamp(),'classification':'REAL-TCP-PACKETS-SYNTHETIC-PEERS','cni':'Kindnet plus kube-router v2.5.0 firewall-only'}
    save('real-packet-controls.json',result); return result

def qualify():
    # Space on the workspace filesystem cannot substitute for space on Docker's
    # loop filesystem. Refuse a new qualification attempt below working margin.
    disk=os.statvfs('/home/astrofer/.local/share/docker-data')
    need(disk.f_bavail*disk.f_frsize >= 256*1024*1024,'Docker data filesystem below 256 MiB qualification margin; do not prune or resize automatically')
    image=load('tools-image.json')['image']; probe,fn=probe_contract()
    namespace(LAB); versions=toolcheck(image)
    for ns in NS: namespace(ns)
    inv=inventory_code(probe)
    profiles={}
    for i,(ns,name) in enumerate(TARGETS):
        which=next((x for x in ('bitcoin','lnd','postgres','redis','tor') if name.endswith('-'+x+'-0')),'vault')
        create(pvc(name,ns)); wait('pvc',name,ns,lambda x:x.get('status',{}).get('phase')=='Bound')
        checkpoint='fixture-'+str(i)
        if not (out/(checkpoint+'.log')).exists():
            code="import subprocess,pathlib\np=pathlib.Path('/data/pgdata')\nif "+repr(which)+"=='postgres' and p.is_dir() and not list(p.iterdir()):p.rmdir()\nsubprocess.run(['python3','/opt/lab/fixtures.py',"+repr(which)+",'/data'],check=True)\n"+inv+"\nprint(json.dumps({'expectedTreeDigest':digest(inventory(pathlib.Path('/data')))},sort_keys=True))"
            o=pod('synthetic-populate-'+str(i),ns,code,image,claim=name,readonly=False)
            # Fixture population must chown synthetic data before read-only owner probes.
            o['spec']['containers'][0]['securityContext']={'runAsUser':0,'allowPrivilegeEscalation':False}
            previous=kub('-n',ns,'get','pod',o['metadata']['name'],'--ignore-not-found','-o','json').stdout
            if previous.strip():
                previous=json.loads(previous)
                if previous.get('status',{}).get('phase')=='Failed':
                    need(which=='postgres' and 'No space left on device' in kub('-n',ns,'logs',o['metadata']['name']).stdout,'failed fixture is not an authorized ENOSPC retry')
                    save('fixture-failed-'+str(i)+'.json',previous)
                    (out/('fixture-failed-'+str(i)+'.log')).write_text(kub('-n',ns,'logs',o['metadata']['name']).stdout)
                    # Failed Pod must leave the stopped-source namespace before backup; its full evidence is retained.
                    kub('-n',ns,'delete','pod',o['metadata']['name'],'--wait=true','--timeout=40s')
                    o['metadata']['name']+='-enospc-retry'
            runpod(o,checkpoint)
        profiles[ns+'/'+name]=json.loads((out/(checkpoint+'.log')).read_text().splitlines()[-1])
        # Backup contract needs source namespaces empty. Only completed, owned fixture Pods are removed.
        for fixture_name in ('synthetic-populate-'+str(i),'synthetic-populate-'+str(i)+'-enospc-retry'):
            existing=kub('-n',ns,'get','pod',fixture_name,'--ignore-not-found','-o','json').stdout
            if existing.strip():
                current=json.loads(existing); need(current['status']['phase']=='Succeeded' and current['metadata']['labels'][LABEL]==a.run_id,'refuse removal of unfinished/unowned fixture Pod')
                kub('-n',ns,'delete','pod',current['metadata']['name'],'--wait=true','--timeout=40s')
    save('source-profiles.json',profiles)
    if not (out/'backup/request.json').exists():
        need(not (out/'backup').exists(),'partial backup retained; refuse duplicate snapshot run')
        argv=['bash',str(REPO/'infra/kubernetes/scripts/create-staging-snapshots.sh'),'--context',CONTEXT,'--run-id',a.run_id,'--release-id','synthetic-kind-audit-only','--release-lock-digest','sha256:'+'0'*64,'--snapshot-class',CLASS,'--approve-stop-staging','--timeout',str(a.timeout),'--output-dir',str(out/'backup')]
        r=cmd(argv,timeout=1200,ok=False); (out/'backup-command.log').write_text(r.stdout+r.stderr)
        need(r.returncode==0,'real CSI backup failed; inspect backup-command.log')
    request=load('backup/request.json'); need(len(request['snapshots'])==10,'backup scope incomplete')
    originals=[get('volumesnapshotcontent',s['boundVolumeSnapshotContentName']) for s in request['snapshots']]
    if not (out/'source-snapshot-content.json').exists(): save('source-snapshot-content.json',originals)
    print('Ten real CSI snapshots ready; stopped-source inventories retained',flush=True)
    network=packet_controls(image)
    pilot='kerosene-restore-'+a.run_id+'-pilot'; main='kerosene-restore-'+a.run_id
    namespace(pilot)
    serviceaccount={'apiVersion':'v1','kind':'ServiceAccount','metadata':meta('restore-check',LAB),'automountServiceAccountToken':False}
    create(serviceaccount)
    configs=[]
    for s in request['snapshots']:
        name=s['persistentVolumeClaim']; kind=next((x for x in ('postgres','redis','bitcoin','lnd','tor') if name.endswith('-'+x+'-0')),'vault')
        configs.append({'kind':kind,'expectedTreeDigest':profiles[s['namespace']+'/'+name]['expectedTreeDigest'],'egressTargets':network['targets'],'timeout':a.timeout})
    pilot_config=configs[0]; pilot_pod=offline_pod(0,pilot,pilot_config,image,probe,fn)
    allowed={'node':NODE,'objects':{}}
    if (out/'admission-allow.json').exists(): allowed=load('admission-allow.json')
    else:
        for ns,count in ((pilot,1),(main,10)):
            allow_entry(allowed,deny_policy(ns),'networkpolicies',LAB)
            sa=copy.deepcopy(serviceaccount); sa['metadata']['namespace']=ns
            allow_entry(allowed,sa,'serviceaccounts',LAB)
            for i in range(count):
                cfg=configs[i]; o=offline_pod(i,ns,cfg,image,probe,fn)
                allow_entry(allowed,o,'pods',LAB)
                claim=pvc('restore-'+str(i),ns,STORAGE+'-wffc' if ns==pilot else STORAGE,'import-'+str(i))
                # Production uses original ready snapshot size.
                claim['spec']['resources']['requests']['storage']=request['snapshots'][i]['restoreSize']
                allow_entry(allowed,claim,'persistentvolumeclaims',LAB)
                _,snap=import_pair(ns,i,request['snapshots'][i]); allow_entry(allowed,snap,'volumesnapshots',LAB)
    admission_install(allowed,[pilot,main],image)
    create(deny_policy(pilot)); sa=copy.deepcopy(serviceaccount); sa['metadata']['namespace']=pilot; create(sa)
    admission=admission_tests(pilot,pilot_pod)
    # Actual backend alias import and WFFC restore BEFORE the lab-only approval declaration.
    content,snap=import_pair(pilot,0,request['snapshots'][0]); create(content); create(snap)
    ready=wait('volumesnapshot','import-0',pilot,lambda x:x.get('status',{}).get('readyToUse') is True)
    claim=pvc('restore-0',pilot,STORAGE+'-wffc','import-0'); claim['spec']['resources']['requests']['storage']=request['snapshots'][0]['restoreSize']; create(claim)
    pilot_result=json.loads(runpod(pilot_pod,'pilot-probe'))
    need(pilot_result['status']=='passed','real import pilot probe failed')
    original=originals[0]; alias=get('volumesnapshotcontent',pilot+'-0'); pv=get('pv',get('pvc','restore-0',pilot)['spec']['volumeName'])
    need(alias['spec']['deletionPolicy']=='Retain' and alias['spec']['source']['snapshotHandle']==original['status']['snapshotHandle'],'import does not retain same backend snapshot')
    need(alias['spec']['volumeSnapshotRef']['uid']==ready['metadata']['uid'] and pv['spec']['csi']['volumeHandle']!=original['spec']['source']['volumeHandle'],'import binding/source volume reused')
    save('retained-import-pilot.json',{'snapshotUid':ready['metadata']['uid'],'contentUid':alias['metadata']['uid'],'pvUid':pv['metadata']['uid'],'result':pilot_result,'storageMode':'WaitForFirstConsumer','sourceHandleDigest':'sha256:'+hashlib.sha256(original['status']['snapshotHandle'].encode()).hexdigest(),'restoredHandleDigest':'sha256:'+hashlib.sha256(pv['spec']['csi']['volumeHandle'].encode()).hexdigest()})
    approval={'context':CONTEXT,'namespace':main,'requestDigest':digest(request),'approvedBy':'authorized-disposable-lab-runner','changeId':a.run_id,'driver':'hostpath.csi.k8s.io','storageClass':STORAGE,'probeImage':image,'networkIsolationVerified':True,'retainedHandleImportSupported':True,'admissionAllowsOfflineProbeOnly':True,'egressTargets':network['targets'],'classification':CLASSIFICATION,'qualificationInputs':{'admissionDigest':digest(admission),'networkDigest':digest(network),'pilotDigest':digest(load('retained-import-pilot.json'))}}
    save('lab-isolation-approval.json',approval)
    environment=dict(ENV); environment.pop('KUBECTL',None)
    collection=subprocess.run(['bash',str(REPO/'infra/kubernetes/scripts/collect-staging-volumesnapshot-attestation-request.sh'),
          '--context',CONTEXT,'--release-id',request['release']['id'],'--release-lock-digest',request['release']['lockDigest'],
          '--snapshot-selector',request['selection']['labelSelector'],'--requested-at',request['requestedAt']],
          capture_output=True,text=True,env=environment,timeout=90)
    need(collection.returncode==0 and json.loads(collection.stdout)==request,'stock collector differs from live request')
    (out/'backup/request-stock-collector.json').write_text(collection.stdout)
    evidence=production_restore(request,approval,configs,probe,fn)
    need(evidence['restoreTested'] is True and len(evidence['probes'])==10 and all(x['result']['status']=='passed' for x in evidence['probes']),'ten actual probes required')
    for old in load('source-snapshot-content.json'):
        current=get('volumesnapshotcontent',old['metadata']['name'])
        need(current['metadata']['uid']==old['metadata']['uid'] and current['spec']==old['spec'],'original snapshot content binding changed')
    need((out/'utility-negative-tests.json').exists(),'snapshots/probes passed; final qualification requires negative-tools phase')
    utilities=load('utility-negative-tests.json'); need(utilities['image']==image and all(r['passed'] for r in utilities['results']),'actual utility negative tests missing')
    save('qualification.json',{'classification':CLASSIFICATION,'qualified':True,'context':CONTEXT,'at':stamp(),'restoreEvidenceDigest':digest(evidence),'admissionTestsDigest':digest(admission),'packetControlsDigest':digest(network),'utilityTestsDigest':digest(utilities),'tools':versions,'image':image,'actualSnapshots':10,'actualProbes':10,'pilotWFFC':True,'originalContentBindingsUnchanged':True,'providerReceiptProduced':False,'resourcesRetained':True,'productionRestoreCliInvoked':True,'limitations':['synthetic Bitcoin layout; no consensus or LevelDB semantics','synthetic bbolt buckets; no LND recovery','synthetic Tor/Vault envelopes; no signing/decryption','single-node hostpath CSI; no production provider qualification','historical collector serialization failure fixed separately; ordinary stock CLI now used','full failure-mode/attestation matrix not claimed']})
    print('REAL disposable CSI/CNI/admission qualification passed: '+str(out/'qualification.json'),flush=True)

try:
    identity()
    if a.phase=='preflight': save('preflight.json',json.loads(kub('get','pods','-A','-o','json').stdout))
    elif a.phase=='prepare': prepare()
    elif a.phase=='install': install()
    elif a.phase=='repair-network': repair_network()
    elif a.phase=='repair-admission': repair_admission()
    elif a.phase=='repair-admission-server': repair_admission_server()
    elif a.phase=='build': build()
    elif a.phase=='release-import-staging': release_import_staging()
    elif a.phase=='recover-collector': recover_collector()
    elif a.phase=='negative-tools': negative_tools()
    elif a.phase=='packets':
        namespace(LAB); packet_controls(load('tools-image.json')['image'])
    elif a.phase=='revalidate-stock': revalidate_stock()
    else: qualify()
except Exception as exc:
    previous=out/('blocker-'+a.phase+'.json')
    if previous.exists():
        raw=previous.read_bytes(); history=out/'blocker-history'
        history.mkdir(mode=0o700,exist_ok=True)
        retained=history/(previous.stem+'-'+hashlib.sha256(raw).hexdigest()+'.json')
        if not retained.exists():
            with retained.open('xb') as stream: stream.write(raw)
    save('blocker-'+a.phase+'.json',{'classification':CLASSIFICATION,'phase':a.phase,'error':str(exc),'at':stamp(),'resourcesRetained':True,'qualified':False})
    for r in ('events','pods'):
        result=kub('get',r,'-A','-o','json',ok=False); (out/('failure-'+r+'.json')).write_text(result.stdout)
    print('REAL LAB BLOCKER: '+str(exc)+'\nEvidence retained in '+str(out),file=sys.stderr)
    sys.exit(78)
