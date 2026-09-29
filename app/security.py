import hashlib
import hmac


def valid_signature(body, header, secret):
    # github sends "sha256=" + the hex HMAC of the raw body, keyed with the webhook secret
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # constant-time compare, so the signature can't be guessed one character at a time
    return hmac.compare_digest(expected, header)
