"""Per-user installation with no root access and no implicit cloud opt-in."""
import ast
import base64
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .paths import CONFIG_HOME, DATA_HOME, CONFIG_DIR, DATA_DIR, STATE_DIR, GATE_UUID, private_write
from .decision import INSTRUCTIONS, CRITERIA, classify, config
from .settings import Settings

RESOURCES = Path(__file__).parent / 'resources'
MANIFEST = STATE_DIR / 'installation.json'
APPLICATION_ID = 'io.github.difflabai.Stillwatch'

def session_environment():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    if (runtime / 'bus').exists():
        os.environ.setdefault('XDG_RUNTIME_DIR', str(runtime))
        os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')

def locations():
    return {
        'launcher': Path.home() / '.local/bin/stillwatch',
        'service': CONFIG_HOME / 'systemd/user/stillwatch.service',
        'sync_service': CONFIG_HOME / 'systemd/user/stillwatch-sync.service',
        'server_service': CONFIG_HOME / 'systemd/user/stillwatch-server.service',
        'desktop': DATA_HOME / f'applications/{APPLICATION_ID}.desktop',
        'icon': DATA_HOME / f'icons/hicolor/scalable/apps/{APPLICATION_ID}.svg',
        'extension': DATA_HOME / 'gnome-shell/extensions' / GATE_UUID,
        'application': DATA_DIR / 'app',
    }

def doctor():
    session_environment()
    results = {}
    for module in ('gi', 'dbus'):
        results[module] = importlib.util.find_spec(module) is not None
    if results['gi']:
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('Adw', '1')
            results['gtk4_adwaita'] = True
        except ValueError:
            results['gtk4_adwaita'] = False
    else:
        results['gtk4_adwaita'] = False
    try:
        result = subprocess.check_output(['gnome-shell', '--version'], text=True).strip()
        results['gnome_50'] = result.split()[-1].split('.')[0] == '50'
        print(result)
    except (OSError, subprocess.CalledProcessError):
        results['gnome_50'] = False
    results['session_bus'] = bool(os.environ.get('DBUS_SESSION_BUS_ADDRESS'))
    for name, present in results.items():
        print(f'{name}: {"available" if present else "missing"}')
    return results

def default_config():
    return {
        'enabled': False, 'model': 'typesafe/jev-1.13', 'threshold': .85,
        'category_thresholds': {'agent': .65},
        'timeout_seconds': 5, 'max_delay_seconds': 10, 'cooldown_seconds': 300,
        'key_file': str(CONFIG_DIR / 'nanogpt.env'), 'excluded_apps': [],
        'instructions': INSTRUCTIONS, 'criteria': CRITERIA,
    }

def save_key():
    value = os.environ.get('NANOGPT_API_KEY') or getpass.getpass('NanoGPT API key (hidden): ')
    value = value.strip()
    if not value or '\n' in value or '\r' in value:
        raise ValueError('Provide a nonempty, single-line API key.')
    private_write(CONFIG_DIR / 'nanogpt.env', 'NANOGPT_API_KEY=' + value + '\n')
    print('Saved the service key privately. It is not displayed or stored in the repository.')

def _systemd_quote(text):
    return '"' + str(text).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'

