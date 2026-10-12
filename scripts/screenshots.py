#!/usr/bin/env python3
"""Render only Stillwatch's widgets with isolated, fictional notification data."""
import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/screenshots'
OUTPUT.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='stillwatch-screenshots-') as temporary:
    os.environ.update(XDG_STATE_HOME=temporary, XDG_CONFIG_HOME=temporary, TZ='UTC')
    if hasattr(__import__('time'), 'tzset'):
        __import__('time').tzset()
    sys.path.insert(0, str(ROOT / 'src'))
    from stillwatch.history import connect, record
    from stillwatch import mesh
    db = connect()
    mesh.identity(db, 'Laptop')
    examples = [
        ('Service Monitor', 'Example: checkout service is down', 'The checkout service is unavailable and customers cannot complete orders.', 'downtime', 'severe', 'immediate'),
        ('Team Chat', 'Example: a direct ping', 'Can you review the deployment plan?', 'slack', 'attention', 'immediate'),
        ('Coding Agent', 'Example: sudo approval needed', 'The agent is waiting for your sudo step to install a dependency.', 'agent', 'attention', 'immediate'),
        ('Calendar', 'Example: meeting in 10 minutes', 'Project sync starts in 10 minutes.', 'meeting', 'attention', 'immediate'),
        ('Files', 'Example: copy completed', 'Copied the sample files successfully.', 'other', 'attention', 'quiet'),
    ]
    # Fixed fictional clock times; never copy a real notification timestamp.
    today = dt.datetime.now(dt.timezone.utc).date()
    for index, (app,title,body,category,severity,decision) in enumerate(examples):
        ident = record(db,app,title,body)
        stamp = dt.datetime.combine(today,dt.time(9,30+index),tzinfo=dt.timezone.utc).timestamp()
        db.execute('UPDATE notifications SET received=?,category=?,severity=?,decision=? WHERE id=?',(stamp,category,severity,decision,ident))
        if index == 0:
            mesh.ingest(db,[{'operation':str(uuid.uuid4()),'kind':'event','payload':{
                'event':str(uuid.uuid4()),'device':str(uuid.uuid4()),'label':'Workstation',
                'received':stamp,'app':app,'title':title,'body':body,
            }}])
        db.commit()
    db.close()
    env = {**os.environ, 'PYTHONPATH': str(ROOT/'src'), 'STILLWATCH_SCREENSHOT_DIR': str(OUTPUT)}
    for args in [('view','--smoke-test','--capture'), ('demo','--smoke-test','--capture'), ('demo','--severe','--smoke-test','--capture')]:
        subprocess.run([sys.executable,'-m','stillwatch',*args],env=env,check=True)
    for filename in ('history.png','attention.png','severe.png'):
        file=OUTPUT/filename
        if not file.exists() or file.stat().st_size < 1000:
            raise RuntimeError('Screenshot generation failed: '+filename)
        file.chmod(0o644)
print('Rendered three screenshots with fictional data in an isolated state directory.')
