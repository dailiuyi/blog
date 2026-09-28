import argparse
import json
import sys
import time

from .bridge import Bridge
from .controller import Controller
from .core import StateStore, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='Trusted Symphony acceptance controller')
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    register = sub.add_parser('register')
    register.add_argument('--issue', required=True, type=int)
    register.add_argument('--deepseek-consent', action='store_true',
                          help='Record explicit consent for THIS issue and plan to api.deepseek.com')
    register.add_argument('--update-plan', action='store_true')
    enqueue = sub.add_parser('enqueue')
    enqueue.add_argument('--issue', required=True, type=int)
    run = sub.add_parser('run')
    run.add_argument('--issue', required=True, type=int)
    run.add_argument('--workspace', required=True)
    status = sub.add_parser('status')
    status.add_argument('--issue', required=True, type=int)
    sub.add_parser('bridge')
    watch = sub.add_parser('watch')
    watch.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.command == 'bridge':
        return Bridge(config).run()
    if args.command == 'status':
        result = StateStore(config['control_root'], config['repository'], args.issue).load()
    else:
        controller = Controller(config, progress=lambda message: print(message, file=sys.stderr, flush=True))
        if args.command == 'register':
            result = controller.register(args.issue, deepseek_consent=args.deepseek_consent, update_plan=args.update_plan)
        elif args.command == 'enqueue':
            result = controller.enqueue(args.issue)
        elif args.command == 'run':
            result = controller.run(args.issue, args.workspace)
        elif args.command == 'watch':
            while True:
                result = controller.watch_once()
                if result:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                if args.once:
                    return 0
                time.sleep(config.get('poll_seconds', 30))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if isinstance(result, dict) and result.get('phase') == 'blocked' else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        sys.exit(1)
