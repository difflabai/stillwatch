"""Opt-in, TLS-pinned mobile bridge. Credentials are never mesh operations."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import urllib.parse
import uuid

from .paths import CONFIG_DIR, private_directory, private_write
from . import mesh

SETTINGS=CONFIG_DIR/'phone-config.json'
CLIENTS=CONFIG_DIR/'phone-clients.json'
TLS_DIR=CONFIG_DIR/'phone-tls'
MAX_BYTES=2_000_000

def configuration():
    if not SETTINGS.exists():
        raise RuntimeError('Configure the mobile bridge first.')
    result=json.loads(SETTINGS.read_text())
    if not isinstance(result.get('host'),str) or not isinstance(result.get('port'),int) or isinstance(result['port'],bool) or not 1<=result['port']<=65535:
        raise ValueError('invalid_phone_configuration')
    return result

def setup(host='127.0.0.1',port=8794):
    from .history import connect
    from .install import MANIFEST,session_environment
    if not MANIFEST.exists():raise RuntimeError('Install Stillwatch first.')
    with connect() as db:
        if mesh.authority(db)!=mesh.identity(db)[0]:raise RuntimeError('The mobile bridge belongs on the authoritative server.')
    if not isinstance(host,str) or not host.strip() or not 1<=port<=65535:
        raise ValueError('invalid_phone_binding')
    private_directory(TLS_DIR)
    cert,key=TLS_DIR/'server.crt',TLS_DIR/'server.key'
    if not cert.exists() or not key.exists():
        subprocess.run(['openssl','req','-x509','-newkey','rsa:3072','-nodes','-keyout',str(key),'-out',str(cert),'-days','3650','-subj','/CN=Stillwatch Mobile/O=difflabai'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cert.chmod(0o600);key.chmod(0o600)
    private_write(SETTINGS,json.dumps({'host':host,'port':port},indent=2))
    session_environment()
    subprocess.run(['systemctl','--user','enable','--now','stillwatch-phone.service'],check=True)
    subprocess.run(['systemctl','--user','restart','stillwatch-phone.service'],check=True)
    print('TLS mobile bridge enabled. Pairing and phone notification access remain explicit opt-ins.')

def pair(url,output,label='Phone'):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Use an HTTPS server URL without credentials or extra paths.')
    if not label.strip() or len(label)>80:raise ValueError('invalid_phone_label')
    configuration()
    device=str(uuid.uuid4())
    token=secrets.token_urlsafe(32)
    values=json.loads(CLIENTS.read_text()) if CLIENTS.exists() else {}
    values[device]={'token_hash':hashlib.sha256(token.encode()).hexdigest(),'label':label.strip()}
    private_write(CLIENTS,json.dumps(values,indent=2))
    cert=ssl.PEM_cert_to_DER_cert((TLS_DIR/'server.crt').read_text())
    pairing={'version':1,'url':url.rstrip('/'),'token':token,'device':device,'label':label.strip(),'certificate_sha256':hashlib.sha256(cert).hexdigest()}
    target=Path(output).expanduser()
    private_write(target,json.dumps(pairing,indent=2))
    print('Saved private phone pairing file. Import it on the phone; never publish or commit it.')

def authenticate(token):
    if not isinstance(token,str) or not 16<=len(token)<=256:return None
    digest=hashlib.sha256(token.encode()).hexdigest()
    values=json.loads(CLIENTS.read_text()) if CLIENTS.exists() else {}
    for device,entry in values.items():
        if secrets.compare_digest(digest,entry['token_hash']):return device,entry['label']
    return None

def exchange(db,device,label,request):
    if mesh.authority(db)!=mesh.identity(db)[0]:raise RuntimeError('mobile_bridge_requires_authority')
    if not isinstance(request,dict) or request.get('version')!=1 or request.get('device')!=device:
        raise ValueError('invalid_mobile_request')
    after=request.get('after')
    if isinstance(after,bool) or not isinstance(after,int) or after<0:raise ValueError('invalid_cursor')
    operations=request.get('operations')
    if not isinstance(operations,list) or len(operations)>64:raise ValueError('invalid_mobile_batch')
    for item in operations:
        mesh.validate(item)
        if item['kind'] not in ('event','dismiss'):raise ValueError('phone_cannot_classify')
        if item['kind']=='event':
            payload=item['payload']
            if payload['device']!=device or payload['label']!=label or not isinstance(payload.get('cloud_allowed',False),bool):
                raise ValueError('invalid_phone_identity_or_consent')
    with db:
        mesh.ingest(db,operations)
        for item in operations:
            if item['kind']=='event' and not item['payload'].get('cloud_allowed',False):
                event=item['payload']['event']
                row=db.execute('SELECT n.id,n.app,n.title,n.body,n.decision FROM notifications n JOIN mesh_events e ON e.notification=n.id WHERE e.event=?',(event,)).fetchone()
                if row and row[4]=='unclassified':
                    from .decision import issue_key
                    mesh.publish_decision(db,row[0],'excluded',None,'other','attention',issue_key(row[1],row[2],'other',row[3]),commit=False)
        # Mobile responses include full snapshots. Keep large Unicode messages
        # below Android's response bound while preserving ordered cursors.
        cursor,changes=mesh.export(db,after,limit=8)
        identifiers=set()
        deleted=set()
        for change in changes:
            payload=change['payload']
            events=payload.get('events',[]) if change['kind']=='dismiss' else [payload['event']]
            for event in events:
                row=db.execute('SELECT notification FROM mesh_events WHERE event=?',(event,)).fetchone()
                if row:identifiers.add(row[0])
                else:deleted.add(event)
        states=[]
        for ident in sorted(identifiers):
            row=db.execute('SELECT app,title,body,decision,severity,attention_dismissed FROM notifications WHERE id=?',(ident,)).fetchone()
            if not row:continue
            events=[r[0] for r in db.execute('SELECT event FROM mesh_events WHERE notification=?',(ident,))]
            states.append({'id':ident,'events':events,'app':row[0],'title':row[1],'body':row[2],'decision':row[3],'severity':row[4],'handled':bool(row[5])})
    return {'version':1,'authority':mesh.authority(db),'cursor':cursor,'ack':[item['operation'] for item in operations],'states':states,'removed_events':sorted(deleted)}

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()
    def log_message(self,*_):pass
    def reply(self,status,value):
        data=json.dumps(value,separators=(',',':')).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        if self.path!='/v1/phone/exchange':
            self.reply(404,{'error':'unknown_endpoint'});return
        token=self.headers.get('Authorization','')
        identity=authenticate(token[7:] if token.startswith('Bearer ') else '')
        if not identity:
            self.reply(401,{'error':'unauthorized'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 1<=size<=MAX_BYTES or self.headers.get('Transfer-Encoding'):
                raise ValueError('invalid_request_size')
            request=json.loads(self.rfile.read(size))
            from .history import connect
            db=connect()
            try:response=exchange(db,*identity,request)
            finally:db.close()
            self.reply(200,response)
        except (ValueError,KeyError,TypeError):self.reply(400,{'error':'invalid_request'})
        except Exception as error:
            print('Mobile request unavailable:',type(error).__name__,flush=True)
            self.reply(503,{'error':'temporarily_unavailable'})

def run():
    settings=configuration()
    server=ThreadingHTTPServer((settings['host'],settings['port']),Handler)
    server.daemon_threads=True
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(TLS_DIR/'server.crt',TLS_DIR/'server.key')
    server.socket=context.wrap_socket(server.socket,server_side=True)
    print('TLS mobile bridge ready; pairing tokens and message contents are not logged.',flush=True)
    server.serve_forever()

def status():
    settings=configuration()
    count=len(json.loads(CLIENTS.read_text())) if CLIENTS.exists() else 0
    print('Paired phones:',count)
    print('Port:',settings['port'])
    print('Credentials remain private; the mobile API never sends a model key.')
