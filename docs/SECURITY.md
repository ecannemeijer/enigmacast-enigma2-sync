# Security

- The Sync API is TLS only. The box generates a local certificate with `openssl`. EnigmaCast pins the certificate's public key after the install wizard has read it over SSH. There is no HTTP fallback for profiles or playlist secrets.
- The Web Manager reuses that certificate on port 8766. If the certificate is not there yet, the dashboard serves HTTP. The browser login is separate from device pairing.
- The first dashboard password is `admin`, stored as a PBKDF2 hash in `web-auth.json` (mode 0600 where the filesystem allows it). A fuse/NTFS disk may ignore ownership; the processes still run as the `enigmacast` user. The plaintext exists only in `web-password-once` until the wizard reads and deletes it. Change it under Serverbeheer. An existing hash is left as it is.
- Browser sessions are HMAC-signed cookies (`HttpOnly`, `SameSite=Strict`, `Secure` on HTTPS). Management actions need the CSRF token from that session.
- Dashboard requests from public addresses are refused. Loopback, link-local, RFC1918 and IPv6 ULA addresses are accepted, which covers the home LAN and a private VPN.
- Device identity is an ECDSA P-256 key. The server stores the public key. Each API request is signed and carries a nonce, so a captured request cannot be replayed after the skew window.
- The device token is stored as a SHA-256 hash.
- Pairing codes and the recovery code are stored as hashes and expire (pairing codes last 10 minutes).
- Exactly one non-revoked admin device. Recovery revokes all devices, including the old admin, then allows a single new pairing to become admin. The dashboard does not change that rule.
- Playlist passwords are AES-256-GCM under `master.key` (mode 0600). Backups include that key on disk so a restore can still open them. The dashboard shows the backup directory, not the key.
- Start, stop and restart call `/etc/init.d/enigmacast-sync` with one of `start`, `stop` or `restart`. The argument is not taken from a shell string.
- Logs and the log viewer drop lines that contain passwords, tokens, pairing codes or authorization headers.
