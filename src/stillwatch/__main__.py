"""Stillwatch command line; GUI/system dependencies load only when needed."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
from . import __version__

def main():
    parser = argparse.ArgumentParser(prog='stillwatch', description='A quiet GNOME inbox with AI attention triage.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    installer = commands.add_parser('install', help='Install for this user; local history by default')
    installer.add_argument('--dry-run', action='store_true')
    installer.add_argument('--enable-ai', action='store_true', help='Opt into sending new notification text to NanoGPT')
    for command in ('uninstall', 'doctor', 'enable-ai', 'disable-ai', 'key', 'record', 'attention'):
        commands.add_parser(command)
    view = commands.add_parser('view', help='Open notification history')
    view.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    view.add_argument('--capture', action='store_true', help=argparse.SUPPRESS)
    demo = commands.add_parser('demo', help='Show a fictional alert without calling a model')
    demo.add_argument('--severe', action='store_true', help='Show a red severe example')
    demo.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    demo.add_argument('--capture', action='store_true', help=argparse.SUPPRESS)
    profile = commands.add_parser('profile', help='Optional compact desktop profile')
    profile.add_argument('action', choices=('apply', 'restore'))
    profile.add_argument('--terminal', action='store_true', help='Also adjust an existing Kitty configuration')
    profile.add_argument('--quiet-crash-reports', action='store_true', help='Also disable recurring Ubuntu crash-report popups')
    commands.add_parser('config', help='Print policy, never credentials')
    args = parser.parse_args()
    try:
        if args.command == 'view':
            from .history import viewer
            viewer(args.smoke_test)
        elif args.command == 'record':
            from .history import recorder
            recorder()
        elif args.command in ('attention', 'demo'):
            from .popup import run
            run(demo=args.command == 'demo', severe=getattr(args, 'severe', False), smoke=getattr(args, 'smoke_test', False), capture=getattr(args, 'capture', False))
        elif args.command == 'profile':
            from . import profile
            profile.apply(args.terminal, args.quiet_crash_reports) if args.action == 'apply' else profile.restore()
        elif args.command == 'config':
            from .decision import config
            fields = {'enabled','model','threshold','timeout_seconds','max_delay_seconds','cooldown_seconds','excluded_apps','instructions','criteria'}
            print(json.dumps({key:value for key,value in config().items() if key in fields}, indent=2))
        else:
            from . import install
            if args.command == 'install':
                install.install(args.dry_run, args.enable_ai)
            elif args.command == 'doctor':
                return 0 if all(install.doctor().values()) else 1
            else:
                {'uninstall': install.uninstall, 'enable-ai': install.activate_ai, 'disable-ai': install.disable_ai, 'key': install.save_key}[args.command]()
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        # Credentials and notification content never go into diagnostic output.
        print(f'Stillwatch: {type(error).__name__}. Check dependencies, configuration, or the service journal.', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
