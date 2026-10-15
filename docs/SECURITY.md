# Security

- TLS only. The box generates a local certificate with `openssl`. EnigmaCast pins the certificate's public key after the install wizard has read it over SSH. There is no HTTP fallback for profiles or playlist secrets.
- Device identity is an ECDSA P-256 key. The server stores the public key. Each request is signed and carries a nonce, so a captured request cannot be replayed after the skew window.
- The device token is stored as a SHA-256 hash.
- Pairing codes and the recovery code are stored as hashes and expire (pairing codes last 10 minutes).
- Exactly one non-revoked admin. Recovery revokes all devices, including the old admin, then allows a single new pairing to become admin.
- Playlist passwords are AES-256-GCM under `master.key` (mode 0600). Backups include that key so a restore can still open them. Logs must not contain tokens, passwords, pairing codes or payloads.
- A member device can only read and write profiles an admin assigned.