def _desktop_quote(text):
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`') + '"'

def install(dry_run=False, enable_ai=False):
    if os.getuid() == 0:
        raise RuntimeError('Run Stillwatch as your desktop user, without sudo.')
    session_environment()
    paths = locations()
    if dry_run:
        print('Per-user installation plan; no settings, services or credentials changed:')
        for name, path in paths.items():
            print(f'{name}: {path}')
        print('Cloud classification:', 'explicitly requested' if enable_ai else 'off')
        return
    checks = doctor()
    if not all(checks[k] for k in ('gi', 'dbus', 'gtk4_adwaita', 'session_bus')):
        raise RuntimeError('Install the GTK/Python system dependencies and run from your desktop session.')
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {'files': {}}
    def write(path, data):
        path = Path(path)
        if path.is_symlink():
            raise RuntimeError(f'Refusing to replace a symlink: {path}')
        entry = manifest['files'].setdefault(str(path), {
            'before': base64.b64encode(path.read_bytes()).decode() if path.exists() else None,
            'mode': path.stat().st_mode & 0o777 if path.exists() else None,
        })
        entry['after'] = base64.b64encode(data).decode()
        private_write(MANIFEST, json.dumps(manifest, indent=2))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # Copy only Python/package resources, never an enclosing checkout or user data.
    source = Path(__file__).parent
    for file in source.rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts and file.suffix in ('.py', '.js', '.css', '.json', '.svg'):
            write(paths['application'] / 'stillwatch' / file.relative_to(source), file.read_bytes())
    launcher = ('#!/bin/sh\nexport PYTHONPATH=' + shlex.quote(str(paths['application'])) + '\nexec ' + shlex.quote(sys.executable) + ' -m stillwatch "$@"\n')
    write(paths['launcher'], launcher.encode())
    paths['launcher'].chmod(0o755)
    service = f'''[Unit]
Description=Stillwatch notification inbox
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart={_systemd_quote(sys.executable)} -m stillwatch record
Environment={_systemd_quote('PYTHONPATH=' + str(paths['application']))}
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=graphical-session.target
'''
    write(paths['service'], service.encode())
    sync_service = service.replace('Stillwatch notification inbox', 'Stillwatch shared notification state').replace('-m stillwatch record', '-m stillwatch sync run')
    write(paths['sync_service'], sync_service.encode())
    server_service = service.replace('Stillwatch notification inbox', 'Stillwatch authoritative attention server').replace('After=graphical-session.target', 'After=dbus.socket\nWants=dbus.socket').replace('PartOf=graphical-session.target\n', '').replace('WantedBy=graphical-session.target', 'WantedBy=default.target')
    write(paths['server_service'], server_service.encode())
    desktop = f'''[Desktop Entry]
Type=Application
Name=Stillwatch
Comment=Search notifications and handle important events
Exec={_desktop_quote(paths['launcher'])} view
Icon={APPLICATION_ID}
Categories=Utility;
StartupNotify=true
'''
    write(paths['desktop'], desktop.encode())
    write(paths['icon'], (RESOURCES / 'icon.svg').read_bytes())
    for file in (RESOURCES / 'extension').iterdir():
        write(paths['extension'] / file.name, file.read_bytes())
    if not (CONFIG_DIR / 'config.json').exists():
        private_write(CONFIG_DIR / 'config.json', json.dumps(default_config(), indent=2))
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    start_recorder(config())
    if enable_ai:
        activate_ai()
    print('Installed Stillwatch. Open it from your app launcher or run stillwatch view.')
    if not enable_ai:
        current = config()
        if current.get('enabled'):
            print('Kept your existing AI triage configuration.')
        elif current.get('present_attention'):
            print('Kept your shared-state satellite configuration; no local cloud calls.')
        else:
            print('History is local. Run stillwatch enable-ai to opt into NanoGPT classification.')

def _monitor_available():
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus(private=True)
    try:
        bus.call_blocking('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.Monitoring', 'BecomeMonitor', 'asu', (["type='method_call',interface='org.freedesktop.Notifications',member='Notify'"], 0))
    finally:
        bus.close()

def start_recorder(policy):
    selected = 'stillwatch-server.service' if policy.get('server_mode') else 'stillwatch.service'
    other = 'stillwatch.service' if policy.get('server_mode') else 'stillwatch-server.service'
    subprocess.run(['systemctl', '--user', 'disable', '--now', other], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'enable', '--now', selected], check=True)
    subprocess.run(['systemctl', '--user', 'restart', selected], check=True)
    return selected

def activate_ai():
    session_environment()
    checks = doctor()
    if not all(checks.values()):
        raise RuntimeError('The system-modal extension currently requires GNOME Shell 50 and a desktop session.')
    if not MANIFEST.exists():
        raise RuntimeError('Install Stillwatch first.')
    from .history import connect
    from . import mesh
    with connect() as db:
        if not mesh.can_classify(db):
            raise RuntimeError('This device is a replica; enable AI on the authoritative server.')
    if not os.environ.get('NANOGPT_API_KEY') and not (CONFIG_DIR / 'nanogpt.env').exists():
        save_key()
    elif os.environ.get('NANOGPT_API_KEY'):
        save_key()
    policy = {**default_config(), **config()}
    _monitor_available()
    # Verify the selected key/model before turning off existing banners.
    classify('Stillwatch', 'Setup check', 'A synthetic setup check. No user action is needed.', policy)
    with connect() as db:
        mesh.set_authority(db, mesh.identity(db)[0])
    policy['enabled'] = True
    private_write(CONFIG_DIR / 'config.json', json.dumps(policy, indent=2))
    selected = start_recorder(policy)
    if subprocess.run(['systemctl', '--user', 'is-active', '--quiet', selected]).returncode:
        raise RuntimeError('The recorder did not start; existing notification settings were left intact.')
    enable_presentation()
    print('AI triage enabled. Log out and back in to load the full GNOME gate and system modals.')

def enable_presentation():
    settings = Settings()
    enabled = ast.literal_eval(settings.get('org.gnome.shell', 'enabled-extensions').removeprefix('@as '))
    if GATE_UUID not in enabled:
        enabled.append(GATE_UUID)
    settings.set('org.gnome.shell', 'enabled-extensions', repr(enabled), 'ai')
    settings.set('org.gnome.shell', 'disable-user-extensions', 'false', 'ai')
    settings.set('org.gnome.desktop.notifications', 'show-banners', 'false', 'ai')
    # Add a single shortcut without changing other custom shortcuts.
    path = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/stillwatch/'
    schema = 'org.gnome.settings-daemon.plugins.media-keys'
    specific = schema + '.custom-keybinding:' + path
    settings.set(specific, 'name', "'Stillwatch'", 'shortcut')
    settings.set(specific, 'command', repr(shlex.quote(str(locations()['launcher'])) + ' view'), 'shortcut')
    settings.set(specific, 'binding', "'<Super><Shift>n'", 'shortcut')
    paths = ast.literal_eval(settings.get(schema, 'custom-keybindings').removeprefix('@as '))
    if path not in paths:
        paths.append(path)
    settings.set(schema, 'custom-keybindings', repr(paths), 'shortcut')

def satellite(device_label):
    session_environment()
    if not MANIFEST.exists() or not all(doctor().values()):
        raise RuntimeError('Install Stillwatch in a GNOME 50 session first.')
    _monitor_available()
    from .sync import label
    label(device_label)
    policy = {**default_config(), **config(), 'enabled': False, 'server_mode': False, 'present_attention': True}
    private_write(CONFIG_DIR / 'config.json', json.dumps(policy, indent=2))
    start_recorder(policy)
    if subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'stillwatch.service']).returncode:
        raise RuntimeError('The recorder did not start; notification settings were left intact.')
    enable_presentation()
    print('Shared-state satellite ready. It makes no cloud calls; the paired classifier supplies decisions.')

def authority(device_label):
    from .history import connect
    from . import mesh
    from .sync import label
    label(device_label)
    with connect() as db:
        mesh.set_authority(db, mesh.identity(db)[0])
    policy = {**default_config(), **config(), 'server_mode': True}
    private_write(CONFIG_DIR / 'config.json', json.dumps(policy, indent=2))
    activate_ai()
    print('This server is the authoritative classifier; paired devices hold synced copies.')

def disable_ai():
    session_environment()
    try:
        saved = config()
    except (OSError, ValueError):
        saved = {}
    policy = {**default_config(), **saved, 'enabled': False, 'present_attention': False}
    private_write(CONFIG_DIR / 'config.json', json.dumps(policy, indent=2))
    settings = Settings()
    settings.restore('ai')
    # Always remove our gate, preserving extensions installed by the user later.
    enabled = ast.literal_eval(settings.get('org.gnome.shell', 'enabled-extensions').removeprefix('@as '))
    if GATE_UUID in enabled:
        subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions', repr([item for item in enabled if item != GATE_UUID])], check=True)
    selected = 'stillwatch-server.service' if policy.get('server_mode') else 'stillwatch.service'
    subprocess.run(['systemctl', '--user', 'try-restart', selected], check=True)
    print('Cloud classification disabled; original banner settings restored where unchanged.')

def uninstall():
    session_environment()
    if not MANIFEST.exists():
        raise RuntimeError('No Stillwatch installation manifest was found.')
    disable_ai()
    subprocess.run(['systemctl', '--user', 'disable', '--now', 'stillwatch-sync.service'], check=False)
    subprocess.run(['systemctl', '--user', 'disable', '--now', 'stillwatch.service'], check=False)
    subprocess.run(['systemctl', '--user', 'disable', '--now', 'stillwatch-server.service'], check=False)
    Settings().restore('shortcut')
    manifest = json.loads(MANIFEST.read_text())
    for filename, entry in manifest['files'].items():
        path = Path(filename)
        if path.is_symlink():
            print('Kept changed symlink:', path)
            continue
        if not path.exists():
            continue
        if path.read_bytes() != base64.b64decode(entry['after']):
            print('Kept a later user edit:', path)
            continue
        if entry['before'] is None:
            path.unlink()
        else:
            path.write_bytes(base64.b64decode(entry['before']))
            path.chmod(entry['mode'])
    MANIFEST.unlink()
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Uninstalled Stillwatch. Private history and configuration are retained.')
