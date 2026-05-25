"""
Authentication module for Bootharoo Photobooth.

Handles subscription-based login against the Bootharoo CRM API.
Stores session locally in settings.json for offline use.

Plans:
  - starter: Offline features (photos, strips, print, branding, camera)
  - professional: All starter + QR sharing, email, GIF/boomerang
"""

import os
import json
from datetime import datetime, timezone

import config

# ── Feature definitions per plan ──

PLAN_FEATURES = {
    "starter": {
        "photos", "strips", "print", "branding", "camera",
        "templates", "overlays", "countdown", "preview",
        "boomerang", "gif",
    },
    "professional": {
        "photos", "strips", "print", "branding", "camera",
        "templates", "overlays", "countdown", "preview",
        "qr_sharing", "email", "boomerang", "gif",
        "data_collection", "sharing", "payments",
    },
}

# All features that require Professional
PRO_ONLY_FEATURES = PLAN_FEATURES["professional"] - PLAN_FEATURES["starter"]


def get_device_id():
    """Get or create a unique device ID for this installation.

    Generated once at first login, persists across sessions.
    Survives image cloning because it's only created at login time.
    """
    import uuid
    settings = _read_settings()
    device_id = settings.get("device_id", "")
    if not device_id:
        device_id = str(uuid.uuid4())
        settings["device_id"] = device_id
        _write_settings(settings)
        print(f"[AUTH] Nieuwe device ID gegenereerd: {device_id[:8]}...")
    return device_id


def _read_settings():
    """Read settings.json, return dict."""
    if not os.path.isfile(config.SETTINGS_FILE):
        return {}
    try:
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_settings(data):
    """Write dict to settings.json."""
    os.makedirs(os.path.dirname(config.SETTINGS_FILE), exist_ok=True)
    with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_session(user_data, token):
    """Save login session locally.

    user_data dict keys: name, license_key, plan, subscription_end, active
    (or legacy: id, email, plan, subscription_end, active)
    token: Bearer/anon token string
    """
    settings = _read_settings()
    settings["auth_token"] = token
    settings["auth_user"] = {
        "id": user_data.get("id", ""),
        "email": user_data.get("email", ""),
        "name": user_data.get("name", ""),
        "license_key": user_data.get("license_key", ""),
        "plan": user_data.get("plan", "starter"),
        "subscription_end": user_data.get("subscription_end", ""),
        "active": user_data.get("active", False),
        "booth_secret": user_data.get("booth_secret", ""),
        "payment_link_url": user_data.get("payment_link_url", ""),
        "is_permanent": user_data.get("is_permanent", False),
    }
    settings["auth_last_verified"] = datetime.now(timezone.utc).isoformat()
    _write_settings(settings)
    _invalidate_session_cache()
    identifier = user_data.get("name") or user_data.get("email") or "?"
    print(f"[AUTH] Sessie opgeslagen voor {identifier}")


_session_cache = {"user": None, "token": None, "dirty": True}


def _invalidate_session_cache():
    """Mark session cache as dirty (called after save/clear)."""
    _session_cache["dirty"] = True


def load_session():
    """Load saved session (cached — only reads disk when dirty).

    Returns (user_data_dict, token) or (None, None) if not logged in.
    """
    if not _session_cache["dirty"] and _session_cache["user"] is not None:
        return _session_cache["user"], _session_cache["token"]

    settings = _read_settings()
    token = settings.get("auth_token", "")
    user = settings.get("auth_user")
    if not token or not user:
        _session_cache.update({"user": None, "token": None, "dirty": False})
        return None, None
    # Valid if has email OR name OR license_key
    if not (user.get("email") or user.get("name") or user.get("license_key")):
        _session_cache.update({"user": None, "token": None, "dirty": False})
        return None, None
    _session_cache.update({"user": user, "token": token, "dirty": False})
    return user, token


def clear_session():
    """Remove login session (logout)."""
    settings = _read_settings()
    settings.pop("auth_token", None)
    settings.pop("auth_user", None)
    settings.pop("auth_last_verified", None)
    _write_settings(settings)
    _invalidate_session_cache()
    print("[AUTH] Sessie verwijderd (uitgelogd)")


