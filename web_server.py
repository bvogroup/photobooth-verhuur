"""
Local web server for photo downloads.

Serves a mobile-friendly page where guests can download their
photo strip and individual photos after scanning the QR code.

Runs on the local WiFi network so all guests can access it
from their phones without internet.
"""

import os
import json
import threading
from flask import Flask, render_template, send_from_directory, abort, request, jsonify

import config


app = Flask(
    __name__,
    template_folder=os.path.join(config.BUNDLE_DIR, "web", "templates"),
    static_folder=None,  # We serve photos directly
)

# Session registry: session_id -> {strip_path, photos: [...], template_name, _ts}
# Auto-prunes sessions older than 1 hour to prevent unbounded memory growth
_sessions = {}
_sessions_lock = threading.Lock()
_MAX_SESSION_AGE = 3600  # 1 hour
_MAX_SESSIONS = 200  # Hard limit


def _prune_old_sessions():
    """Remove sessions older than 1 hour (called on every register)."""
    import time
    now = time.time()
    to_remove = [sid for sid, s in _sessions.items()
                 if now - s.get("_ts", 0) > _MAX_SESSION_AGE]
    for sid in to_remove:
        del _sessions[sid]
    # Hard limit: if still too many, remove oldest
    if len(_sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(_sessions.items(), key=lambda x: x[1].get("_ts", 0))
        for sid, _ in sorted_sessions[:len(_sessions) - _MAX_SESSIONS]:
            del _sessions[sid]
    if to_remove:
        print(f"[WEB] {len(to_remove)} oude sessies opgeruimd")


def register_session(session_id, strip_path, photo_paths, template_name="",
                      boomerang_path=None):
    """Register a completed photo session for download."""
    import time
    with _sessions_lock:
        _prune_old_sessions()
        _sessions[session_id] = {
            "strip_path": strip_path,
            "photos": list(photo_paths),
            "template_name": template_name,
            "boomerang_path": boomerang_path,
            "_ts": time.time(),
        }
    boom_msg = " + boomerang" if boomerang_path else ""
    print(f"[WEB] Sessie geregistreerd: {session_id} ({len(photo_paths)} foto's{boom_msg})")


@app.route("/session/<session_id>")
def session_page(session_id):
    """Render the download page for a session."""
    with _sessions_lock:
        session = _sessions.get(session_id)

    if not session:
        abort(404)

    # Build file info for template
    strip_filename = os.path.basename(session["strip_path"]) if session["strip_path"] else None
    photo_filenames = [os.path.basename(p) for p in session["photos"]]
    boomerang_filename = (os.path.basename(session["boomerang_path"])
                          if session.get("boomerang_path") else None)

    return render_template(
        "session.html",
        session_id=session_id,
        strip_filename=strip_filename,
        photo_filenames=photo_filenames,
        template_name=session.get("template_name", ""),
        boomerang_filename=boomerang_filename,
        config_email_enabled=getattr(config, 'EMAIL_ENABLED', False),
    )


@app.route("/download/<session_id>/<filename>")
def download_file(session_id, filename):
    """Serve a photo file for download."""
    with _sessions_lock:
        session = _sessions.get(session_id)

    if not session:
        abort(404)

    # Verify the file belongs to this session
    all_files = list(session["photos"])
    if session["strip_path"]:
        all_files.append(session["strip_path"])
    if session.get("boomerang_path"):
        all_files.append(session["boomerang_path"])

    valid_filenames = {os.path.basename(f) for f in all_files}
    if filename not in valid_filenames:
        abort(403)

    # Add version to download filename so user can identify the build
    version = getattr(config, 'VERSION', 'v1')
    name, ext = os.path.splitext(filename)
    download_name = f"bootharoo_{version}_{name}{ext}"

    return send_from_directory(
        config.PHOTO_DIR, filename,
        as_attachment=True,
        download_name=download_name
    )


_email_rate_limit = {}  # {session_id: last_send_timestamp}

@app.route("/email/<session_id>", methods=["POST"])
def send_email(session_id):
    """Send session photos via email (rate limited: 1 per session per 60s)."""
    if not getattr(config, 'EMAIL_ENABLED', False):
        return jsonify({"success": False, "error": "E-mail is niet ingeschakeld"}), 400

    # Rate limit: max 5 emails per session per 60 seconds
    import time as _time
    now = _time.time()
    rate_key = f"{session_id}"
    if rate_key not in _email_rate_limit:
        _email_rate_limit[rate_key] = []
    # Remove entries older than 60 seconds
    _email_rate_limit[rate_key] = [ts for ts in _email_rate_limit[rate_key] if now - ts < 60]
    if len(_email_rate_limit[rate_key]) >= 5:
        return jsonify({"success": False, "error": "Te veel verzoeken, probeer later opnieuw"}), 429
    _email_rate_limit[rate_key].append(now)
    # Clean old entries (older than 5 minutes)
    cutoff = now - 300
    for k in list(_email_rate_limit):
        _email_rate_limit[k] = [ts for ts in _email_rate_limit[k] if ts > cutoff]
        if not _email_rate_limit[k]:
            del _email_rate_limit[k]

    with _sessions_lock:
        session = _sessions.get(session_id)

    if not session:
        abort(404)

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()

    import re
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
        return jsonify({"success": False, "error": "Ongeldig e-mailadres"}), 400

    try:
        from email_sender import load_smtp_config, send_photo_email

        smtp_config = load_smtp_config()
        if not smtp_config:
            return jsonify({"success": False,
                            "error": "E-mail niet geconfigureerd op server"}), 500

        # Collect attachments
        attachments = []
        if session.get("strip_path"):
            attachments.append(session["strip_path"])
        if session.get("boomerang_path"):
            attachments.append(session["boomerang_path"])

        # Send email in background thread to prevent blocking
        def _send():
            try:
                send_photo_email(email, attachments, smtp_config=smtp_config)
                print(f"[WEB] Email verzonden naar {email}")
            except Exception as ex:
                print(f"[WEB] Email fout: {ex}")

        threading.Thread(target=_send, daemon=True).start()
        return jsonify({"success": True, "message": "E-mail wordt verzonden..."})

    except Exception as e:
        print(f"[WEB] Email fout: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def start_server(port=8080):
    """Start the Flask server in a daemon thread."""
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.WARNING)

    def _run():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    print(f"[WEB] Server gestart op poort {port}")
    return thread
