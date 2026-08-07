"""
Custom flow — verborgen geavanceerde betaalmodus voor MyBoothBox Photobooth.

Deze module bevat alleen de unlock-helper. Alle UI en flow-logica zit in
photobooth.py / event_model.py / voucher.py. Hier slechts één ding centraal:
de wachtwoord-validatie voor het ontgrendelen van de custom flow.

De code is een gedeeld geheim tussen ontwikkelaar en oprichter. Niet hardcoden
op meerdere plekken zodat we 'm hier kunnen aanpassen zonder elders te zoeken.
"""

from __future__ import annotations

# Letterlijk wachtwoord. Case-sensitive, exact zoals door eigenaar gekozen.
UNLOCK_CODE = "O'Learys"


def is_valid_unlock_code(code: str) -> bool:
    """Check of een ingevoerde code de custom-flow ontgrendelt.

    Whitespace aan begin/eind wordt gestript, maar hoofdletter/leesteken
    moeten exact kloppen. Lege input is altijd ongeldig.
    """
    if not code:
        return False
    return code.strip() == UNLOCK_CODE