def is_subscription_valid_offline():
    """Check if subscription is still valid based on locally stored end date.

    Returns (valid: bool, end_date_str: str, plan: str).
    """
    user, token = load_session()
    if not user:
        return False, "", ""

    end_str = user.get("subscription_end", "")
    plan = user.get("plan", "starter")

    if not end_str:
        return False, "", plan

    is_permanent = user.get("is_permanent", False)

    try:
        # Parse ISO datetime (handle Z suffix and +00:00)
        end_str_clean = end_str.replace("Z", "+00:00")
        end_date = datetime.fromisoformat(end_str_clean)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        pro_active = now < end_date

        if is_permanent:
            # Permanent license: always valid, plan degrades after pro expires
            effective_plan = "professional" if pro_active else "starter"
            return True, end_str, effective_plan

        return pro_active, end_str, plan
    except Exception as e:
        print(f"[AUTH] Datum parse fout: {e}")
        # Permanent licenses are always valid even with parse errors
        if is_permanent:
            return True, end_str, "starter"
        return False, end_str, plan


def is_feature_allowed(feature_name):
    """Check if a feature is allowed under the current plan.

    Args:
        feature_name: e.g. "email", "qr_sharing", "boomerang", "gif"

    Returns True if allowed.
    """
    user, _ = load_session()
    if not user:
        return feature_name in PLAN_FEATURES.get("starter", set())
    plan = user.get("plan", "starter")
    allowed = PLAN_FEATURES.get(plan, PLAN_FEATURES["starter"])
    return feature_name in allowed


def get_plan():
    """Return current plan name ('starter' or 'professional'), or '' if not logged in."""
    user, _ = load_session()
    if not user:
        return ""
    return user.get("plan", "starter")


