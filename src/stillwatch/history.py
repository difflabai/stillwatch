#!/usr/bin/python3
"""A bounded, local notification archive and native GNOME history browser."""
import datetime as dt
import fnmatch
from concurrent.futures import ThreadPoolExecutor
import html
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

os.umask(0o077)
from .paths import STATE_DIR
from . import mesh
STATE = STATE_DIR
STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
DATABASE = STATE / 'history.sqlite3'
MAX_ROWS = 1000
RETENTION = 7 * 86400

def connect():
    db = sqlite3.connect(DATABASE, timeout=5)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, received REAL NOT NULL, app TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS received_idx ON notifications(received)')
    columns = {column[1] for column in db.execute('PRAGMA table_info(notifications)')}
    for name, declaration in {
        'decision': "TEXT NOT NULL DEFAULT 'unclassified'",
        'probability': 'REAL',
        'attention_dismissed': 'INTEGER NOT NULL DEFAULT 0',
        'category': "TEXT NOT NULL DEFAULT 'other'",
        'severity': "TEXT NOT NULL DEFAULT 'attention'",
        'issue_key': "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            db.execute(f'ALTER TABLE notifications ADD COLUMN {name} {declaration}')
    db.execute('CREATE TABLE IF NOT EXISTS attention_cooldowns (issue_key TEXT PRIMARY KEY, last_alert REAL NOT NULL, severity TEXT NOT NULL)')
    from . import mesh
    mesh.initialize(db)
    db.commit()
    return db

def prune(db):
    db.execute("DELETE FROM notifications WHERE received < ? AND NOT (decision IN ('immediate','immediate_delayed') AND attention_dismissed=0)", (time.time() - RETENTION,))
    db.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?) AND NOT (decision IN ('immediate','immediate_delayed') AND attention_dismissed=0)", (MAX_ROWS,))
    db.execute('DELETE FROM attention_cooldowns WHERE last_alert < ?', (time.time() - RETENTION,))
    from . import mesh
    mesh.prune(db)
    db.commit()

def plain(value):
    return html.unescape(re.sub(r'</?(?:b|i|u|a|img|span)(?:\s[^>]*)?/?>', '', str(value)))[:10000]

def record(db, app, title, body):
    from . import mesh
    ident, _ = mesh.capture(db, plain(app)[:200], plain(title), plain(body))
    prune(db)
    return ident

def search(db, query='', details=False):
    # Literal substring matching: user-entered % and _ are not wildcards.
    query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    term = '%' + query + '%'
    columns = 'id,received,app,title,body,decision,category,attention_dismissed,severity' if details else 'received,app,title,body'
    return db.execute(f"SELECT {columns} FROM notifications WHERE (received >= ? OR (decision IN ('immediate','immediate_delayed') AND attention_dismissed=0)) AND (app LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') ORDER BY CASE WHEN decision IN ('immediate','immediate_delayed') AND attention_dismissed=0 AND severity='severe' THEN 0 WHEN decision IN ('immediate','immediate_delayed') AND attention_dismissed=0 THEN 1 WHEN decision LIKE 'immediate%' THEN 2 ELSE 3 END,id DESC LIMIT ?", (time.time() - RETENTION, term, term, term, MAX_ROWS)).fetchall()

