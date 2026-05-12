#!/usr/bin/env python3
"""
hue-flame-stream.py — flame effect via Hue Entertainment API (DTLS streaming).

REST is capped around 8-10Hz per light; the Entertainment API streams over
DTLS-PSK at 25-50Hz, which is what SignalRGB / Hue Sync use for buttery
smoothness. Bypasses python-mbedtls (broken against mbedtls 3.x) by piping
binary HueStream v2 frames into an openssl s_client subprocess that owns
the DTLS handshake and record framing.

Usage:
  hue-flame-stream.py                       # 50Hz, speed 0.5 — the calibrated default
  hue-flame-stream.py --rate 30             # lower refresh
  hue-flame-stream.py --rate 50 --speed 1.0 # full tempo (faster flame story)
  hue-flame-stream.py --rate 50 --speed 0.3 # slow, mellow flame

Rate and speed are independent: rate is how often we push frames to the bridge
(smoothness); speed is how fast the flame's story plays (state-change tempo).
Credentials and the entertainment configuration UUID load from hue_config (env
vars or ~/.config/hue-flame/config.json). Number of channels is auto-derived
from the bridge's entertainment configuration channel count.
"""
import argparse, colorsys, json, random, signal, ssl, subprocess, sys, threading, time, urllib.request
import hue_config

BRIDGE          = hue_config.bridge()
APP_KEY         = hue_config.app_key()
CLIENT_KEY_HEX  = hue_config.client_key()
ENT_CONFIG_UUID = hue_config.ent_config()
# Channel count is derived at runtime from the entertainment configuration
# the user paired with — we don't hardcode a 4 anywhere.
N_CHANNELS      = 0

