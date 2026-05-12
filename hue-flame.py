#!/usr/bin/env python3
"""
Realistic flame effect over Philips Hue REST API.

Each configured bulb runs an independent walking-state flicker loop at ~8Hz
against the bridge. Hue's native fire/candle are slow color-faders; this
aims for actual fire look on Hue color bulbs. Credentials and target lights
load from hue_config (env vars or ~/.config/hue-flame/config.json).

Usage:
  hue-flame.py             # walking flame (deep red→orange with yellow flicker)
  hue-flame.py --campfire  # same colors, 20% slower pace
  hue-flame.py --lava      # dim deep-red slow molten glow
  hue-flame.py --warm      # static warm glow
  hue-flame.py --off       # all configured bulbs off

For higher-rate smooth streaming (DTLS over UDP, 30-50Hz), see
hue-flame-stream.py.
"""
import argparse, json, random, ssl, sys, threading, time, urllib.request
import hue_config

BRIDGE = hue_config.bridge()
APP_KEY = hue_config.app_key()
LIGHTS = hue_config.lights()
UUIDS  = hue_config.light_uuids()                 # optional; {} if not set

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def _put(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="PUT",
        headers={"hue-application-key": APP_KEY, "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, context=ctx, timeout=2)
    except Exception:
        pass  # rate-limit / transient bridge errors aren't fatal

def v1_state(lid, body):
    _put(f"https://{BRIDGE}/api/{APP_KEY}/lights/{lid}/state", body)

def v2_clear_effect(lid):
    uuid = UUIDS.get(str(lid))
    if uuid:
        _put(f"https://{BRIDGE}/clip/v2/resource/light/{uuid}",
             {"effects": {"effect": "no_effect"}})

# Per-bulb walking state. Each tick the bulb moves toward its current target;
# when target reached (or hold expires) we pick a new target. Capped step size
# means adjacent commanded states stay close, so brightness/hue ramp through
# intermediate values instead of teleporting (deep red → bright yellow in one
# tick used to look like a cut, not a climb).
# Per-bulb starting state randomized so the 4 bulbs are already at different
# points in the flicker cycle at tick 0 — prevents the visual lockstep where
# all 4 hit "left=0, pick new target" on the same wall-clock tick.
_state = {lid: {"bri": random.randint(110, 220),
                "hue": random.randint(0, 5500),
                "sat": random.randint(244, 254),
                "tgt_bri": random.randint(110, 220),
                "tgt_hue": random.randint(0, 5500),
                "tgt_sat": random.randint(244, 254),
                "left": random.randint(0, 3)}
          for lid in LIGHTS}

MAX_BRI_STEP = 45
MAX_HUE_STEP = 1800
MAX_SAT_STEP = 15

def _pick_target(s):
    r = random.random()
    if r < 0.04:                        # yellow peak (rare, climbs through orange)
        s["tgt_bri"] = random.randint(245, 254)
        s["tgt_hue"] = random.randint(10500, 13000)   # deeper into yellow
        s["tgt_sat"] = random.randint(215, 245)       # slight desat = hot-white
        s["left"]   = random.randint(2, 3)            # brief peak, climb-fall
    elif r < 0.12:                       # gutter dip
        s["tgt_bri"] = random.randint(25, 90)
        s["tgt_hue"] = random.randint(0, 2000)
        s["tgt_sat"] = random.randint(248, 254)
        s["left"]   = random.randint(2, 4)
    elif r < 0.42:                       # deep red-orange cooling
        s["tgt_bri"] = random.randint(130, 200)
        s["tgt_hue"] = random.randint(0, 2500)
        s["tgt_sat"] = random.randint(248, 254)
        s["left"]   = random.randint(1, 3)
    else:                                # main orange body (most common)
        s["tgt_bri"] = random.randint(160, 230)
        s["tgt_hue"] = random.randint(2000, 5500)
        s["tgt_sat"] = random.randint(244, 254)
        s["left"]   = random.randint(1, 2)

def flame_state(lid):
    s = _state[lid]
    if s["left"] <= 0:
        _pick_target(s)
    # walk toward target with capped per-tick step
    dbri = s["tgt_bri"] - s["bri"]
    s["bri"] += max(-MAX_BRI_STEP, min(MAX_BRI_STEP, dbri))
    dhue = s["tgt_hue"] - s["hue"]
    s["hue"] += max(-MAX_HUE_STEP, min(MAX_HUE_STEP, dhue))
    dsat = s["tgt_sat"] - s["sat"]
    s["sat"] += max(-MAX_SAT_STEP, min(MAX_SAT_STEP, dsat))
    s["left"] -= 1
    return {"on": True,
            "hue": s["hue"],
            "sat": s["sat"],
            "bri": s["bri"],
            "transitiontime": 1}

def lava_state():
    # Deep red, dim, very slow crossfade — molten glow rather than active flame.
    return {"on": True,
            "hue": random.randint(0, 2500),
            "sat": 254,
            "bri": random.randint(10, 80),
            "transitiontime": 100}                  # 10s fade between targets

def flame_bulb(lid, stop, mode):
    v2_clear_effect(lid)                # kill any native effect first
    # Phase offset so the 4 threads don't fire on the same wall-clock cadence
    if stop.wait(random.uniform(0, 0.18)): return
    while not stop.is_set():
        if mode == "campfire":
            # Same walking flame colors as default, paced ~20% slower for a
            # mellower campfire feel
            v1_state(lid, flame_state(lid))
            if stop.wait(0.144 + random.uniform(-0.036, 0.06)): break
        elif mode == "lava":
            v1_state(lid, lava_state())
            if stop.wait(10.0): break               # match the 10s fade interval
        else:                                       # default walking flame
            v1_state(lid, flame_state(lid))
            # ~8Hz tick + 100ms transitiontime — fades land as next target arrives
            if stop.wait(0.12 + random.uniform(-0.03, 0.05)): break

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--off",      action="store_true", help="all 4 bulbs off")
    g.add_argument("--warm",     action="store_true", help="static warm glow")
    g.add_argument("--campfire", action="store_true", help="brighter yellow-orange erratic flicker")
    g.add_argument("--lava",     action="store_true", help="dim deep-red slow molten glow")
    args = ap.parse_args()

    if args.off:
        for lid in LIGHTS:
            v2_clear_effect(lid)
            v1_state(lid, {"on": False})
        return
    if args.warm:
        for lid in LIGHTS:
            v2_clear_effect(lid)
            v1_state(lid, {"on": True, "hue": 6000, "sat": 220, "bri": 200,
                           "transitiontime": 4})
        return

    mode = "campfire" if args.campfire else "lava" if args.lava else "flame"
    stop = threading.Event()
    threads = [threading.Thread(target=flame_bulb, args=(lid, stop, mode), daemon=True)
               for lid in LIGHTS]
    for t in threads:
        t.start()
    print(f"{mode} running on lights {LIGHTS} — Ctrl-C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        time.sleep(0.3)
        for lid in LIGHTS:
            v1_state(lid, {"on": True, "hue": 6000, "sat": 220, "bri": 200,
                           "transitiontime": 4})

if __name__ == "__main__":
    main()
