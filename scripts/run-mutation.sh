#!/usr/bin/env bash
set -euo pipefail

scope="${1:-all}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$scope" in
    pause|resume)
        exec "$script_dir/../server/.venv/bin/python" "$script_dir/mutation-session.py" "$scope" ;;
    help|-h|--help)
        cat <<'HELP'
Usage: mutation [all|backend|francesco|davide|khaled|common|pause|resume|help]

Fresh campaigns (default: all):
  all                         All configured source modules
  backend                     Flask backend and login rate limiter
  francesco|davide|khaled      Selected watermarking package
  common                      Group13, Toy EOF, registry, contract, utilities and CLI

Session controls:
  pause                       Stop the active campaign safely, preserving saved results
  resume                      Resume the last recorded campaign with its original filters
  help, -h, --help            Show this help without changing session data

Fresh campaigns require confirmation before replacing server/mutants/.
Resume does nothing if already running, complete, or no session can be recovered.
Pause does nothing if no campaign is running. Use commands one at a time.
Run ./mutation from the repository root or ../mutation from server/.
Python 3.12, project dependencies and mutmut must be installed in server/.venv.
HELP
        exit 0 ;;
    all) set -- ;;
    backend) set -- 'server.*' 'login_rate_limit.*' ;;
    francesco|davide|khaled) set -- "watermarking_methods.$scope.*" ;;
    common) set -- 'watermarking_methods.group13.*' 'watermarking_methods.add_after_eof.*' \
        'watermarking_methods.x*' 'watermarking_method.*' 'watermarking_utils.*' 'watermarking_cli.*' ;;
    *) echo "Unknown command: $scope. Run mutation help for usage." >&2; exit 2 ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../server"
.venv/bin/python "$script_dir/mutation-session.py" check
read -r -p "This will overwrite previous outputs in server/mutants/ and start the '$scope' campaign. Continue? [y/N] " answer
case "$answer" in
    y|Y) ;;
    *) echo "Cancelled."; exit 0 ;;
esac
rm -rf -- mutants
.venv/bin/python "$script_dir/mutation-session.py" record "$@"
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
exec .venv/bin/mutmut run "$@"
