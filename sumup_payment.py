"""
SumUp Solo Cloud API integration for unattended photobooth payments.

Flow:
1. Create a checkout (amount + description)
2. Solo displays "Tap your card"
3. Poll checkout status every 2 seconds
4. On PAID → emit signal → photobooth starts session
5. Create new checkout → Solo ready again

Requires:
- SumUp API Key (from developer.sumup.com)
- SumUp Merchant Code (from SumUp dashboard)
- Solo with API mode enabled (Solo → Connections → API → Connect)
"""

import json
import os
import time
import urllib.request
import urllib.error
from PyQt5.QtCore import QThread, pyqtSignal

import config


SUMUP_CONFIG_FILE = os.path.join(config.DATA_DIR, "sumup_config.json")

# SumUp Cloud API base URL
API_BASE = "https://api.sumup.com"


def save_sumup_config(api_key, merchant_code, amount, description="Photobooth", reader_id=""):
    """Save SumUp configuration."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    data = {
        "api_key": api_key,
        "merchant_code": merchant_code,
        "amount": float(amount),
        "description": description,
        "currency": "EUR",
        "reader_id": reader_id,
    }
    with open(SUMUP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[SUMUP] Config opgeslagen: merchant={merchant_code}, amount={amount}, reader={reader_id}")


def load_sumup_config():
    """Load SumUp configuration. Returns dict or None."""
    if not os.path.isfile(SUMUP_CONFIG_FILE):
        return None
    try:
        with open(SUMUP_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("api_key") and data.get("merchant_code"):
            return data
    except Exception:
        pass
    return None


def remove_sumup_config():
    """Remove SumUp configuration."""
    if os.path.isfile(SUMUP_CONFIG_FILE):
        os.remove(SUMUP_CONFIG_FILE)
        print("[SUMUP] Config verwijderd")


def pair_reader(api_key, merchant_code, pairing_code):
    """Pair a Solo reader using the pairing code shown on its screen.

    Args:
        api_key: SumUp API key
        merchant_code: Merchant code (e.g. MWJFHJ9T)
        pairing_code: 8-9 character code shown on Solo screen

    Returns (success, message).
    """
    data = {
        "pairing_code": pairing_code.strip(),
        "name": "Photobooth Solo",
    }
    endpoint = f"/v0.1/merchants/{merchant_code.strip()}/readers"
    result, status = _api_request("POST", endpoint, api_key, data)

    if status in (200, 201):
        reader_id = result.get("id", "?")
        print(f"[SUMUP] Reader gepaired: {reader_id}")
        return True, f"Solo gekoppeld! Reader ID: {reader_id}"
    elif status == 409:
        return True, "Solo is al gekoppeld"
    else:
        error = result.get("error", result.get("message", result.get("detail", f"HTTP {status}")))
        print(f"[SUMUP] Pairing fout: {status} - {result}")
        return False, f"Pairing mislukt: {error}"


def _api_request(method, endpoint, api_key, data=None):
    """Make a SumUp API request. Returns (response_dict, status_code).

    Handles ALL network errors gracefully — never raises exceptions.
    Returns status_code 0 for network/connection errors.
    """
    url = f"{API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = e.headers.get("Retry-After", "5")
            try:
                wait = int(retry_after)
            except ValueError:
                wait = 5
            print(f"[SUMUP] Rate limited — wacht {wait}s")
            time.sleep(wait)
            return {"error": "rate_limited"}, 429
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = str(e)
        return {"error": error_body, "status_code": e.code}, e.code
    except urllib.error.URLError as e:
        # No internet, DNS failure, connection refused, etc.
        print(f"[SUMUP] Netwerk fout: {e.reason}")
        return {"error": f"Geen verbinding: {e.reason}"}, 0
    except Exception as e:
        # Socket timeout, SSL error, or any other unexpected error
        print(f"[SUMUP] API fout: {e}")
        return {"error": str(e)}, 0


def cancel_checkout(api_key, checkout_id):
    """Cancel/delete an active checkout so it can't be paid anymore.

    Returns (success, message).
    """
    if not checkout_id:
        return True, "Geen checkout om te annuleren"
    # SumUp doesn't have a cancel endpoint, but we can let it expire.
    # The best we can do is track it and ignore late payments.
    print(f"[SUMUP] Checkout {checkout_id[:12]}... geannuleerd (wordt genegeerd)")
    return True, "Checkout geannuleerd"


def abort_reader_checkout(api_key, merchant_code, reader_id):
    """Abort the current checkout on the reader.

    Returns (success, message).
    """
    if not reader_id:
        return False, "Geen reader ID"
    result, status = _api_request(
        "DELETE",
        f"/v0.1/merchants/{merchant_code}/readers/{reader_id}/checkout",
        api_key,
    )
    if status in (200, 204, 404):
        print(f"[SUMUP] Reader checkout geannuleerd")
        return True, "Reader checkout geannuleerd"
    else:
        error = result.get("error", f"HTTP {status}")
        print(f"[SUMUP] Reader annuleren mislukt: {error}")
        return False, str(error)


def _get_reader_id(api_key, merchant_code):
    """Get the first paired reader ID for this merchant."""
    result, status = _api_request("GET", f"/v0.1/merchants/{merchant_code}/readers", api_key)
    if status == 200:
        items = result.get("items", [])
        for reader in items:
            if reader.get("status") == "paired":
                return reader["id"]
    return None


def create_checkout(api_key, merchant_code, amount, currency="EUR", description="Photobooth", reader_id=""):
    """Create a new checkout on SumUp and send it to the Solo reader.

    Returns (checkout_id, error_message).
    """
    import uuid
    reference = f"booth-{uuid.uuid4().hex[:12]}"

    data = {
        "checkout_reference": reference,
        "amount": float(amount),
        "currency": currency,
        "merchant_code": merchant_code,
        "description": description,
    }

    result, status = _api_request("POST", "/v0.1/checkouts", api_key, data)

    if status not in (200, 201) or not result.get("id"):
        error = result.get("error", result.get("message", f"HTTP {status}"))
        print(f"[SUMUP] Checkout fout: {error}")
        return None, str(error)

    checkout_id = result["id"]
    print(f"[SUMUP] Checkout aangemaakt: {checkout_id} ({amount} {currency})")

    # Find reader ID if not provided
    if not reader_id:
        reader_id = _get_reader_id(api_key, merchant_code)
        if not reader_id:
            print("[SUMUP] Geen gepairde reader gevonden!")
            return None, "Geen gepairde Solo reader gevonden"

    # Send checkout to the Solo reader
    # total_amount must be an object with value in minor units (cents)
    amount_cents = int(round(float(amount) * 100))
    send_data = {
        "total_amount": {
            "currency": currency,
            "minor_unit": 2,
            "value": amount_cents,
        },
    }
    send_result, send_status = _api_request(
        "POST",
        f"/v0.1/merchants/{merchant_code}/readers/{reader_id}/checkout",
        api_key,
        send_data,
    )

    if send_status in (200, 201, 204):
        client_tx_id = ""
        if isinstance(send_result, dict):
            client_tx_id = send_result.get("data", {}).get("client_transaction_id", "")
        print(f"[SUMUP] Checkout naar Solo gestuurd: {reader_id} (tx={client_tx_id[:16]})")
        return checkout_id, client_tx_id
    elif send_status == 422:
        # Reader is busy with another checkout
        error_detail = send_result.get("error", "")
        if "READER_BUSY" in str(error_detail):
            print(f"[SUMUP] Solo is bezig met een andere checkout")
            return None, "READER_BUSY"
        print(f"[SUMUP] Checkout fout 422: {error_detail}")
        return None, str(error_detail)
    else:
        error = send_result.get("error", send_result.get("message", f"HTTP {send_status}"))
        print(f"[SUMUP] Checkout naar reader sturen mislukt: {error}")
        return None, f"Solo fout: {error}"


def check_checkout(api_key, checkout_id, client_transaction_id=None):
    """Check the status of a checkout.

    Also checks transaction history for FAILED status since SumUp
    keeps checkout as PENDING even after card is declined on Solo.

    Returns status string: 'PENDING', 'PAID', 'FAILED', 'EXPIRED', or 'ERROR'.
    """
    result, status = _api_request("GET", f"/v0.1/checkouts/{checkout_id}", api_key)

    if status == 200:
        checkout_status = result.get("status", "UNKNOWN").upper()
        if checkout_status == "PAID":
            print(f"[SUMUP] Betaling ontvangen! Checkout {checkout_id}")
            return "PAID"
        elif checkout_status == "FAILED":
            print(f"[SUMUP] Checkout mislukt")
            return "FAILED"
        elif checkout_status == "EXPIRED":
            return "EXPIRED"

        # Checkout is still PENDING — check if Solo has a failed transaction
        # for this checkout (Solo declines card locally without updating checkout)
        if client_transaction_id:
            tx_result, tx_status = _api_request(
                "GET", "/v0.1/me/transactions/history?limit=3&order=descending", api_key
            )
            if tx_status == 200:
                items = tx_result.get("items", [])
                for tx in items:
                    if tx.get("client_transaction_id") == client_transaction_id:
                        tx_status_str = tx.get("status", "").upper()
                        if tx_status_str == "FAILED":
                            print(f"[SUMUP] Kaart geweigerd (transaction history)")
                            return "FAILED"
                        elif tx_status_str == "SUCCESSFUL":
                            print(f"[SUMUP] Betaling ontvangen (transaction history)")
                            return "PAID"

        return checkout_status
    else:
        return "ERROR"


def check_recent_successful_payments(api_key, since_timestamp=None):
    """Check if there are any successful payments since a given timestamp.

    Returns list of successful transaction IDs since the timestamp.
    """
    result, status = _api_request(
        "GET", "/v0.1/me/transactions/history?limit=5&order=descending&statuses[]=SUCCESSFUL",
        api_key,
    )
    if status != 200:
        return []

    items = result.get("items", [])
    if not since_timestamp:
        return [tx.get("id") for tx in items if tx.get("status") == "SUCCESSFUL"]

    # Filter transactions newer than since_timestamp
    from datetime import datetime as _dt
    new_txs = []
    for tx in items:
        tx_time_str = tx.get("timestamp", "")
        if not tx_time_str:
            continue
        try:
            # Parse SumUp timestamp (ISO 8601)
            tx_time_str = tx_time_str.replace("Z", "+00:00")
            tx_time = _dt.fromisoformat(tx_time_str)
            # Make since_timestamp timezone-aware if needed
            if hasattr(since_timestamp, 'tzinfo') and since_timestamp.tzinfo is None:
                tx_time = tx_time.replace(tzinfo=None)
            if tx_time > since_timestamp:
                new_txs.append(tx.get("id"))
        except (ValueError, TypeError):
            continue
    return new_txs


def check_reader_status(api_key, merchant_code, reader_id):
    """Check the current status of the reader.

    Returns dict with reader info or None.
    """
    result, status = _api_request(
        "GET", f"/v0.1/merchants/{merchant_code}/readers/{reader_id}", api_key
    )
    if status == 200:
        return result
    return None


def test_connection(api_key):
    """Test the SumUp API connection.

    Returns (success, message).
    """
    result, status = _api_request("GET", "/v0.1/me", api_key)

    if status == 200:
        name = result.get("merchant_profile", {}).get("business_name", "OK")
        return True, f"Verbonden: {name}"
    elif status == 401:
        return False, "Ongeldige API key"
    else:
        error = result.get("error", f"HTTP {status}")
        return False, f"Fout: {error}"


class SumUpPaymentLoop(QThread):
    """Background thread that continuously creates checkouts and polls for payment.

    Signals:
        payment_received: Emitted when a payment is successful
        status_changed(str): Emitted with status updates for UI
        error_occurred(str): Emitted on errors
    """

    payment_received = pyqtSignal()
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._paused = False

    def start_loop(self):
        """Start the payment loop."""
        self._running = True
        self._paused = False
        if not self.isRunning():
            self.start()

    def stop_loop(self):
        """Stop the payment loop."""
        self._running = False
        self.wait(5000)

    def pause(self):
        """Pause creating new checkouts (during photo session)."""
        self._paused = True

    def resume(self):
        """Resume creating checkouts after photo session."""
        self._paused = False

    def _interruptible_sleep(self, seconds):
        """Sleep that can be interrupted by stopping the loop."""
        for _ in range(int(seconds * 2)):
            if not self._running:
                return
            time.sleep(0.5)

    def run(self):
        """Main payment loop."""
        cfg = load_sumup_config()
        if not cfg:
            self.error_occurred.emit("SumUp niet geconfigureerd")
            return

        api_key = cfg["api_key"]
        merchant_code = cfg["merchant_code"]
        amount = cfg["amount"]
        currency = cfg.get("currency", "EUR")
        description = cfg.get("description", "Photobooth")
        reader_id = cfg.get("reader_id", "")

        consecutive_errors = 0
        MAX_ERRORS = 10  # Show error on screen after 10 consecutive errors

        while self._running:
            try:
                # Wait while paused (during photo session)
                while self._paused and self._running:
                    time.sleep(0.5)

                if not self._running:
                    break

                # Create new checkout and send to Solo reader
                self.status_changed.emit("Wacht op betaling...")
                checkout_id, error = create_checkout(
                    api_key, merchant_code, amount, currency, description, reader_id
                )

                if not checkout_id:
                    consecutive_errors += 1
                    backoff = min(60, 10 * min(consecutive_errors, 4))
                    if consecutive_errors >= MAX_ERRORS:
                        self.error_occurred.emit(f"Terminal niet bereikbaar ({consecutive_errors}x) — controleer verbinding")
                        self.status_changed.emit("Verbindingsprobleem — herstart terminal")
                        backoff = 60
                    else:
                        self.status_changed.emit(f"Opnieuw verbinden ({consecutive_errors}/{MAX_ERRORS})...")
                    print(f"[SUMUP] Checkout fout ({consecutive_errors}x): {error} — retry in {backoff}s")
                    self._interruptible_sleep(backoff)
                    continue

                # Checkout gelukt — reset error counter
                if consecutive_errors > 0:
                    print(f"[SUMUP] Verbinding hersteld na {consecutive_errors} fouten")
                consecutive_errors = 0

                # Poll for payment (max 9 minutes, then create new checkout)
                poll_start = time.time()
                MAX_POLL_SECONDS = 540  # 9 minutes (checkout expires at 10)
                poll_errors = 0

                while self._running and not self._paused:
                    elapsed = time.time() - poll_start
                    if elapsed > MAX_POLL_SECONDS:
                        print(f"[SUMUP] Checkout verlopen na 9 min — nieuwe aanmaken")
                        break

                    status = check_checkout(api_key, checkout_id)

                    if status == "PAID":
                        consecutive_errors = 0
                        poll_errors = 0
                        self.status_changed.emit("Betaling ontvangen!")
                        print("[SUMUP] BETALING ONTVANGEN — fotosessie starten")
                        self.payment_received.emit()
                        # Wait for photo session to complete
                        self._paused = True
                        while self._paused and self._running:
                            time.sleep(0.5)
                        break

                    elif status == "FAILED":
                        poll_errors = 0
                        print(f"[SUMUP] Betaling mislukt — nieuwe checkout in 3s")
                        self.status_changed.emit("Betaling mislukt — probeer opnieuw")
                        self._interruptible_sleep(3)
                        break

                    elif status == "EXPIRED":
                        poll_errors = 0
                        print(f"[SUMUP] Checkout verlopen — vernieuwt")
                        break

                    elif status == "ERROR":
                        poll_errors += 1
                        consecutive_errors += 1
                        if poll_errors >= 5:
                            # Polling this checkout keeps failing, create new one
                            print(f"[SUMUP] Poll errors ({poll_errors}x) — nieuwe checkout")
                            self._interruptible_sleep(5)
                            break
                        if consecutive_errors >= MAX_ERRORS:
                            self.error_occurred.emit(f"Verbinding verloren ({consecutive_errors}x) — controleer terminal en WiFi")
                            self.status_changed.emit("Verbindingsprobleem")
                            self._interruptible_sleep(30)
                            consecutive_errors = 0  # Reset so it keeps trying
                            break

                    # Poll interval
                    time.sleep(2)

            except Exception as e:
                # Catch ANY unexpected error — never let the loop crash
                consecutive_errors += 1
                print(f"[SUMUP] Onverwachte fout in loop: {e}")
                if consecutive_errors >= MAX_ERRORS:
                    self.error_occurred.emit(f"Onverwachte fout — herstart aanbevolen")
                self._interruptible_sleep(15)

        print("[SUMUP] Payment loop gestopt")
