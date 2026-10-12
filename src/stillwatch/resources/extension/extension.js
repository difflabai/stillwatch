import Gio from 'gi://Gio';
import St from 'gi://St';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `<node><interface name="io.github.difflabai.Stillwatch.Gate"><signal name="Dismissed"><arg type="t"/></signal></interface></node>`;

export default class AttentionGate extends Extension {
    enable() {
        this._queue = new Map();
        this._dismissed = new Set();
        this._retry = 0;
        this._dialog = new ModalDialog.ModalDialog({styleClass: 'attention-gate-modal', destroyOnClose: false, shouldFadeIn: false, shouldFadeOut: false});
        this._injections = new InjectionManager();
        // The separate decision service owns urgent cards. No original banner,
        // including app-declared CRITICAL notifications, bypasses that decision.
        this._injections.overrideMethod(MessageTray.MessageTray.prototype, '_showNotification', () => function () {
            this._notificationQueue = [];
            this.emit('queue-changed');
        });
        Main.messageTray._notificationQueue = [];
        if (Main.messageTray._banner)
            Main.messageTray._hideNotification(false);
        this._export = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._export.export(Gio.DBus.session, '/io/github/difflabai/Stillwatch/Gate');
        this._name = Gio.bus_own_name(Gio.BusType.SESSION, 'io.github.difflabai.Stillwatch.Gate', Gio.BusNameOwnerFlags.NONE, null, null, null);
        // Subscribe to the controller's authenticated bus owner, rather than
        // accepting arbitrary method calls that would bypass model classification.
        this._signal = Gio.DBus.session.signal_subscribe(
            'io.github.difflabai.Stillwatch.Controller', 'io.github.difflabai.Stillwatch.Attention', null,
            '/io/github/difflabai/Stillwatch/Attention', null, Gio.DBusSignalFlags.NONE,
            (_connection, _sender, _path, _iface, signal, parameters) => {
                try {
                    if (signal === 'Withdraw') {
                        const [ident] = parameters.deep_unpack();
                        this._dismissed.add(ident);
                        this._queue.delete(ident);
                        this._render();
                        return;
                    }
                    if (signal !== 'Show')
                        return;
                    const [payload] = parameters.deep_unpack();
                    const item = JSON.parse(payload);
                    if (!Number.isSafeInteger(item.id) || item.id < 1 || this._dismissed.has(item.id))
                        return;
                    if (!['app', 'title', 'body'].every(key => typeof item[key] === 'string'))
                        return;
                    if (!['severe', 'attention'].includes(item.severity))
                        return;
                    this._queue.set(item.id, item);
                    this._render();
                } catch (error) {
                    console.error('Attention card payload rejected');
                }
            });
    }

    _label(text, style) {
        const label = new St.Label({text, style_class: style, x_expand: true});
        label.clutter_text.line_wrap = true;
        label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        label.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        return label;
    }

    _render() {
        const item = [...this._queue.values()].sort((a, b) => Number(b.severity === 'severe') - Number(a.severity === 'severe'))[0];
        if (!item) {
            this._dialog.close();
            if (this._retry) {
                GLib.source_remove(this._retry);
                this._retry = 0;
            }
            return;
        }
        // Wait for any existing system modal (e.g. authentication) to finish.
        // This is a retry for a modal grab, never an expiry timer.
        if (Main.modalCount > (this._dialog._hasModal ? 1 : 0) || Main.sessionMode.isLocked) {
            if (!this._retry) {
                this._retry = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                    this._retry = 0;
                    this._render();
                    return GLib.SOURCE_REMOVE;
                });
            }
            return;
        }
        const severe = item.severity === 'severe';
        if (severe)
            this._dialog.dialogLayout.add_style_class_name('attention-gate-severe');
        else
            this._dialog.dialogLayout.remove_style_class_name('attention-gate-severe');
        this._dialog.contentLayout.destroy_all_children();
        const card = this._dialog.contentLayout;
        const header = new St.BoxLayout({style_class: 'attention-gate-header'});
        header.add_child(new St.Icon({icon_name: 'dialog-warning-symbolic', icon_size: 24}));
        header.add_child(this._label(severe ? 'DROP EVERYTHING · SEVERE' : 'NEEDS YOUR ATTENTION', 'attention-gate-tag'));
        card.add_child(header);
        card.add_child(this._label(item.app.slice(0, 200), 'attention-gate-source'));
        card.add_child(this._label(item.title.slice(0, 180), 'attention-gate-title'));
        const body = item.body.split('\n').slice(0, 6).join('\n').slice(0, 650);
        card.add_child(this._label(body, 'attention-gate-body'));
        card.add_child(this._label(this._queue.size > 1 ? `${this._queue.size} items need attention · stays until dismissed` : 'Stays until you dismiss it', 'attention-gate-footnote'));
        this._dialog.clearButtons();
        const dismiss = this._dialog.addButton({label: 'Dismiss', default: true, key: Clutter.KEY_Escape, action: () => {
            this._dismissed.add(item.id);
            this._queue.delete(item.id);
            this._export.emit_signal('Dismissed', new GLib.Variant('(t)', [item.id]));
            this._render();
        }});
        dismiss.add_style_class_name('attention-gate-dismiss');
        this._dialog.setInitialKeyFocus(dismiss);
        this._dialog.open();
        dismiss.grab_key_focus();
    }

    disable() {
        if (this._retry)
            GLib.source_remove(this._retry);
        this._retry = 0;
        this._dialog?.close();
        this._dialog?.destroy();
        this._dialog = null;
        if (this._signal)
            Gio.DBus.session.signal_unsubscribe(this._signal);
        if (this._name)
            Gio.bus_unown_name(this._name);
        this._export?.unexport();
        this._injections?.clear();
        this._queue?.clear();
        this._dismissed?.clear();
    }
}
