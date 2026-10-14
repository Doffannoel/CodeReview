import hashlib
import hmac

from codereviewer.security import verify_github_signature


def test_verify_github_signature() -> None:
    body = b'{"action":"opened"}'
    secret = "test-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_github_signature(body, signature, secret)
    assert not verify_github_signature(body + b"x", signature, secret)
    assert not verify_github_signature(body, None, secret)
