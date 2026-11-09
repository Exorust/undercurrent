"""Print a signed undercurrent invite. Environment overrides the repository .env."""
import base64
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys
import unicodedata


def secret_from_env():
    if 'UC_TOKEN_SECRET' in os.environ:
        return os.environ['UC_TOKEN_SECRET']
    path = Path(__file__).resolve().parents[1] / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r'^\s*(?:export\s+)?UC_TOKEN_SECRET\s*=\s*(.*?)\s*$', line)
            if match:
                value = match[1]
                if value[:1] in ('"', "'") and value[-1:] == value[:1]:
                    return value[1:-1]
                return value.split(' #', 1)[0].strip()
    return ''


def main():
    if len(sys.argv) != 2:
        sys.exit('Usage: python tools/invite.py "Jane Doe"')
    slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', sys.argv[1]).encode('ascii', 'ignore').decode().lower()).strip('-')
    if not slug or len(slug) > 200:
        sys.exit('Use a name that produces a slug of 1 to 200 characters.')
    secret = secret_from_env()
    if not secret:
        sys.exit('UC_TOKEN_SECRET is required.')
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip('=')
    payload = 'v1.i.' + b64(slug.encode())
    token = payload + '.' + b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    print('https://charoori.com/undercurrent/map/?invite=' + token)


if __name__ == '__main__':
    main()
