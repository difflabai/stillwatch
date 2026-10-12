"""Private, authenticated SSH pairing; no listener or third-party relay."""
import json
import re
import subprocess
import sys
import time

from .paths import CONFIG_DIR, private_write
from . import mesh

PAIRING=CONFIG_DIR/'pairing.json'
MAX_BYTES=2_000_000

def peers():
    value=json.loads(PAIRING.read_text()) if PAIRING.exists() else {'peers':[]}
    if not isinstance(value,dict) or not isinstance(value.get('peers'),list):
        raise ValueError('invalid_pairing')
    for peer in value['peers']:
        if not isinstance(peer,dict) or not isinstance(peer.get('host'),str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',peer['host']):
            raise ValueError('invalid_ssh_host')
    return value['peers']

def label(value):
    from .history import connect
    with connect() as db:
        mesh.identity(db,value)
    print('Device label saved.')

def pair(host, device_label):
    from .install import MANIFEST, session_environment
    if not MANIFEST.exists():
        raise RuntimeError('Install Stillwatch first.')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',host):
        raise ValueError('invalid_ssh_host')
    label(device_label)
    configured=peers()
    if not any(peer['host']==host for peer in configured):
        configured.append({'host':host})
    private_write(PAIRING,json.dumps({'peers':configured},indent=2))
    session_environment()
    subprocess.run(['systemctl','--user','enable','--now','stillwatch-sync.service'],check=True)
    subprocess.run(['systemctl','--user','restart','stillwatch-sync.service'],check=True)
    print('SSH pairing enabled. Shared state remains on the paired devices.')

def once(db, host, invoke=None):
    db.execute('INSERT OR IGNORE INTO mesh_peers(peer) VALUES (?)',(host,))
    sent,received=db.execute('SELECT sent,received FROM mesh_peers WHERE peer=?',(host,)).fetchone()
    cursor,items=mesh.export(db,sent)
    request={'version':1,'after':received,'sent':cursor,'operations':items}
    db.commit()
    payload=json.dumps(request,separators=(',',':')).encode()
    if invoke:
        raw=invoke(payload)
    else:
        result=subprocess.run([
            'ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
            '-o','ConnectTimeout=5','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1',
            host,'exec .local/bin/stillwatch exchange',
        ],input=payload,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,check=True)
        raw=result.stdout
    if len(raw)>MAX_BYTES:
        raise ValueError('oversized_response')
    response=json.loads(raw)
    remote_cursor=response.get('cursor')
    if response.get('version')!=1 or response.get('ack')!=cursor or isinstance(remote_cursor,bool) or not isinstance(remote_cursor,int) or remote_cursor<received:
        raise ValueError('invalid_sync_response')
    with db:
        mesh.ingest(db,response.get('operations'))
        db.execute('UPDATE mesh_peers SET sent=?,received=? WHERE peer=?',(cursor,remote_cursor,host))
    return len(items)+len(response['operations'])

def exchange():
    from .history import connect
    raw=sys.stdin.buffer.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError('oversized_request')
    db=connect()
    try:
        response=mesh.exchange(db,json.loads(raw))
        sys.stdout.write(json.dumps(response,separators=(',',':'))+'\n')
    finally:
        db.close()

def run():
    from .history import connect
    db=connect()
    previous_errors={}
    try:
        while True:
            for peer in peers():
                host=peer['host']
                try:
                    once(db,host)
                    if host in previous_errors:
                        print('Paired device reconnected.',flush=True)
                        previous_errors.pop(host)
                except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                    db.rollback()
                    # No remote stderr, host details, credentials or notification text.
                    name=type(error).__name__
                    if previous_errors.get(host)!=name:
                        print('Paired device unavailable:',name,'; will retry.',flush=True)
                        previous_errors[host]=name
            time.sleep(2)
    finally:
        db.close()

def status():
    from .history import connect
    db=connect()
    try:
        print('Device label:',mesh.identity(db)[1])
        print('Paired devices:',len(peers()))
        print('Unique notifications:',db.execute('SELECT count(*) FROM notifications').fetchone()[0])
        print('Recorded deliveries:',db.execute('SELECT count(*) FROM mesh_events').fetchone()[0])
    finally:
        db.close()