# --- REST helper for activate/deactivate streaming (V2 API) ---
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def v2_put(path, body):
    req = urllib.request.Request(
        f"https://{BRIDGE}/clip/v2/resource/{path}",
        data=json.dumps(body).encode(),
        method="PUT",
        headers={"hue-application-key": APP_KEY, "Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, context=ssl_ctx, timeout=5).read().decode()

# --- HueStream v2 binary frame encoding ---
def encode_frame(rgb16_per_channel):
    """Build one HueStream v2 frame (RGB color space).
    rgb16_per_channel: list of (R, G, B) tuples, each component 0-65535.
    """
    f  = b"HueStream"                                # 9 bytes magic
    f += bytes([0x02, 0x00])                          # version 2.0
    f += bytes([0x00])                                # sequence (bridge ignores)
    f += bytes([0x00, 0x00])                          # reserved
    f += bytes([0x00])                                # color space: 0 = RGB
    f += bytes([0x00])                                # reserved
    f += ENT_CONFIG_UUID.encode()                     # 36 bytes ASCII UUID
    for ch, (r, g, b) in enumerate(rgb16_per_channel):
        f += bytes([ch])
        f += r.to_bytes(2, "big")
        f += g.to_bytes(2, "big")
        f += b.to_bytes(2, "big")
    return f

# --- DTLS-PSK transport via openssl s_client subprocess ---
def open_dtls():
    cmd = [
        "openssl", "s_client",
        "-dtls1_2",
        "-cipher", "PSK-AES128-GCM-SHA256",          # what Hue speaks
        "-psk_identity", APP_KEY,
        "-psk", CLIENT_KEY_HEX,
        "-connect", f"{BRIDGE}:2100",
        "-quiet",
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

# --- Flame state machine in HSV space (h in 0..1 where 0=red, ~0.083=orange,
#     ~0.16=yellow). Walking-state with capped per-tick deltas; one state per
#     entertainment channel. Initialized at runtime once N_CHANNELS is known.
_state = []

def _init_state(n):
    return [{
        "h": random.uniform(0.0, 0.06),
        "s": random.uniform(0.95, 1.0),
        "v": random.uniform(0.40, 0.75),
        "tgt_h": random.uniform(0.0, 0.06),
        "tgt_s": 1.0,
        "tgt_v": random.uniform(0.40, 0.75),
        "left": random.randint(0, 12),
    } for _ in range(n)]

def _discover_channel_count():
    """Ask the bridge how many channels this entertainment configuration has."""
    url = f"https://{BRIDGE}/clip/v2/resource/entertainment_configuration/{ENT_CONFIG_UUID}"
    req = urllib.request.Request(url, headers={"hue-application-key": APP_KEY})
    data = json.loads(urllib.request.urlopen(req, context=ssl_ctx, timeout=5).read())
    return len(data["data"][0]["channels"])

# Algorithm is calibrated at REFERENCE_RATE Hz with speed=1.0. At other
# rates/speeds we scale `left` durations and step sizes inversely so the
# flame's perceived tempo stays constant regardless of refresh rate.
REFERENCE_RATE = 30
DURATION_SCALE = 1.0    # set in main() from args.rate and args.speed
STEP_SCALE     = 1.0

# Targets describe flame regions (h fraction = degrees/360):
#   yellow peak    h 0.10-0.16 (36°-58°)   s desat (white-hot)   v near max
#   gutter         h 0.0-0.025 (deep red)  s max                 v low
#   red-orange    h 0.0-0.045              s max                 v mid
#   orange body   h 0.03-0.09              s max                 v mid-high
def _dur(a, b):
    """Scale a frame-count range by DURATION_SCALE; min 2 frames."""
    return max(2, int(random.randint(a, b) * DURATION_SCALE))

def pick_target(s):
    r = random.random()
    if r < 0.04:                                       # yellow peak
        s["tgt_h"] = random.uniform(0.105, 0.158)
        s["tgt_s"] = random.uniform(0.78, 0.93)         # desat -> hot-white
        s["tgt_v"] = random.uniform(0.92, 1.0)
        s["left"]  = _dur(8, 14)
    elif r < 0.12:                                     # gutter
        s["tgt_h"] = random.uniform(0.0, 0.025)
        s["tgt_s"] = random.uniform(0.95, 1.0)
        s["tgt_v"] = random.uniform(0.08, 0.30)
        s["left"]  = _dur(10, 18)
    elif r < 0.42:                                     # deep red-orange
        s["tgt_h"] = random.uniform(0.0, 0.045)
        s["tgt_s"] = random.uniform(0.96, 1.0)
        s["tgt_v"] = random.uniform(0.42, 0.68)
        s["left"]  = _dur(6, 14)
    else:                                              # orange body
        s["tgt_h"] = random.uniform(0.030, 0.090)
        s["tgt_s"] = random.uniform(0.95, 1.0)
        s["tgt_v"] = random.uniform(0.55, 0.80)
        s["left"]  = _dur(6, 12)

# Per-tick step caps at REFERENCE_RATE — scaled at startup by STEP_SCALE
H_STEP_BASE = 0.010
S_STEP_BASE = 0.015
V_STEP_BASE = 0.025

def step(s):
    if s["left"] <= 0:
        pick_target(s)
    for k, base in (("h", H_STEP_BASE), ("s", S_STEP_BASE), ("v", V_STEP_BASE)):
        st = base * STEP_SCALE
        d = s[f"tgt_{k}"] - s[k]
        s[k] += max(-st, min(st, d))
    s["left"] -= 1

def hsv_to_rgb16(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 65535), int(g * 65535), int(b * 65535))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate",  type=int,   default=50,  help="frame rate Hz (10-50, default 50)")
    ap.add_argument("--speed", type=float, default=0.5, help="flame tempo (0.2-3.0, default 0.5)")
    args = ap.parse_args()
    if not (10 <= args.rate <= 50):
        print("rate must be 10-50 Hz", file=sys.stderr)
        sys.exit(1)
    if not (0.2 <= args.speed <= 3.0):
        print("speed must be 0.2-3.0", file=sys.stderr)
        sys.exit(1)
    period = 1.0 / args.rate

    # Decouple frame rate from flame tempo: longer dwells + smaller steps at
    # higher rates keep the wall-clock pace constant. Speed multiplier
    # adjusts the perceived tempo on top of that.
    global DURATION_SCALE, STEP_SCALE, N_CHANNELS, _state
    DURATION_SCALE = (args.rate / REFERENCE_RATE) / args.speed
    STEP_SCALE     = (REFERENCE_RATE / args.rate) * args.speed

    try:
        N_CHANNELS = _discover_channel_count()
    except Exception as e:
        print(f"failed to query entertainment_configuration {ENT_CONFIG_UUID}: {e}", file=sys.stderr)
        sys.exit(1)
    _state = _init_state(N_CHANNELS)

    print(f"[1/3] activating entertainment streaming ({N_CHANNELS} channels) on config {ENT_CONFIG_UUID[:8]}...")
    try:
        v2_put(f"entertainment_configuration/{ENT_CONFIG_UUID}", {"action": "start"})
    except Exception as e:
        print(f"  FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.6)            # bridge needs a moment after activate

    print(f"[2/3] opening DTLS-PSK to {BRIDGE}:2100 via openssl s_client...")
    proc = open_dtls()
    time.sleep(0.9)            # let DTLS handshake complete
    if proc.poll() is not None:
        print("  openssl exited early; check bridge state / clientkey / port", file=sys.stderr)
        v2_put(f"entertainment_configuration/{ENT_CONFIG_UUID}", {"action": "stop"})
        sys.exit(1)

    print(f"[3/3] streaming flame at {args.rate}Hz on channels 0-3 — Ctrl-C to stop")
    stop = threading.Event()
    signal.signal(signal.SIGINT,  lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    next_tick = time.monotonic()
    frames = 0
    t0 = time.monotonic()
    try:
        while not stop.is_set():
            rgbs = []
            for s in _state:
                step(s)
                rgbs.append(hsv_to_rgb16(s["h"], s["s"], s["v"]))
            try:
                proc.stdin.write(encode_frame(rgbs))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                print("DTLS pipe broken — openssl died", file=sys.stderr)
                break
            frames += 1
            next_tick += period
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                stop.wait(sleep)
            else:
                next_tick = time.monotonic()   # if we slipped, resync
    finally:
        elapsed = time.monotonic() - t0
        if elapsed > 0:
            print(f"\nsent {frames} frames in {elapsed:.1f}s ({frames/elapsed:.1f} Hz actual)")
        try: proc.stdin.close()
        except Exception: pass
        try: proc.terminate(); proc.wait(timeout=2)
        except Exception: pass
        try:
            v2_put(f"entertainment_configuration/{ENT_CONFIG_UUID}", {"action": "stop"})
        except Exception:
            pass
        print("stopped, entertainment session deactivated")

if __name__ == "__main__":
    main()