def login(name, license_key):
    """Verify a license key + name via the Supabase verify-license edge function.

    Args:
        name: Name linked to the license (case-insensitive)
        license_key: License key (format: XXXX-XXXX-XXXX-XXXX)

    Returns:
        (success: bool, user_data: dict or None, error_message: str)
    """
    import urllib.request
    import urllib.error

    supabase_url = getattr(config, "SUPABASE_URL", "")
    anon_key = getattr(config, "SUPABASE_ANON_KEY", "")
    if not supabase_url:
        return False, None, "Supabase URL niet geconfigureerd"

    url = f"{supabase_url.rstrip('/')}/functions/v1/verify-license"
    device = get_device_id()
    payload = json.dumps({
        "license_key": license_key.strip().upper(),
        "name": name.strip(),
        "device_id": device,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("valid"):
            plan = data.get("plan", "starter")
            expires = data.get("expiresAt", "")
            user = {
                "name": name.strip(),
                "license_key": license_key.strip().upper(),
                "plan": plan,
                "subscription_end": expires,
                "active": True,
                "booth_secret": data.get("boothSecret", ""),
                "payment_link_url": data.get("paymentLinkUrl", ""),
                "is_permanent": data.get("isPermanent", False),
            }
            save_session(user, anon_key)
            return True, user, ""
        else:
            error = data.get("error", "Ongeldige licentie")
            return False, None, error

    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            return False, None, body.get("error", f"Server fout ({e.code})")
        except Exception:
            return False, None, f"Server fout ({e.code})"

    except urllib.error.URLError as e:
        return False, None, f"Geen internetverbinding.\n({e.reason})"

    except Exception as e:
        return False, None, f"Onverwachte fout: {e}"


def verify_session_online(api_base_url=None):
    """Re-verify the saved license key via Supabase verify-license edge function.

    Checks the license key online to get the latest expiry date
    (handles automatic subscription renewals).

    Returns:
        (success: bool, user_data: dict or None, error_message: str)
        If no internet, returns (False, None, "offline")
    """
    import urllib.request
    import urllib.error

    user, token = load_session()
    if not user or not token:
        return False, None, "Geen sessie"

    license_key = user.get("license_key", "")
    name = user.get("name", "")

    if not license_key:
        # Legacy session without license key — use offline check
        valid, _, plan = is_subscription_valid_offline()
        if valid:
            return True, user, ""
        clear_session()
        return False, None, "Abonnement verlopen"

    # Re-verify license key online to get latest expiry date
    supabase_url = getattr(config, "SUPABASE_URL", "")
    anon_key = getattr(config, "SUPABASE_ANON_KEY", "")
    if not supabase_url:
        # No URL configured — fall back to offline
        valid, _, _ = is_subscription_valid_offline()
        return (True, user, "") if valid else (False, None, "offline")

    url = f"{supabase_url.rstrip('/')}/functions/v1/verify-license"
    device = get_device_id()
    payload = json.dumps({"license_key": license_key, "name": name, "device_id": device}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
    }

    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("valid"):
            # Check device ID — if another device activated, this one gets kicked
            server_device = data.get("activeDeviceId", "")
            if server_device and server_device != device:
                print(f"[AUTH] Licentie actief op ander apparaat ({server_device[:8]}...)")
                clear_session()
                return False, None, "device_mismatch"

            # Update local session with latest expiry + payment info
            new_expiry = data.get("expiresAt", user.get("subscription_end", ""))
            new_plan = data.get("plan", user.get("plan", "starter"))
            updated_user = {
                **user,
                "subscription_end": new_expiry,
                "plan": new_plan,
                "active": True,
                "booth_secret": data.get("boothSecret", user.get("booth_secret", "")),
                "payment_link_url": data.get("paymentLinkUrl", user.get("payment_link_url", "")),
                "is_permanent": data.get("isPermanent", user.get("is_permanent", False)),
            }
            save_session(updated_user, token)
            print(f"[AUTH] Online verificatie OK — geldig tot {new_expiry}")
            return True, updated_user, ""
        else:
            # License no longer valid
            error = data.get("error", "Licentie niet meer geldig")
            print(f"[AUTH] Online verificatie: {error}")
            clear_session()
            return False, None, error

    except (urllib.error.URLError, OSError):
        # No internet — fall back to offline expiry check
        print("[AUTH] Geen internet — offline check")
        valid, _, plan = is_subscription_valid_offline()
        if valid:
            return True, user, ""
        clear_session()
        return False, None, "Abonnement verlopen"
    except Exception as e:
        print(f"[AUTH] Verificatie fout: {e}")
        # On error, trust local session
        valid, _, _ = is_subscription_valid_offline()
        return (True, user, "") if valid else (False, None, str(e))


def check_internet():
    """Quick check if there is internet connectivity."""
    import urllib.request
    try:
        urllib.request.urlopen("https://www.google.com", timeout=5)
        return True
    except Exception:
        return False


def startup_auth_check():
    """Full authentication check at app startup.

    Returns:
        (allowed: bool, user: dict or None, plan: str, message: str)

    Logic:
        1. No saved session → must login
        2. Has session + internet → verify online, update session
        3. Has session + no internet → check local subscription_end date
        4. Subscription expired → must re-login
    """
    user, token = load_session()

    # 1. No session at all
    if not user or not token:
        return False, None, "", "login_required"

    plan = user.get("plan", "starter")

    # 2. Try online verification
    has_internet = check_internet()

    if has_internet:
        success, updated_user, error = verify_session_online()
        if success and updated_user:
            plan = updated_user.get("plan", plan)
            return True, updated_user, plan, ""
        elif error != "offline":
            # Server said session is invalid
            return False, None, "", error

    # 3. Offline or online check failed — use local subscription_end
    valid, end_str, plan = is_subscription_valid_offline()
    if valid:
        print(f"[AUTH] Offline modus — abonnement geldig tot {end_str}")
        return True, user, plan, ""
    else:
        if end_str:
            return False, None, "", f"Je abonnement is verlopen ({end_str}).\nVerbind met internet en log opnieuw in."
        else:
            return False, None, "", "Je sessie is verlopen. Log opnieuw in."