def recorder():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    db = connect()
    prune(db)
    from .decision import config, classify, issue_key, allow_alert
    from . import mesh
    try:
        policy = config()
    except (OSError, ValueError):
        policy = {'enabled': False}
        print('Invalid AI policy; recording locally without cloud calls.', flush=True)
    executor = ThreadPoolExecutor(max_workers=4)
    pending = set()
    pending_ids = set()
    popup = [sys.executable, '-m', 'stillwatch', 'attention']
    controller = dbus.SessionBus(private=True)
    controller_name = dbus.service.BusName('io.github.difflabai.Stillwatch.Controller', bus=controller, do_not_queue=True)
    class AttentionSignals(dbus.service.Object):
        @dbus.service.signal('io.github.difflabai.Stillwatch.Attention', signature='s')
        def Show(self, payload):
            pass
        @dbus.service.signal('io.github.difflabai.Stillwatch.Attention', signature='t')
        def Withdraw(self, ident):
            pass
        @dbus.service.signal('io.github.difflabai.Stillwatch.Attention', signature='s')
        def Handled(self, payload):
            pass
    attention_signals = AttentionSignals(controller_name, '/io/github/difflabai/Stillwatch/Attention')
    shown_ids = set()
    handled_ids = {row[0] for row in db.execute('SELECT id FROM notifications WHERE attention_dismissed=1')}
    def dismissed(ident):
        mesh.dismiss(db, int(ident))
    controller.add_signal_receiver(dismissed, signal_name='Dismissed', dbus_interface='io.github.difflabai.Stillwatch.Gate', bus_name='io.github.difflabai.Stillwatch.Gate')
    def native_dismissed(payload):
        try:
            data = __import__('json').loads(payload)
            apps = data['apps']
            if not isinstance(apps, list) or not 1 <= len(apps) <= 5 or not all(isinstance(app, str) and len(app) <= 200 for app in apps):
                return
            if not all(isinstance(data.get(key), str) and len(data[key]) <= 10000 for key in ('title','body')):
                return
            digests = {mesh.fingerprint(app, plain(data['title']), plain(data['body'])) for app in apps}
            for ident, digest in db.execute('SELECT id,fingerprint FROM notifications WHERE attention_dismissed=0').fetchall():
                if digest in digests:
                    mesh.dismiss(db, ident)
        except (ValueError, KeyError, TypeError):
            print('Native dismissal rejected.', flush=True)
    controller.add_signal_receiver(native_dismissed, signal_name='NativeDismissed', dbus_interface='io.github.difflabai.Stillwatch.Gate', bus_name='io.github.difflabai.Stillwatch.Gate')
    def show_attention():
        rows = db.execute("SELECT id,app,title,body,severity FROM notifications WHERE decision='immediate' AND attention_dismissed=0 ORDER BY id").fetchall()
        shown_ids.update(row[0] for row in rows)
        if controller.name_has_owner('io.github.difflabai.Stillwatch.Gate'):
            for ident, app, title, body, severity in rows:
                attention_signals.Show(__import__('json').dumps({'id': ident, 'app': app, 'title': title, 'body': body, 'severity': severity}))
        else:
            subprocess.Popen(popup, stdout=subprocess.DEVNULL)
    def sync_handled():
        handled = {row[0] for row in db.execute('SELECT id FROM notifications WHERE attention_dismissed=1')}
        for ident in handled - handled_ids:
            row = db.execute('SELECT app,title,body FROM notifications WHERE id=?', (ident,)).fetchone()
            if row:
                attention_signals.Handled(__import__('json').dumps(dict(zip(('app','title','body'), row))))
        handled_ids.clear()
        handled_ids.update(handled)
        active = {row[0] for row in db.execute("SELECT id FROM notifications WHERE decision='immediate' AND attention_dismissed=0")}
        for ident in shown_ids - active:
            attention_signals.Withdraw(ident)
        shown_ids.intersection_update(active)
        if (policy.get('enabled') or policy.get('present_attention')) and active - shown_ids:
            show_attention()
        classify_waiting()
        return GLib.SOURCE_CONTINUE
    GLib.timeout_add_seconds(1, sync_handled)
    def gate_available(name, previous, current):
        if current and (policy.get('enabled') or policy.get('present_attention')):
            show_attention()
    controller.add_signal_receiver(gate_available, signal_name='NameOwnerChanged', dbus_interface='org.freedesktop.DBus', bus_name='org.freedesktop.DBus', arg0='io.github.difflabai.Stillwatch.Gate')
    def classified(future, ident, started):
        pending.discard(future)
        pending_ids.discard(ident)
        try:
            result = future.result()
            probability = result['probability']
            category = result['category']
            severity = result['severity']
            decision = 'immediate' if probability >= policy.get('threshold', .85) else 'quiet'
            # Never replay an interruption after a long model/network delay.
            if time.monotonic() - started > policy.get('max_delay_seconds', 10):
                decision = 'immediate_delayed' if decision == 'immediate' else 'quiet'
        except Exception as error:
            probability = None
            category = 'other'
            severity = 'attention'
            decision = 'unavailable'
            print('Notification decision unavailable:', type(error).__name__, flush=True)
        row = db.execute('SELECT app,title,body,received,attention_dismissed FROM notifications WHERE id=?', (ident,)).fetchone()
        if not row:
            return GLib.SOURCE_REMOVE
        fingerprint = issue_key(row[0], row[1], category, row[2])
        if decision == 'immediate' and time.time() - row[3] > policy.get('max_delay_seconds', 10):
            decision = 'immediate_delayed'
        if decision == 'immediate' and not allow_alert(db, fingerprint, severity, policy.get('cooldown_seconds', 300)):
            decision = 'immediate_repeat'
        mesh.publish_decision(db, ident, decision, probability, category, severity, fingerprint)
        if decision == 'immediate' and not row[4]:
            show_attention()
        return GLib.SOURCE_REMOVE
    def archive(app, title, body):
        record(db, app, title, body)
        classify_waiting()

    def classify_waiting():
        if not policy.get('enabled'):
            return
        for ident, app, title, body in db.execute("SELECT id,app,title,body FROM notifications WHERE decision='unclassified' AND attention_dismissed=0 AND EXISTS (SELECT 1 FROM mesh_events WHERE notification=notifications.id) ORDER BY id LIMIT 64").fetchall():
            if ident in pending_ids:
                continue
            fingerprint = issue_key(app, title, 'other', body)
            if any(fnmatch.fnmatchcase(str(app).casefold(), pattern.casefold()) for pattern in policy.get('excluded_apps', [])):
                mesh.publish_decision(db, ident, 'excluded', None, 'other', 'attention', fingerprint)
                continue
            if len(pending) >= 32:
                mesh.publish_decision(db, ident, 'busy', None, 'other', 'attention', fingerprint)
                continue
            started = time.monotonic()
            future = executor.submit(classify, plain(app), plain(title), plain(body), policy)
            pending.add(future)
            pending_ids.add(ident)
            future.add_done_callback(lambda result, item=ident, stamp=started: GLib.idle_add(classified, result, item, stamp))
    bus = dbus.SessionBus(private=True)
    owners = {}
    for name in ('org.freedesktop.Notifications', 'org.gtk.Notifications'):
        try:
            owners[name] = str(bus.get_name_owner(name))
        except dbus.DBusException:
            owners[name] = None
    def received(connection, message):
        try:
            if message.get_interface() == 'org.freedesktop.DBus' and message.get_member() == 'NameOwnerChanged':
                name, previous_owner, new_owner = message.get_args_list()
                if name in owners:
                    owners[name] = str(new_owner) or None
                return
            interface = message.get_interface()
            # Ubuntu forwards the same call from its public daemon to Shell.
            # Archive only the call to the public service to avoid duplicates.
            if interface not in owners or message.get_destination() not in (interface, owners[interface]):
                return
            if message.get_member() == 'Notify' and message.get_interface() == 'org.freedesktop.Notifications':
                args = message.get_args_list()
                archive(args[0], args[3], args[4])
            elif message.get_member() == 'AddNotification' and message.get_interface() == 'org.gtk.Notifications':
                app, notification_id, properties = message.get_args_list()
                archive(app, properties.get('title', ''), properties.get('body', ''))
        except (sqlite3.Error, IndexError, ValueError, TypeError) as error:
            # Never put message contents in the journal.
            print('Unable to archive notification:', type(error).__name__, flush=True)
    bus.add_message_filter(received)
    bus.call_blocking('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.Monitoring', 'BecomeMonitor', 'asu', ([
        "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
        "type='method_call',interface='org.gtk.Notifications',member='AddNotification'",
        "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.freedesktop.Notifications'",
        "type='signal',sender='org.freedesktop.DBus',interface='org.freedesktop.DBus',member='NameOwnerChanged',arg0='org.gtk.Notifications'",
    ], 0))
    print('Notification archive ready; seven-day retention, maximum 1000 entries.', flush=True)
    if policy.get('enabled') or policy.get('present_attention'):
        print('Jev attention filtering enabled; routine or undecided messages remain quiet.', flush=True)
        if db.execute("SELECT 1 FROM notifications WHERE decision='immediate' AND attention_dismissed=0 LIMIT 1").fetchone():
            show_attention()
    def cleanup():
        prune(db)
        return GLib.SOURCE_CONTINUE
    GLib.timeout_add_seconds(300, cleanup)
    GLib.MainLoop().run()

