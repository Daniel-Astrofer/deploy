"""Fail closed, exact offline specs. No Kubernetes token or API client."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl

BOUNDARY = 'KEROSENE_OFFLINE_BOUNDARY'
ALLOW = json.loads(Path('/policy/allow.json').read_text())

def permitted(r):
    resource=r['resource']['resource']; sub=r.get('subResource','')
    obj=r.get('object') or {}; name=r.get('name') or obj.get('metadata',{}).get('name','')
    if sub == 'binding' and resource == 'pods':
        target=obj.get('target',{})
        return r.get('userInfo',{}).get('username') == 'system:kube-scheduler' and target.get('kind') == 'Node' and target.get('name') == ALLOW['node'] and '/'.join((r.get('namespace',''),resource,name)) in ALLOW['objects']
    if sub: return False  # status excluded by rules; exec/ephemeral/resize denied
    if r['operation'] not in ('CREATE','UPDATE'): return False
    key='/'.join((r.get('namespace',''),resource,name))
    expected=ALLOW.get('objects',{}).get(key)
    if expected is None:
        return resource=='serviceaccounts' and name=='default' and not any(obj.get(k) for k in ('secrets','imagePullSecrets')) and obj.get('automountServiceAccountToken') in (None,False)
    actual=copy.deepcopy(obj.get('spec',{}))
    if resource=='persistentvolumeclaims' and r['operation']=='UPDATE':
        user=r.get('userInfo',{}).get('username')
        if user not in ('system:kube-controller-manager','system:serviceaccount:default:csi-hostpathplugin-sa','system:kube-scheduler'): return False
        if user=='system:kube-scheduler':
            if actual.get('volumeName'): return False
            node=obj.get('metadata',{}).get('annotations',{}).get('volume.kubernetes.io/selected-node')
            if node not in (None,ALLOW['node']): return False
        actual.pop('volumeName',None)
    if resource=='pods' and r['operation']=='UPDATE':
        old=r.get('oldObject') or {}
        if actual.get('nodeName') not in (None,ALLOW['node']): return False
        actual.pop('nodeName',None)
        if obj.get('metadata',{}).get('uid') != old.get('metadata',{}).get('uid'): return False
    if actual != expected['spec']: return False
    if resource=='serviceaccounts':
        return obj.get('automountServiceAccountToken') is False and not obj.get('secrets') and not obj.get('imagePullSecrets')
    return True

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ready')
    def do_POST(self):
        try:
            review=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            r=review['request']; allowed=permitted(r)
            response={'uid':r['uid'],'allowed':allowed}
            if not allowed: response['status']={'code':403,'message':BOUNDARY+': exact reviewed offline spec required'}
            body=json.dumps({'apiVersion':'admission.k8s.io/v1','kind':'AdmissionReview','response':response}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        except Exception:
            self.send_response(500); self.end_headers()
    def log_message(self,*args): pass

if __name__ == '__main__':
    server=ThreadingHTTPServer(('0.0.0.0',8443),Handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain('/tls/tls.crt','/tls/tls.key')
    server.socket=ctx.wrap_socket(server.socket,server_side=True); server.serve_forever()
