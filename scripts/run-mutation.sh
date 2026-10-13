#!/usr/bin/env bash
set -euo pipefail

scope="${1:-all}"
case "$scope" in
    -h|--help)
        echo "Usage: $0 [all|backend|francesco|davide|khaled|common]"
        echo "Default: all. Confirmation is required before replacing server/mutants/."
        exit 0 ;;
    all) set -- ;;
    backend) set -- 'server.*' 'login_rate_limit.*' ;;
    francesco|davide|khaled) set -- "watermarking_methods.$scope.*" ;;
    common) set -- 'watermarking_methods.group13.*' 'watermarking_methods.add_after_eof.*' \
        'watermarking_methods.x*' 'watermarking_method.*' 'watermarking_utils.*' 'watermarking_cli.*' ;;
    *) echo "Usage: $0 [all|backend|francesco|davide|khaled|common]" >&2; exit 2 ;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../server"
read -r -p "This will overwrite previous outputs in server/mutants/ and start the '$scope' campaign. Continue? [y/N] " answer
case "$answer" in
    y|Y) ;;
    *) echo "Cancelled."; exit 0 ;;
esac
rm -rf -- mutants
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
exec .venv/bin/mutmut run "$@"
