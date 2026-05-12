"""
Hue bridge credentials + light targeting for the firelight scripts.

Resolution order per setting:
  1. Environment variable (HUE_BRIDGE, HUE_APP_KEY, HUE_CLIENT_KEY,
     HUE_ENT_CONFIG, HUE_LIGHTS, HUE_LIGHT_UUIDS)
  2. JSON file at $HUE_FIRELIGHT_CONFIG (defaults to
     ~/.config/hue-firelight/config.json)
  3. Hard error with a setup-instructions hint pointing at README.md

The config file lives outside the repo on purpose — credentials never get
committed. See README.md for how to pair with a bridge and discover light IDs.
"""
import json
import os
import sys
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "HUE_FIRELIGHT_CONFIG",
    Path.home() / ".config" / "hue-firelight" / "config.json",
))

def _load_file():
    if not CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: could not read {CONFIG_PATH}: {e}", file=sys.stderr)
        return {}

_FILE_CFG = _load_file()


def _get(env_key, file_key, required=False, default=None):
    val = os.environ.get(env_key)
    if val is None:
        val = _FILE_CFG.get(file_key, default)
    if required and val in (None, ""):
        sys.exit(
            f"missing config: set the {env_key} env var or '{file_key}' in {CONFIG_PATH}\n"
            f"see README.md (Setup section) for how to obtain it"
        )
    return val


def bridge():       return _get("HUE_BRIDGE",     "bridge",     required=True)
def app_key():      return _get("HUE_APP_KEY",    "app_key",    required=True)
def client_key():   return _get("HUE_CLIENT_KEY", "client_key", required=True)
def ent_config():   return _get("HUE_ENT_CONFIG", "ent_config", required=True)


def lights():
    """Return list of integer V1 light IDs to target."""
    raw = _get("HUE_LIGHTS", "lights", required=True)
    if isinstance(raw, str):
        return [int(x) for x in raw.replace(",", " ").split()]
    return [int(x) for x in raw]


def light_uuids():
    """Optional: {light_id: V2_uuid} map for native-effect clear via V2 API.
    Returns {} if not configured — scripts handle absence gracefully."""
    raw = _get("HUE_LIGHT_UUIDS", "light_uuids", default={})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {str(k): v for k, v in raw.items()}
