"""Exercise history pagination with real GTK widgets and fictional local data.

Run with an isolated session bus and an available display, like gui_constructor.py.
No real notification archive or model calls are used.
"""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
with tempfile.TemporaryDirectory(prefix='stillwatch-history-test-') as directory:
    os.environ.update(XDG_STATE_HOME=directory, XDG_CONFIG_HOME=directory,
                      ADW_DISABLE_PORTAL='1', GTK_USE_PORTAL='0')
    import gi
    gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib
    from stillwatch import history

    db = history.connect()
    db.executemany(
        'INSERT INTO notifications(received,app,title,body,fingerprint) VALUES (?,?,?,?,?)',
        [(time.time(), 'Test App', f'Notification {i:04}', 'Fictional message. ' * 20, str(i))
         for i in range(1000)],
    )
    db.commit()

    def cards(app):
        result = []
        child = app.list.get_first_child()
        while child:
            if child.has_css_class('card'):
                result.append(child)
            child = child.get_next_sibling()
        return result

    def exercise(app, *_):
        app.register(None)
        app.activate()
        assert len(cards(app)) == history.PAGE_SIZE
        assert '1–50 of 1000' in app.status.get_text()
        assert not app.previous.get_sensitive() and app.next.get_sensitive()

        # A sync cursor write must not recreate every notification widget.
        original = cards(app)[0]
        writer = sqlite3.connect(history.DATABASE)
        writer.execute("UPDATE mesh_identity SET label='Test device'")
        writer.commit()
        app.render()
        assert cards(app)[0] is original

        # Actual visible text and source changes must still refresh.
        writer.execute("UPDATE notifications SET body='Changed fixture' WHERE id=1000")
        writer.commit()
        app.render()
        assert cards(app)[0] is not original
        original = cards(app)[0]
        writer.execute("INSERT INTO mesh_events VALUES ('fixture',1000,'device','Laptop')")
        writer.commit()
        app.render()
        assert cards(app)[0] is not original

        app.change_page(1)
        assert len(cards(app)) == history.PAGE_SIZE
        assert '51–100 of 1000' in app.status.get_text()
        app.change_page(1000)
        assert '951–1000 of 1000' in app.status.get_text()
        assert not app.next.get_sensitive()

        # Searching must include notifications outside the first page.
        app.entry.set_text('Notification 0000')
        app.search_changed()
        assert app.page == 0 and len(cards(app)) == 1
        assert not app.previous.get_visible() and not app.next.get_visible()
        app.entry.set_text('No such fixture')
        app.search_changed()
        assert not cards(app) and app.stack.get_visible_child_name() == 'empty'
        app.entry.set_text('')
        app.search_changed()
        assert len(cards(app)) == history.PAGE_SIZE
        context = GLib.MainContext.default()
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline and context.pending():
            context.iteration(False)
        app.window.close()
        writer.close()
        app.db.close()
        return 0

    with patch.object(Adw.Application, 'run', exercise):
        history.viewer()
    db.close()
print('History: 1,000-row pagination, full search, and selective refresh passed.')
