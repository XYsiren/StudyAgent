"""Prepare a private Prometheus bearer token; never print credentials."""
import argparse
import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env.docker'))
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error('Create the deployment env file first')
    settings = dotenv_values(args.env_file)
    token = settings.get('RAG_METRICS_TOKEN') or secrets.token_urlsafe(40)
    set_key(args.env_file, 'RAG_METRICS_TOKEN', token)
    target = args.env_file.resolve().parent / '.secrets/metrics_token'
    target.parent.mkdir(mode=0o700, exist_ok=True)
    target.write_text(token, encoding='utf-8')
    target.chmod(0o600)
    print('Configured private metrics credentials. Restart the API to apply the deployment env file.')


if __name__ == '__main__':
    main()
