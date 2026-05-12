# Hue Firelight

Realistic fire/flame effect for Philips Hue color bulbs on Linux. Two implementations:

| Script | Transport | Refresh | Smoothness | Setup cost |
|---|---|---|---|---|
| `hue-flame.py` | HTTPS REST (V1 API) | ~8 Hz/light (bridge rate-limit) | Visible step-fade between targets | Minimal — just an app key |
| `hue-flame-stream.py` | DTLS-PSK over UDP (Entertainment API v2) | 30-50 Hz | Hue-Sync-grade smooth | Requires an Entertainment configuration on the bridge |

Both use the same walking-state flame algorithm: each bulb independently picks color/brightness targets weighted across four flame regions (orange body / deep red-orange / gutter dip / bright yellow peak), then walks toward each target with a per-tick step cap so motion ramps rather than teleports. State distribution and step sizes were tuned by hand to match what real fire looks like to the eye.

## Requirements

- Python 3.10 or newer (stdlib only — no pip packages)
- `openssl` CLI (for the streaming version) — Arch package `openssl`, Debian/Ubuntu `openssl`
- A Philips Hue Bridge V2 (`BSB002` or newer) on the same LAN
- Hue color bulbs (Extended Color Light type) — White Ambiance and dimmable-only bulbs work for the REST script but won't show full flame colors

## Setup

### 1. Find your bridge IP

```sh
curl -s https://discovery.meethue.com/
```

Returns JSON with the bridge's local IP, e.g. `192.168.1.100`.

### 2. Pair: press the bridge button, then run

```sh
curl -k -X POST https://<BRIDGE_IP>/api \
     -H 'Content-Type: application/json' \
     -d '{"devicetype":"hue-firelight#'"$(hostname)"'","generateclientkey":true}'
```

You have **30 seconds** after pressing the physical button. Response contains:

```json
[{"success":{
  "username": "abc123...",       // -> app_key
  "clientkey": "1A2B3C..."       // -> client_key (required for streaming only)
}}]
```

### 3. List your lights, pick which ones to target

```sh
curl -ks -H "hue-application-key: <APP_KEY>" \
     https://<BRIDGE_IP>/clip/v2/resource/light \
  | python3 -c 'import sys, json; [print(l["id"], l["metadata"]["name"]) for l in json.load(sys.stdin)["data"]]'
```

V1 numeric light IDs (used by the REST script) come from the V1 API instead:

```sh
curl -ks https://<BRIDGE_IP>/api/<APP_KEY>/lights | python3 -m json.tool
```

### 4. (Streaming only) Configure an Entertainment Area

Open the official Hue app on your phone → **Settings → Entertainment areas → Create new** → pick the bulbs you want syncing → save. The streaming script discovers the configuration UUID automatically:

```sh
curl -ks -H "hue-application-key: <APP_KEY>" \
     https://<BRIDGE_IP>/clip/v2/resource/entertainment_configuration \
  | python3 -m json.tool
```

Look for your area's `id` and the `channels` array (its length is the number of bulbs in the streaming session).

### 5. Drop credentials into the config file

Copy `config.example.json` to `~/.config/hue-firelight/config.json` and fill in the values:

```json
{
  "bridge": "192.168.1.100",
  "app_key": "abc123...",
  "client_key": "1A2B3C...",
  "ent_config": "fed839e5-...-...",
  "lights": [11, 12, 13, 15],
  "light_uuids": {
    "11": "da2389b9-...-...",
    "12": "9a1bfbe8-...-..."
  }
}
```

Field-by-field:
- `bridge` — bridge LAN IP from step 1
- `app_key` — the `username` from pairing (step 2). Required.
- `client_key` — the `clientkey` from pairing. Required for the streaming script only.
- `ent_config` — UUID of your Entertainment configuration (step 4). Streaming only.
- `lights` — list of integer V1 light IDs to flicker (REST script)
- `light_uuids` — V2 UUID map for clearing native effects before flicker starts. Optional; if absent, the REST script skips effect-clear and assumes the bulbs aren't running a native effect.

