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
    commands.add_parser('exchange', help=argparse.SUPPRESS)
    sync = commands.add_parser('sync', help='Share deduplicated events and dismissals over SSH')
    sync_commands = sync.add_subparsers(dest='action', required=True)
    pair = sync_commands.add_parser('pair')
    pair.add_argument('host', help='An existing, trusted SSH host or alias')
    pair.add_argument('--label', default='Laptop', help='Label for this device')
    pair.add_argument('--authority', action='store_true', help='Explicitly pin this server as the authority, replacing any previous pin')
    satellite = sync_commands.add_parser('satellite')
    satellite.add_argument('--label', default='Workstation')
    authority = sync_commands.add_parser('authority')
    authority.add_argument('--label', default='Server')
    name = sync_commands.add_parser('label')
    name.add_argument('name')
    sync_commands.add_parser('run')
    sync_commands.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command == 'exchange':
            from .sync import exchange
            exchange()
        elif args.command == 'sync':
            from . import sync
            if args.action == 'pair':
                sync.pair(args.host, args.label, args.authority)
            elif args.action == 'satellite':
                from .install import satellite
                satellite(args.label)
            elif args.action == 'authority':
                from .install import authority
                authority(args.label)
            elif args.action == 'label':
                sync.label(args.name)
            else:
                {'run': sync.run, 'status': sync.status}[args.action]()
        elif args.command == 'view':
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
            fields = {'enabled','server_mode','present_attention','model','threshold','category_thresholds','timeout_seconds','max_delay_seconds','cooldown_seconds','excluded_apps','instructions','criteria'}
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
