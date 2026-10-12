"""Track only settings we change, and preserve later edits during restoration."""
import json
import subprocess
from .paths import STATE_DIR, private_write

class Settings:
    def __init__(self, path=None):
        self.path = path or STATE_DIR / 'settings.json'
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else []

    @staticmethod
    def get(schema, key):
        return subprocess.check_output(['gsettings', 'get', schema, key], text=True).strip()

    def set(self, schema, key, value, scope):
        previous = self.get(schema, key)
        entry = next((e for e in self.entries if e['schema'] == schema and e['key'] == key and e['scope'] == scope), None)
        if entry is None:
            entry = dict(schema=schema, key=key, before=previous, after=value, scope=scope)
            self.entries.append(entry)
        # Write the undo record before mutation, including interrupted installs.
        private_write(self.path, json.dumps(self.entries, indent=2))
        subprocess.run(['gsettings', 'set', schema, key, value], check=True)
        entry['after'] = self.get(schema, key)
        private_write(self.path, json.dumps(self.entries, indent=2))

    def restore(self, scope):
        remaining = []
        for entry in reversed(self.entries):
            if entry['scope'] != scope:
                remaining.append(entry)
                continue
            current = self.get(entry['schema'], entry['key'])
            if current == entry['after']:
                subprocess.run(['gsettings', 'set', entry['schema'], entry['key'], entry['before']], check=True)
            else:
                print('Kept a later user edit:', entry['schema'], entry['key'])
        self.entries = list(reversed(remaining))
        private_write(self.path, json.dumps(self.entries, indent=2))
