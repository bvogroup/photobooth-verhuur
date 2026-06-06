"""Test de StatusPoller met persistent dialog — 8 polls, meet tijd per poll."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import time
from dnp_status import StatusPoller

PRINTER = "DP-QW410 (Kopie 2)"
poller = StatusPoller(interval_sec=2.0, printer_name=PRINTER)

last = None
def on_change(st):
    global last
    now = time.monotonic()
    dt = (now - last) if last else 0
    last = now
    print(f"  [{time.strftime('%H:%M:%S')}] CHANGE: level={st.level.value}  code={st.code}  "
          f"label={st.label!r}  remain={st.prints_remaining}/{st.prints_total}  "
          f"life={st.life_counter}  method={st.error_method}")

poller.on_change(on_change)
print(f"Start poller met 2s interval, monitor 16 seconden...")
print(f"Doe nu eventueel iets met de printer (klep open/dicht) en zie of melding binnen 2-4 sec verschijnt.\n")
poller.start()
time.sleep(16)
print(f"\nStoppen...")
poller.stop()
print("Klaar")