Or set the same values as environment variables (`HUE_BRIDGE`, `HUE_APP_KEY`, `HUE_CLIENT_KEY`, `HUE_ENT_CONFIG`, `HUE_LIGHTS`, `HUE_LIGHT_UUIDS`). Env vars override the config file.

## Usage

### Streaming (recommended)

```sh
./hue-flame-stream.py                       # 50 Hz, speed 0.5 — calibrated default
./hue-flame-stream.py --rate 30             # lower refresh, same tempo
./hue-flame-stream.py --rate 50 --speed 1.0 # smooth + faster flame
./hue-flame-stream.py --rate 50 --speed 0.3 # smooth + very slow flame
```

`--rate` is how often frames go to the bridge (10-50 Hz, smoothness). `--speed` is how fast the flame's *story* progresses in real time (0.2-3.0, tempo). They're independent — raising `--rate` from 30 to 50 alone makes the *same* flame smoother, not faster, because target dwells and step sizes are scaled inversely with the rate.

Ctrl-C cleanly deactivates the Entertainment session and shuts down the DTLS socket.

### REST

```sh
./hue-flame.py             # walking flame
./hue-flame.py --campfire  # same colors, ~20% slower pace
./hue-flame.py --lava      # dim deep-red slow molten glow
./hue-flame.py --warm      # static warm glow (no flicker)
./hue-flame.py --off       # all configured bulbs off
```

The REST script tops out at ~8 Hz/light because of Hue's bridge rate limit. Visually it's noticeably steppier than the streaming version, but it doesn't require an Entertainment configuration and runs on any color bulb.

## How the streaming transport works

Hue's Entertainment API is DTLS-PSK over UDP port 2100. Python's stdlib has no DTLS support, and the available third-party libraries (`python-mbedtls`) don't build against current `libmbedtls` 3.x at the time of writing. So the streaming script spawns `openssl s_client -dtls1_2 -psk_identity ... -psk ... -connect bridge:2100` as a subprocess and pipes binary HueStream v2 frames into its stdin. openssl owns the DTLS handshake and record framing; Python just generates color targets and encodes 52+7×N byte frames. The Entertainment configuration is activated via a V2 REST PUT before streaming starts and deactivated on exit.

## Algorithm notes

Each entertainment channel (streaming) or bulb (REST) holds independent state:

- Current HSV target
- Remaining frames until next target pick (`left`)

On every tick we walk current values toward the target with capped per-tick step sizes (`H_STEP`, `S_STEP`, `V_STEP`). When `left` reaches zero, a new target is picked from a weighted distribution:

| Region | Hue | Saturation | Value | Weight |
|---|---|---|---|---|
| Orange body | 0.03-0.09 (orange) | high | mid-high | 58% |
| Deep red-orange | 0.0-0.045 (red-orange) | high | mid | 30% |
| Gutter dip | 0.0-0.025 (deep red) | high | low | 8% |
| Yellow peak | 0.10-0.16 (yellow) | slightly desat (white-hot) | near max | 4% |

The capped step size means yellow peaks have to **climb through** orange — you see the flame building up rather than teleporting to bright. Each bulb starts with randomized initial state and a small phase offset so they don't flicker in lockstep.

## Files

- `hue-flame.py` — REST flame loop
- `hue-flame-stream.py` — Entertainment-API streaming flame loop
- `hue_config.py` — credential and target-list loader (used by both scripts)
- `config.example.json` — template for `~/.config/hue-firelight/config.json`

## Security

The bridge `app_key` and `client_key` are durable credentials — anyone holding them can control your bridge until you revoke them in the Hue app. Keep `~/.config/hue-firelight/config.json` out of any repo. The `.gitignore` in this repo excludes accidental copies inside the project tree, but the canonical location is your home config directory.

To revoke a paired application later, open the Hue phone app → **Settings → Hue Bridges → (your bridge) → Bridge settings → My apps**, find the entry by the `devicetype` string used during pairing, and remove it.

## License

MIT — see LICENSE.
