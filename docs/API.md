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
| GET | `/playlists` | any paired device | The one IPTV login (M3U or Xtream), not tied to a profile |
| POST | `/playlists` | any paired device | Store that login when it is newer; the password is sealed |
| POST | `/sync/push` | admin or assigned | Apply changes that are newer |
| GET | `/sync/pull?profileId&cursor&limit` | admin or assigned | Up to `limit` changes after the cursor; repeat with the returned cursor |
| POST | `/backups` | admin | Snapshot the database and the key file |
| GET | `/status` | admin | Same facts as `/server`, admin only |

A push body is `{"changes":[{"profileId","entity","key","op","payload","updatedMs","changeId"}]}`. `op` is `upsert` or `delete`. The same `changeId` is a replay. An older `updatedMs` is rejected.

A pull response is `{"cursor":<next>,"changes":[...],"limit":<page>}`. Call it again with the returned cursor while a full page was returned, so a client never asks for the whole history at once. The server records the **requested** cursor per device and prunes only changes that all active devices already applied, keeping a recent tail. Devices unseen for 90 days no longer block pruning. A new or very old device needs a full resync; the current API has no snapshot endpoint yet.
