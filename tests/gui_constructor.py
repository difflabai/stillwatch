"""Exercise the real-alert constructor, which demos deliberately skip.

Run with system PyGObject/libadwaita and an isolated session bus. No windows,
model calls, real notification history, or other applications are accessed.
"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
with tempfile.TemporaryDirectory(prefix='stillwatch-gui-constructor-') as directory:
    os.environ['XDG_STATE_HOME']=directory
    os.environ['XDG_CONFIG_HOME']=directory
    os.environ['ADW_DISABLE_PORTAL']='1'
    os.environ['GTK_USE_PORTAL']='0'
    from stillwatch import popup
    from stillwatch.history import record
    from stillwatch import mesh
    popup.DEMO=False
    app=popup.Attention()
    assert app.bus is not None and app.gate_subscription>0
    ident=record(app.db,'Test Agent','Awaiting approval','A fictional agent request.')
    app.current=[ident]
    app.close_requested()
    assert app.db.execute('SELECT attention_dismissed FROM notifications WHERE id=?',(ident,)).fetchone()[0]==1
    assert app.db.execute('SELECT count(*) FROM mesh_tombstones').fetchone()[0]==1
    app.bus.signal_unsubscribe(app.gate_subscription)
    app.db.close()
print('Real-alert Python/Gio construction and close-to-dismiss path passed without opening a window.')
