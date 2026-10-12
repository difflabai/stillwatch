#!/usr/bin/python3
"""Persistent, explicit-dismissal attention cards for model-approved alerts."""
import datetime as dt
import sys
import os
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk, Gdk, Gsk, Pango
from .history import connect
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

SEVERE_DEMO = '--demo-severe' in sys.argv
DEMO = '--demo' in sys.argv or SEVERE_DEMO
SMOKE = '--smoke-test' in sys.argv
CAPTURE = '--capture' in sys.argv

class Attention(Adw.Application):
    def __init__(self):
        super().__init__(application_id=('io.github.difflabai.Stillwatch.Demo.Severe' if SEVERE_DEMO else 'io.github.difflabai.Stillwatch.Demo') if DEMO else 'io.github.difflabai.Stillwatch.Attention', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self.db = connect()
        self.current = []
        self.transferring = False
        if not DEMO:
            def gate_available(connection, sender, path, interface, signal, parameters):
                if parameters.deep_unpack()[2]:
                    self.transferring = True
                    self.quit()
            Gio.DBus.session.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus', 'io.github.difflabai.Stillwatch.Gate', Gio.DBusSignalFlags.NONE, gate_available)

    def do_activate(self):
        if not self.window:
            self.window = Adw.ApplicationWindow(application=self, title='Stillwatch — Example' if DEMO else 'Stillwatch — Attention', default_width=620, default_height=380)
            self.window.set_modal(True)
            self.window.connect('close-request', self.close_requested)
            provider = Gtk.CssProvider()
            provider.load_from_string('''
                .attention-root { background: #171b22; color: #e9edf3; }
                .attention-content { border: 3px solid #f6c556; border-radius: 10px; background: #25221a; }
                .attention-content.severe { border-color: #ff6b6b; background: #24191d; }
                .attention-tag { color: #ffdb85; font-size: 11px; font-weight: bold; letter-spacing: 1px; }
                .severe .attention-tag { color: #ff8b8b; }
                .attention-title { color: #fff3f3; font-size: 19px; font-weight: bold; }
                .attention-body { color: #eadfe1; font-size: 14px; }
                .attention-dismiss { background: #9d7819; color: white; padding: 10px 24px; font-weight: bold; }
                .severe .attention-dismiss { background: #cf4254; }
            ''')
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.toolbar = Adw.ToolbarView()
            self.toolbar.add_css_class('attention-root')
            self.window.set_content(self.toolbar)
            header = Adw.HeaderBar()
            header.set_title_widget(Adw.WindowTitle(title='Stillwatch', subtitle='Example preview' if DEMO else 'Selected by Jev · NanoGPT'))
            self.toolbar.add_top_bar(header)
            self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            self.toolbar.set_content(self.scroller)
            self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
            self.scroller.set_child(self.list)
            if not DEMO:
                def sync():
                    active = [row[0] for row in self.db.execute("SELECT id FROM notifications WHERE decision='immediate' AND attention_dismissed=0 ORDER BY (severity='severe') DESC,id DESC")]
                    if active != self.current:
                        self.render()
                        if not self.current:
                            self.quit()
                    return GLib.SOURCE_CONTINUE
                GLib.timeout_add_seconds(1, sync)
        self.render()
        if not self.current and not DEMO:
            self.quit()
            return
        self.window.present()
        if SMOKE:
            GLib.timeout_add(1200, lambda: (self.quit(), GLib.SOURCE_REMOVE)[1])
        if CAPTURE:
            GLib.timeout_add(600, self.capture)

    def render(self):
        child = self.list.get_first_child()
        while child:
            after = child.get_next_sibling()
            self.list.remove(child)
            child = after
        if DEMO:
            if SEVERE_DEMO:
                rows = [(-1, 'Service Monitor · Example', 'Production is down', 'The service is unavailable and needs immediate intervention.\n\nThis is a demonstration; no outage is happening.', 'severe')]
            else:
                rows = [(-1, 'Coding Agent · Example', 'The agent needs your sudo approval', 'The agent is waiting for you to run a sudo command before it can continue.\n\nThis is a demonstration; no command needs to be run.', 'attention')]
        else:
            rows = self.db.execute("SELECT id,app,title,body,severity FROM notifications WHERE decision='immediate' AND attention_dismissed=0 ORDER BY (severity='severe') DESC,id DESC").fetchall()
        self.current = [row[0] for row in rows]
        for ident, app, title, body, severity in rows:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            card.add_css_class('attention-content')
            if severity == 'severe':
                card.add_css_class('severe')
            inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=22, margin_end=22, margin_top=20, margin_bottom=20)
            card.append(inside)
            tag = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            tag.append(Gtk.Image(icon_name='dialog-warning-symbolic', pixel_size=22))
            text = 'DROP EVERYTHING · SEVERE' if severity == 'severe' else 'NEEDS YOUR ATTENTION'
            label = Gtk.Label(label=('EXAMPLE · ' if DEMO else '') + text, xalign=0)
            label.add_css_class('attention-tag')
            tag.append(label)
            inside.append(tag)
            source = Gtk.Label(label=app, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            source.add_css_class('dim-label')
            inside.append(source)
            heading = Gtk.Label(label=title, xalign=0, wrap=True, selectable=True)
            heading.add_css_class('attention-title')
            inside.append(heading)
            content = Gtk.Label(label=body, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            content.add_css_class('attention-body')
            inside.append(content)
            bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            stays = Gtk.Label(label='Stays here until you dismiss it', xalign=0, hexpand=True, wrap=True)
            stays.add_css_class('dim-label')
            stays.add_css_class('caption')
            bottom.append(stays)
            if not DEMO:
                history = Gtk.Button(label='History')
                history.connect('clicked', lambda *_: Gio.DesktopAppInfo.new('io.github.difflabai.Stillwatch.desktop').launch([], None))
                bottom.append(history)
            dismiss = Gtk.Button(label='Dismiss Example' if DEMO else 'Dismiss')
            dismiss.add_css_class('attention-dismiss')
            dismiss.connect('clicked', lambda _, item=ident: self.dismiss(item))
            bottom.append(dismiss)
            inside.append(bottom)
            self.list.append(card)

    def dismiss(self, ident):
        if DEMO:
            self.quit()
            return
        self.db.execute('UPDATE notifications SET attention_dismissed=1 WHERE id=?', (ident,))
        self.db.commit()
        self.render()
        if not self.current:
            self.quit()

    def close_requested(self, *_):
        if not DEMO and not self.transferring:
            self.db.executemany('UPDATE notifications SET attention_dismissed=1 WHERE id=?', ((ident,) for ident in self.current))
            self.db.commit()
        return False

    def capture(self):
        # Render our own widget, without capturing the user's desktop or other apps.
        paintable = Gtk.WidgetPaintable.new(self.list)
        snapshot = Gtk.Snapshot()
        width, height = self.list.get_width(), self.list.get_height()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node:
            renderer = Gsk.Renderer.new_for_surface(self.window.get_surface())
            texture = renderer.render_texture(node, None)
            texture.save_to_png(str(Path(os.environ.get('STILLWATCH_SCREENSHOT_DIR', '.')) / ('severe.png' if SEVERE_DEMO else 'attention.png')))
            renderer.unrealize()
        return GLib.SOURCE_REMOVE

def run(demo=False, severe=False, smoke=False, capture=False):
    global DEMO, SEVERE_DEMO, SMOKE, CAPTURE
    DEMO, SEVERE_DEMO, SMOKE, CAPTURE = demo, severe, smoke, capture
    Attention().run([sys.argv[0]])
