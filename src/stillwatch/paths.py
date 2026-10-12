"""XDG locations; no host-specific paths or credentials are bundled."""
import os
from pathlib import Path

def xdg_directory(name, fallback):
    value = os.environ.get(name)
    return Path(value) if value and Path(value).is_absolute() else Path(fallback)

CONFIG_HOME = xdg_directory('XDG_CONFIG_HOME', Path.home() / '.config')
DATA_HOME = xdg_directory('XDG_DATA_HOME', Path.home() / '.local/share')
CONFIG_DIR = CONFIG_HOME / 'stillwatch'
STATE_DIR = xdg_directory('XDG_STATE_HOME', Path.home() / '.local/state') / 'stillwatch'
DATA_DIR = DATA_HOME / 'stillwatch'
GATE_UUID = 'stillwatch@difflabai'

def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path

def private_write(path, text):
    private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(text)
    path.chmod(0o600)