def viewer(smoke=False):
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gio, GLib, Gtk, Pango
    Gtk.Window.set_default_icon_name('io.github.difflabai.Stillwatch')
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    class History(Adw.Application):
        def __init__(self):
            super().__init__(application_id='io.github.difflabai.Stillwatch', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window = None
            self.db = connect()

        def do_activate(self):
            if self.window:
                self.render()
                self.window.present()
                return
            prune(self.db)
            self.window = Adw.ApplicationWindow(application=self, title='Stillwatch', default_width=720, default_height=780)
            from gi.repository import Gdk
            provider = Gtk.CssProvider()
            provider.load_from_string('''
                .history-root { background-color: #171b22; color: #e9edf3; }
                .history-root .card { background-color: #242a33; color: #e9edf3; }
                .history-root entry { background-color: #242a33; color: #e9edf3; }
                .history-downtime { border-left: 4px solid #f6c556; }
                .history-slack { border-left: 4px solid #62a0ea; }
                .history-agent { border-left: 4px solid #c061cb; }
                .history-meeting { border-left: 4px solid #f6c556; }
                .history-other { border-left: 4px solid #ff936b; }
                .history-severe { border-left: 4px solid #f66151; }
                .tag-downtime { color: #ffdb85; }
                .tag-slack { color: #86baff; }
                .tag-agent { color: #e298ea; }
                .tag-meeting { color: #ffdb85; }
                .tag-other { color: #ffb899; }
                .tag-severe { color: #ff8b80; }
            ''')
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            toolbar = Adw.ToolbarView()
            toolbar.add_css_class('history-root')
            self.window.set_content(toolbar)
            header = Adw.HeaderBar()
            header.set_title_widget(Adw.WindowTitle(title='Stillwatch', subtitle='Important items first · Seven-day history'))
            refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh history')
            refresh.connect('clicked', lambda *_: self.render())
            header.pack_start(refresh)
            clear = Gtk.Button(icon_name='edit-clear-all-symbolic', tooltip_text='Clear saved history')
            clear.connect('clicked', self.clear)
            header.pack_end(clear)
            toolbar.add_top_bar(header)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=18, margin_end=18, margin_top=12, margin_bottom=12)
            toolbar.set_content(box)
            self.entry = Gtk.SearchEntry(placeholder_text='Search messages, apps, or keywords…', hexpand=True)
            self.entry.connect('search-changed', lambda *_: self.render())
            box.append(self.entry)
            scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            box.append(scroller)
            self.stack = Gtk.Stack()
            scroller.set_child(self.stack)
            self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.stack.add_named(self.list, 'messages')
            self.empty = Adw.StatusPage(icon_name='preferences-system-notifications-symbolic', title='All quiet', description='New notifications will appear here, even after their banners disappear.')
            self.stack.add_named(self.empty, 'empty')
            self.status = Gtk.Label(xalign=0)
            self.status.add_css_class('dim-label')
            self.status.add_css_class('caption')
            box.append(self.status)
            controller = Gtk.EventControllerKey()
            def key_pressed(controller, keyval, keycode, state):
                from gi.repository import Gdk
                if keyval == Gdk.KEY_Escape:
                    self.window.close()
                    return True
                if keyval == Gdk.KEY_f and state & Gdk.ModifierType.CONTROL_MASK:
                    self.entry.grab_focus()
                    return True
                return False
            controller.connect('key-pressed', key_pressed)
            self.window.add_controller(controller)
            self.render()
            self.version = self.db.execute('PRAGMA data_version').fetchone()[0]
            def sync_history():
                version = self.db.execute('PRAGMA data_version').fetchone()[0]
                if version != self.version:
                    self.version = version
                    self.render()
                return GLib.SOURCE_CONTINUE
            GLib.timeout_add_seconds(2, sync_history)
            self.window.present()
            self.entry.grab_focus()
            if smoke:
                GLib.timeout_add(800, lambda: (self.quit(), GLib.SOURCE_REMOVE)[1])
            if '--capture' in sys.argv:
                def capture():
                    from gi.repository import Gsk
                    paintable = Gtk.WidgetPaintable.new(self.window.get_content())
                    snapshot = Gtk.Snapshot()
                    paintable.snapshot(snapshot, self.window.get_width(), self.window.get_height())
                    node = snapshot.to_node()
                    if node:
                        renderer = Gsk.Renderer.new_for_surface(self.window.get_surface())
                        renderer.render_texture(node, None).save_to_png(str(Path(os.environ.get('STILLWATCH_SCREENSHOT_DIR', '.')) / 'history.png'))
                        renderer.unrealize()
                    return GLib.SOURCE_REMOVE
                GLib.timeout_add(400, capture)

        def render(self):
            prune(self.db)
            child = self.list.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                self.list.remove(child)
                child = next_child
            rows = search(self.db, self.entry.get_text().strip(), details=True)
            self.stack.set_visible_child_name('messages' if rows else 'empty')
            if not rows:
                searching = bool(self.entry.get_text().strip())
                self.empty.set_title('No matches' if searching else 'All quiet')
                self.empty.set_description('Try another keyword or app name.' if searching else 'New notifications will appear here, even after their banners disappear.')
            current_day = None
            current_group = None
            today = dt.date.today()
            for ident, stamp, app, title, body, decision, category, dismissed, severity in rows:
                important = decision.startswith('immediate')
                pending_attention = decision in ('immediate', 'immediate_delayed') and not dismissed
                group = 'Drop everything · Severe' if pending_attention and severity == 'severe' else ('Needs attention' if pending_attention else ('Earlier important items' if important else 'Other notifications'))
                if group != current_group:
                    current_group = group
                    current_day = None
                    section = Gtk.Label(label=group, xalign=0, margin_top=8)
                    section.add_css_class('title-4')
                    self.list.append(section)
                when = dt.datetime.fromtimestamp(stamp)
                if when.date() != current_day:
                    current_day = when.date()
                    label = 'Today' if current_day == today else ('Yesterday' if current_day == today - dt.timedelta(days=1) else when.strftime('%A, %b %d'))
                    day = Gtk.Label(label=label, xalign=0, margin_top=8)
                    day.add_css_class('heading')
                    self.list.append(day)
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7, margin_start=0, margin_end=0)
                card.add_css_class('card')
                if important:
                    card.add_css_class('history-' + ('severe' if severity == 'severe' else category))
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=14, margin_end=14, margin_top=12, margin_bottom=12)
                card.append(inner)
                info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                labels = mesh.sources(self.db, ident)
                source_text = app + (' · ' + ' / '.join(labels) if labels else '')
                app_label = Gtk.Label(label=source_text, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
                app_label.set_tooltip_text(source_text)
                app_label.add_css_class('accent')
                info.append(app_label)
                clock = Gtk.Label(label=when.strftime('%H:%M'))
                clock.add_css_class('dim-label')
                info.append(clock)
                inner.append(info)
                if important:
                    names = {'downtime': 'Downtime', 'slack': 'Slack ping', 'agent': 'Agent needs you', 'meeting': 'Meeting', 'other': 'Important'}
                    prefix = 'DROP EVERYTHING · ' if severity == 'severe' else ('NEEDS ATTENTION · ' if pending_attention else 'IMPORTANT · ')
                    tag = Gtk.Label(label=prefix + names.get(category, 'Important'), xalign=0)
                    tag.add_css_class('tag-' + ('severe' if severity == 'severe' else category))
                    tag.add_css_class('heading')
                    inner.append(tag)
                    if decision == 'immediate_delayed':
                        delay = Gtk.Label(label='Decision arrived late · Popup suppressed', xalign=0)
                        delay.add_css_class('dim-label')
                        inner.append(delay)
                    if decision == 'immediate_repeat':
                        repeat = Gtk.Label(label='Repeated issue · Another popup suppressed', xalign=0)
                        repeat.add_css_class('dim-label')
                        inner.append(repeat)
                elif decision in ('unavailable', 'busy'):
                    review = Gtk.Label(label='Could not classify · Kept in history for review', xalign=0)
                    review.add_css_class('dim-label')
                    inner.append(review)
                heading = Gtk.Label(label=title, xalign=0, wrap=True, selectable=True)
                heading.add_css_class('heading')
                inner.append(heading)
                if body:
                    text = Gtk.Label(label=body, xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
                    inner.append(text)
                if pending_attention:
                    dismiss_button = Gtk.Button(label='Mark handled', halign=Gtk.Align.START)
                    def handled(_, item=ident):
                        mesh.dismiss(self.db, item)
                        self.render()
                    dismiss_button.connect('clicked', handled)
                    inner.append(dismiss_button)
                self.list.append(card)
            self.status.set_text(f'{len(rows)} notifications · Saved on this computer · Seven-day history')

        def clear(self, *_):
            dialog = Adw.AlertDialog(heading='Clear notification history?', body='This marks shared events handled on paired devices and removes the history saved on this computer.')
            dialog.add_response('cancel', 'Keep History')
            dialog.add_response('clear', 'Clear History')
            dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_close_response('cancel')
            def response(dialog, result):
                if dialog.choose_finish(result) == 'clear':
                    for (ident,) in self.db.execute('SELECT id FROM notifications').fetchall():
                        mesh.dismiss(self.db, ident)
                    self.db.execute('DELETE FROM notifications')
                    prune(self.db)
                    self.render()
            dialog.choose(self.window, None, response)

    History().run([sys.argv[0]])
