"""Eenmalig diagnose + backfill script voor het digitale album.

Draait de backfill_marks() voor alle lokale upload-queues waarvan een
token bekend is (events/*.json of token.json). Veilig om te herhalen.

Gebruik:  python run_backfill.py            (diagnose + backfill)
          python run_backfill.py --dry-run  (alleen diagnose)
"""
import json
import os
import sys

import config
from cloud_uploader import (
    discover_pending_uploads, backfill_marks, read_queue_token, _data_root,
)

dry_run = "--dry-run" in sys.argv

upload_root = os.path.join(_data_root(), "upload_queue")
snapshots = discover_pending_uploads(config.EVENTS_DIR)

print(f"{'Booking':38} {'upl':>4} {'pend':>4} {'token':6} {'backfilled':>10}")
print("-" * 70)

todo = []
for entry in sorted(os.listdir(upload_root)):
    bdir = os.path.join(upload_root, entry)
    if not os.path.isdir(bdir):
        continue
    qpath = os.path.join(bdir, "queue.json")
    try:
        with open(qpath, "r", encoding="utf-8") as f:
            q = json.load(f)
    except Exception:
        q = {"files": {}}
    files = q.get("files", {})
    uploaded = sum(1 for m in files.values() if m.get("state") == "uploaded")
    pending = sum(1 for m in files.values() if m.get("state") == "pending")
    backfilled = "ja" if q.get("marks_backfilled_at") else "nee"

    # Token: uit discover (events) of token.json
    token = (snapshots.get(entry, {}) or {}).get("token", "")
    if not token:
        token = read_queue_token(entry).get("token", "")
    tok_label = "JA" if token else "NEE"

    print(f"{entry:38} {uploaded:>4} {pending:>4} {tok_label:6} {backfilled:>10}")
    if token and uploaded > 0 and not q.get("marks_backfilled_at"):
        todo.append((entry, token, uploaded))

print()
if not todo:
    print("Niks te backfillen (geen queues met uploads + token + zonder flag).")
    sys.exit(0)

if dry_run:
    print(f"DRY RUN — zou {len(todo)} queue(s) backfillen:")
    for bid, _tok, n in todo:
        print(f"  {bid}: {n} foto's")
    sys.exit(0)

for bid, token, n in todo:
    print(f"\n>>> Backfill {bid} ({n} foto's)...")
    ok = backfill_marks(bid, token)
    print(f">>> {ok} aangemeld")

print("\nKlaar. Check het album in het portaal.")
