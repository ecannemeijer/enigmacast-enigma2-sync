# API v1

The Sync API is unchanged. The web dashboard on port 8766 is a separate process and does not add routes here.

All paths are under `/api/v1`. Except `health`, `server`, `pair` and `recover`, every call needs:

- `Authorization: Bearer <token>`
- `X-Timestamp` unix milliseconds
- `X-Nonce` unique for five minutes
- `X-Signature` base64 DER ECDSA-SHA256 over `timestamp + "\n" + nonce + "\n" + METHOD + "\n" + path + "\n" + sha256(body)`

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| GET | `/health` | anyone | Process is up |
| GET | `/server` | anyone | Version marker and whether an admin exists |
| POST | `/pair` | pairing code | First unused code becomes the only admin; later codes become members |
| POST | `/recover` | recovery code | Revokes every device and returns one new pairing code |
| POST | `/pairing` | admin | New member pairing code |
| GET | `/devices` | admin | Device list without keys |
| POST | `/devices/revoke` | admin | Revoke another device |
| POST | `/devices/profiles` | admin | Assign profile uuids to a member |
| POST | `/profiles` | admin or assigned | Create or update a profile |
| POST | `/playlists` | admin | Store a playlist; the password is sealed |
| POST | `/sync/push` | admin or assigned | Apply changes that are newer |
| GET | `/sync/pull?profileId&cursor` | admin or assigned | Changes after the cursor |
| POST | `/backups` | admin | Snapshot the database and the key file |
| GET | `/status` | admin | Same facts as `/server`, admin only |

A push body is `{"changes":[{"profileId","entity","key","op","payload","updatedMs","changeId"}]}`. `op` is `upsert` or `delete`. The same `changeId` is a replay. An older `updatedMs` is rejected.
