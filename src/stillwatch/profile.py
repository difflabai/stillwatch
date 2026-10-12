"""An optional compact GNOME profile, independent of notification triage."""
from pathlib import Path
import re
import subprocess
from .paths import STATE_DIR, private_write
from .settings import Settings

def apply(terminal=False, quiet_crash_reports=False):
    schemas = set(subprocess.check_output(['gsettings', 'list-schemas'], text=True).splitlines())
    settings = Settings()
    changes = {
        'org.gnome.desktop.interface': {
            'font-name': "'DejaVu Sans 10'", 'document-font-name': "'DejaVu Sans 10'",
            'monospace-font-name': "'DejaVu Sans Mono 10'", 'color-scheme': "'prefer-dark'",
            'accent-color': "'blue'", 'clock-show-weekday': 'true', 'show-battery-percentage': 'true',
        },
        'org.gnome.desktop.wm.preferences': {
            'titlebar-font': "'DejaVu Sans Bold 10'", 'button-layout': "'appmenu:minimize,maximize,close'",
            'resize-with-right-button': 'true',
        },
        'org.gnome.desktop.wm.keybindings': {
            'switch-applications': "['<Super>Tab']", 'switch-applications-backward': "['<Shift><Super>Tab']",
            'switch-windows': "['<Alt>Tab']", 'switch-windows-backward': "['<Shift><Alt>Tab']",
        },
        'org.gnome.shell.extensions.dash-to-dock': {
            'dock-position': "'LEFT'", 'dash-max-icon-size': '36', 'extend-height': 'false',
            'show-trash': 'false', 'show-mounts': 'false', 'click-action': "'focus-or-previews'",
            'scroll-action': "'cycle-windows'", 'show-windows-preview': 'true',
            'running-indicator-style': "'DASHES'", 'require-pressure-to-show': 'false',
            'show-delay': '0.1', 'hide-delay': '0.5',
            'show-icons-notifications-counter': 'false', 'application-counter-overrides-notifications': 'true',
        },
        'org.gnome.shell.extensions.tiling-assistant': {'window-gap': '8', 'single-screen-gap': '8'},
    }
    if Path('/usr/share/themes/Yaru-blue-dark').is_dir():
        changes['org.gnome.desktop.interface']['gtk-theme'] = "'Yaru-blue-dark'"
    if Path('/usr/share/icons/Yaru-blue-dark').is_dir():
        changes['org.gnome.desktop.interface']['icon-theme'] = "'Yaru-blue-dark'"
    if quiet_crash_reports:
        changes['com.ubuntu.update-notifier'] = {'show-apport-crashes': 'false'}
    for schema, keys in changes.items():
        if schema not in schemas:
            continue
        available = set(subprocess.check_output(['gsettings', 'list-keys', schema], text=True).splitlines())
        for key, value in keys.items():
            if key in available:
                settings.set(schema, key, value, 'profile')
    if terminal:
        kitty = Path.home() / '.config/kitty/kitty.conf'
        if kitty.exists():
            backup = STATE_DIR / 'kitty-original.conf'
            if not backup.exists():
                private_write(backup, kitty.read_text())
            text = re.sub(r'^font_size\s+.*$', 'font_size 10.5', kitty.read_text(), flags=re.M)
            if not re.search(r'^font_size\s+', text, flags=re.M):
                text += '\nfont_size 10.5\n'
            text = re.sub(r'^modify_font cell_height\s+.*$', 'modify_font cell_height 100%', text, flags=re.M)
            kitty.write_text(text)
            print('Kitty configuration updated; reload Kitty to apply it.')
    print('Compact profile applied. Vertical screen space is preserved by the left dock.')

def restore():
    Settings().restore('profile')
    backup = STATE_DIR / 'kitty-original.conf'
    if backup.exists():
        target = Path.home() / '.config/kitty/kitty.conf'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            current = target.read_text()
            original = backup.read_text()
            for expression, applied in [(r'^font_size\s+.*$', '10.5'), (r'^modify_font cell_height\s+.*$', '100%')]:
                previous = re.search(expression, original, flags=re.M)
                now = re.search(expression, current, flags=re.M)
                if now and now.group(0).split()[-1] == applied:
                    current = re.sub(expression, previous.group(0) if previous else '', current, flags=re.M)
            target.write_text(current)
        backup.unlink()
    print('Previous desktop profile restored.')
