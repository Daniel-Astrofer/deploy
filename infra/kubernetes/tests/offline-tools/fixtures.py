"""Synthetic files only. Actual database engines used solely to populate lab PVCs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def run(argv):
    subprocess.run(argv, check=True, stdout=subprocess.DEVNULL)

def main():
    kind, directory = sys.argv[1:]
    root = Path(directory)
    assert root == Path('/data') and not list(root.iterdir()), 'fixture volume must be empty'
    uid = {'postgres': 70, 'redis': 999, 'bitcoin': 1000, 'tor': 1000, 'lnd': 0, 'vault': 65532}[kind]
    if kind == 'postgres':
        pg = root / 'pgdata'; pg.mkdir(); os.chown(pg, 70, 70)
        run(['su-exec','70:70','initdb','-D',str(pg),'--data-checksums','--auth=trust','--no-locale'])
        run(['su-exec','70:70','pg_ctl','-D',str(pg),'-l','/tmp/pg.log','-o',"-c listen_addresses='' -c unix_socket_directories=/tmp",'-w','start'])
        run(['su-exec','70:70','psql','-h','/tmp','-U','postgres','-d','postgres','-c',"CREATE TABLE synthetic AS SELECT generate_series(1,1000) AS n; CHECKPOINT;"])
        run(['su-exec','70:70','pg_ctl','-D',str(pg),'-m','fast','-w','stop'])
        run(['su-exec','70:70','pg_checksums','--check','-D',str(pg)])
    elif kind == 'redis':
        run(['redis-server','--port','0','--unixsocket','/tmp/lab-redis.sock','--dir',str(root),'--dbfilename','dump.rdb','--daemonize','yes','--save',''])
        run(['redis-cli','-s','/tmp/lab-redis.sock','SET','SYNTHETIC','disposable-only'])
        run(['redis-cli','-s','/tmp/lab-redis.sock','SAVE'])
        run(['redis-cli','-s','/tmp/lab-redis.sock','SHUTDOWN','NOSAVE'])
        run(['redis-check-rdb',str(root/'dump.rdb')])
    elif kind == 'lnd':
        for rel in ('data/chain/bitcoin/testnet/wallet.db','data/graph/testnet/channel.db'):
            f = root/rel; f.parent.mkdir(parents=True,exist_ok=True)
            run(['bbolt','fixture',str(f)]); run(['bbolt','check',str(f)])
    elif kind == 'bitcoin':
        for rel, data in {'testnet3/chainstate/CURRENT':b'MANIFEST-000001\n','testnet3/chainstate/MANIFEST-000001':b'SYNTHETIC layout only','testnet3/chainstate/000002.ldb':b'SYNTHETIC not LevelDB semantic validation','testnet3/blocks/index/MANIFEST-000001':b'SYNTHETIC'}.items():
            f=root/rel; f.parent.mkdir(parents=True,exist_ok=True); f.write_bytes(data)
    elif kind == 'tor':
        d=root/'onion'; d.mkdir(); pub=hashlib.sha256(b'SYNTHETIC-NOT-A-REAL-ONION-KEY').digest()
        suffix=b'\x03'; address=pub+hashlib.sha3_256(b'.onion checksum'+pub+suffix).digest()[:2]+suffix
        (d/'hostname').write_text(base64.b32encode(address).decode().lower()+'.onion\n')
        (d/'hs_ed25519_public_key').write_bytes(b'== ed25519v1-public: type0 ==\x00\x00\x00'+pub)
        (d/'hs_ed25519_secret_key').write_bytes(b'== ed25519v1-secret: type0 ==\x00\x00\x00'+b'SYNTHETIC-NOT-SIGNING-MATERIAL'.ljust(64,b'.'))
    elif kind == 'vault':
        d=root/'shares'; d.mkdir()
        (d/('share-'+hashlib.sha256(b'SYNTHETIC').hexdigest()+'.bin')).write_bytes(b'SYNTHETIC-NOT-AN-AEAD-CIPHERTEXT'.ljust(64,b'.'))
        (root/'economy.json').write_text(json.dumps({'synthetic':True}))
    else: raise ValueError('unknown kind')
    for parent, dirs, files in os.walk(root):
        os.chown(parent,uid,uid); os.chmod(parent,0o700)
        for name in files:
            os.chown(Path(parent)/name,uid,uid); os.chmod(Path(parent)/name,0o600)
    print(json.dumps({'fixture':kind,'synthetic':True,'uid':uid}))

if __name__ == '__main__': main()
