"""Real checkers on disposable memory-backed fixtures; no restored/source data writes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

kind=sys.argv[1]
root=Path('/data')
subprocess.run(['python3','/opt/lab/fixtures.py',kind,str(root)],check=True,stdout=subprocess.DEVNULL)

def command(argv,success):
    r=subprocess.run(argv,capture_output=True,timeout=120)
    assert (r.returncode==0)==success, 'checker unexpectedly accepted/rejected fixture'
    return {'argv':argv,'exitCode':r.returncode,'stdoutDigest':hashlib.sha256(r.stdout).hexdigest(),'stderrDigest':hashlib.sha256(r.stderr).hexdigest()}

def hash_tree():
    h=hashlib.sha256()
    for f in sorted(root.rglob('*')):
        if f.is_file(): h.update(f.relative_to(root).as_posix().encode()); h.update(f.read_bytes())
    return h.hexdigest()

tests=[]
if kind=='postgres':
    pg=root/'pgdata'
    tests.append(command(['su-exec','70:70','pg_checksums','--check','-D',str(pg)],True))
    subprocess.run(['su-exec','70:70','pg_ctl','-D',str(pg),'-l','/tmp/pg-negative.log','-o',"-c listen_addresses='' -c unix_socket_directories=/tmp",'-w','start'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['su-exec','70:70','pg_ctl','-D',str(pg),'-m','immediate','-w','stop'],check=True,stdout=subprocess.DEVNULL)
    before=hash_tree()
    tests.append(command(['su-exec','70:70','pg_checksums','--check','-D',str(pg)],False))
    assert hash_tree()==before, 'unclean checker wrote to data'
    control=subprocess.run(['pg_controldata','-D',str(pg)],check=True,capture_output=True).stdout
    assert b'Database cluster state:               in production' in control
    # Recover this synthetic scratch cluster to clean shutdown, then damage an actual database page.
    subprocess.run(['su-exec','70:70','pg_ctl','-D',str(pg),'-l','/tmp/pg-negative.log','-o',"-c listen_addresses='' -c unix_socket_directories=/tmp",'-w','start'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['su-exec','70:70','pg_ctl','-D',str(pg),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
    f=max((f for f in (pg/'base').rglob('*') if f.is_file() and f.stat().st_size>=8192),key=lambda f:f.stat().st_size)
    with f.open('r+b') as stream:
        stream.seek(100); byte=stream.read(1); stream.seek(100); stream.write(bytes([byte[0]^1]))
    before=hash_tree()
    tests.append(command(['su-exec','70:70','pg_checksums','--check','-D',str(pg)],False))
    assert hash_tree()==before, 'checksum checker wrote to data'
elif kind=='redis':
    f=root/'dump.rdb'
    tests.append(command(['redis-check-rdb',str(f)],True))
    with f.open('r+b') as stream:
        stream.seek(-1,2); byte=stream.read(1); stream.seek(-1,2); stream.write(bytes([byte[0]^1]))
    before=hash_tree(); tests.append(command(['redis-check-rdb',str(f)],False)); assert hash_tree()==before
elif kind=='lnd':
    f=root/'data/graph/testnet/channel.db'
    before=hash_tree(); tests.append(command(['bbolt','check',str(f)],True)); assert hash_tree()==before
    # Destroy both meta magic words. Open/check must fail, and may not repair the file.
    with f.open('r+b') as stream:
        for offset in (16,4096+16): stream.seek(offset); stream.write(b'\0\0\0\0')
    before=hash_tree(); tests.append(command(['bbolt','check',str(f)],False)); assert hash_tree()==before
else: raise ValueError(kind)
print(json.dumps({'kind':kind,'passed':True,'actualUtilityResults':tests,'checkersLeftBytesUnchanged':True},sort_keys=True))
