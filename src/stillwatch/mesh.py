"""Idempotent event/state replication; transport never carries credentials."""
import hashlib
import json
import math
import time
import unicodedata
import uuid

WINDOW = 300
KINDS = {'event', 'decision', 'dismiss'}
DECISIONS = {'quiet', 'immediate', 'immediate_delayed', 'immediate_repeat', 'unavailable', 'excluded', 'busy'}

def fingerprint(app, title, body):
    def normal(value):
        return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    app = normal(app).removesuffix('.desktop')
    app = {'com.slack.slack': 'slack', 'org.gnome.nautilus': 'files', 'brave browser': 'brave', 'com.brave.browser': 'brave'}.get(app, app)
    # Preserve numbers, URLs, names and bodies: separate events must stay separate.
    return hashlib.sha256('\0'.join((app, normal(title), normal(body))).encode()).hexdigest()

def initialize(db):
    columns = {row[1] for row in db.execute('PRAGMA table_info(notifications)')}
    if 'fingerprint' not in columns:
        db.execute("ALTER TABLE notifications ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
    db.execute('CREATE INDEX IF NOT EXISTS fingerprint_idx ON notifications(fingerprint,received)')
    db.execute('CREATE TABLE IF NOT EXISTS mesh_identity (id INTEGER PRIMARY KEY CHECK(id=1), device TEXT NOT NULL, label TEXT NOT NULL)')
    db.execute('INSERT OR IGNORE INTO mesh_identity VALUES (1,?,?)', (str(uuid.uuid4()), 'This device'))
    db.execute('CREATE TABLE IF NOT EXISTS mesh_events (event TEXT PRIMARY KEY, notification INTEGER NOT NULL, device TEXT NOT NULL, label TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS mesh_operations (seq INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS mesh_tombstones (event TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS mesh_peers (peer TEXT PRIMARY KEY, sent INTEGER NOT NULL DEFAULT 0, received INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE TABLE IF NOT EXISTS mesh_authority (id INTEGER PRIMARY KEY CHECK(id=1), device TEXT NOT NULL)')
    # Old local history is not broadcast automatically when pairing.
    for ident, app, title, body in db.execute("SELECT id,app,title,body FROM notifications WHERE fingerprint='' ").fetchall():
        db.execute('UPDATE notifications SET fingerprint=? WHERE id=?', (fingerprint(app,title,body),ident))

def identity(db, label=None):
    if label is not None:
        if not isinstance(label, str) or not label.strip() or len(label)>80:
            raise ValueError('invalid_device_label')
        db.execute('UPDATE mesh_identity SET label=? WHERE id=1', (label.strip(),))
    return db.execute('SELECT device,label FROM mesh_identity WHERE id=1').fetchone()

def operation(db, kind, payload):
    db.execute('INSERT INTO mesh_operations(operation,kind,payload) VALUES (?,?,?)', (str(uuid.uuid4()),kind,json.dumps(payload,separators=(',',':'))))

def authority(db):
    row = db.execute('SELECT device FROM mesh_authority WHERE id=1').fetchone()
    return row[0] if row else None

def set_authority(db, device):
    _uuid(device)
    db.execute('INSERT OR REPLACE INTO mesh_authority VALUES (1,?)', (device,))

def can_classify(db):
    owner = authority(db)
    return owner is None or owner == identity(db)[0]

def _event(db, payload):
    existing = db.execute('SELECT notification FROM mesh_events WHERE event=?', (payload['event'],)).fetchone()
    if existing:
        return existing[0], False
    digest = fingerprint(payload['app'],payload['title'],payload['body'])
    existing = db.execute('SELECT id FROM notifications WHERE fingerprint=? AND ABS(received-?) <= ? ORDER BY id LIMIT 1', (digest,payload['received'],WINDOW)).fetchone()
    created = not existing
    if existing:
        ident = existing[0]
    else:
        ident = db.execute('INSERT INTO notifications(received,app,title,body,fingerprint) VALUES (?,?,?,?,?)', (payload['received'],payload['app'],payload['title'],payload['body'],digest)).lastrowid
    db.execute('INSERT INTO mesh_events VALUES (?,?,?,?)', (payload['event'],ident,payload['device'],payload['label']))
    if db.execute('SELECT 1 FROM mesh_tombstones WHERE event=?', (payload['event'],)).fetchone():
        _dismiss(db, [payload['event']])
    return ident, created

def capture(db, app, title, body, received=None):
    device,label=identity(db)
    payload={'event':str(uuid.uuid4()),'device':device,'label':label,'received':time.time() if received is None else received,'app':app,'title':title,'body':body}
    ident,created=_event(db,payload)
    operation(db,'event',payload)
    return ident,created

def _dismiss(db, events):
    db.executemany('INSERT OR IGNORE INTO mesh_tombstones VALUES (?)', ((event,) for event in events))
    for event in events:
        row=db.execute('SELECT notification FROM mesh_events WHERE event=?',(event,)).fetchone()
        if row:
            db.execute('UPDATE notifications SET attention_dismissed=1 WHERE id=?',(row[0],))
            db.execute('INSERT OR IGNORE INTO mesh_tombstones SELECT event FROM mesh_events WHERE notification=?',(row[0],))

def dismiss(db, ident):
    events=[row[0] for row in db.execute('SELECT event FROM mesh_events WHERE notification=?',(ident,))]
    db.execute('UPDATE notifications SET attention_dismissed=1 WHERE id=?',(ident,))
    if events:
        _dismiss(db,events)
        operation(db,'dismiss',{'events':events})
    db.commit()

def publish_decision(db, ident, decision, probability, category, severity, issue_key, commit=True):
    if not can_classify(db):
        raise RuntimeError('only_the_authority_can_classify')
    db.execute('UPDATE notifications SET decision=?,probability=?,category=?,severity=?,issue_key=? WHERE id=?',(decision,probability,category,severity,issue_key,ident))
    event=db.execute('SELECT event FROM mesh_events WHERE notification=? LIMIT 1',(ident,)).fetchone()
    if event:
        operation(db,'decision',{'event':event[0],'producer':identity(db)[0],'decision':decision,'probability':probability,'category':category,'severity':severity,'issue_key':issue_key})
    if commit:
        db.commit()

def _uuid(value):
    if not isinstance(value,str) or str(uuid.UUID(value)) != value:
        raise ValueError('invalid_event_identifier')

def validate(item):
    if not isinstance(item,dict) or item.get('kind') not in KINDS or not isinstance(item.get('payload'),dict):
        raise ValueError('invalid_operation')
    _uuid(item.get('operation'))
    payload=item['payload']
    if item['kind']=='event':
        _uuid(payload.get('event'));_uuid(payload.get('device'))
        for key,limit in [('app',200),('title',10000),('body',10000),('label',80)]:
            if not isinstance(payload.get(key),str) or len(payload[key])>limit:
                raise ValueError('invalid_event_text')
        stamp=payload.get('received')
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<0 or stamp>time.time()+300:
            raise ValueError('invalid_event_time')
    elif item['kind']=='decision':
        _uuid(payload.get('event'))
        if 'producer' in payload:
            _uuid(payload['producer'])
        if payload.get('decision') not in DECISIONS or payload.get('category') not in {'downtime','slack','agent','meeting','other'} or payload.get('severity') not in {'attention','severe'}:
            raise ValueError('invalid_event_decision')
        probability=payload.get('probability')
        if probability is not None and (isinstance(probability,bool) or not isinstance(probability,(int,float)) or not math.isfinite(probability) or not 0<=probability<=1):
            raise ValueError('invalid_event_probability')
        key=payload.get('issue_key')
        if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('invalid_issue_key')
    else:
        if not isinstance(payload.get('events'),list) or not 1<=len(payload['events'])<=10000:
            raise ValueError('invalid_dismissal')
        for event in payload['events']:_uuid(event)

def ingest(db, items):
    if not isinstance(items,list) or len(items)>64:
        raise ValueError('invalid_batch')
    for item in items:validate(item)
    for item in items:
        if db.execute('SELECT 1 FROM mesh_operations WHERE operation=?',(item['operation'],)).fetchone():
            continue
        payload=item['payload']
        if item['kind']=='event':
            _event(db,payload)
        elif item['kind']=='dismiss':
            _dismiss(db,payload['events'])
        else:
            row=db.execute('SELECT notification FROM mesh_events WHERE event=?',(payload['event'],)).fetchone()
            owner = authority(db)
            if row and (owner is None or payload.get('producer') == owner):
                db.execute('UPDATE notifications SET decision=?,probability=?,category=?,severity=?,issue_key=? WHERE id=?',(payload['decision'],payload['probability'],payload['category'],payload['severity'],payload['issue_key'],row[0]))
        db.execute('INSERT INTO mesh_operations(operation,kind,payload) VALUES (?,?,?)',(item['operation'],item['kind'],json.dumps(payload,separators=(',',':'))))

def export(db, after, limit=64):
    if isinstance(after,bool) or not isinstance(after,int) or after<0:
        raise ValueError('invalid_cursor')
    rows=db.execute('SELECT seq,operation,kind,payload FROM mesh_operations WHERE seq>? ORDER BY seq LIMIT ?',(after,limit)).fetchall()
    cursor=rows[-1][0] if rows else after
    return cursor,[{'operation':op,'kind':kind,'payload':json.loads(data)} for _,op,kind,data in rows]

def exchange(db, request):
    if not isinstance(request,dict) or request.get('version')!=1:
        raise ValueError('invalid_protocol')
    after=request.get('after'); sent=request.get('sent')
    for value in (after,sent):
        if isinstance(value,bool) or not isinstance(value,int) or value<0:
            raise ValueError('invalid_cursor')
    with db:
        ingest(db,request.get('operations'))
        cursor,items=export(db,after)
    return {'version':1,'ack':sent,'cursor':cursor,'operations':items,'authority':authority(db),'device':identity(db)[0]}

def prune(db):
    # Retain tiny dismissal tombstones so offline devices cannot revive handled events.
    db.execute('DELETE FROM mesh_events WHERE notification NOT IN (SELECT id FROM notifications)')
    db.execute("DELETE FROM mesh_operations WHERE kind IN ('event','decision') AND json_extract(payload,'$.event') NOT IN (SELECT event FROM mesh_events)")

def sources(db, ident):
    return [row[0] for row in db.execute('SELECT DISTINCT label FROM mesh_events WHERE notification=? ORDER BY label',(ident,))]
