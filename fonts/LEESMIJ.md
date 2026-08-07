# De merkletters

In deze map horen de lettertypebestanden van MyBoothBox. Ze gaan mee in de build
(zie `bootharoo.spec`) en worden bij het opstarten ingelezen door `lettertype.py`.
Er wordt niets op Windows geïnstalleerd — geen beheerdersrechten, geen handmatige
stap per tablet.

**Staat deze map leeg, dan werkt de software gewoon**, maar valt hij terug op Segoe
UI: de letter van Windows zelf. Dan ziet de bediening er weer generiek uit, en dat
is precies wat we hiermee oplossen.

## Welke bestanden hier horen

| Bestand | Waarvoor |
|---|---|
| `DMSans-Regular.ttf` | lopende tekst |
| `DMSans-Medium.ttf` | knoppen en labels |
| `DMSans-Bold.ttf` | nadruk |
| `PlusJakartaSans-Bold.ttf` | koppen |
| `PlusJakartaSans-ExtraBold.ttf` | de grote kop op een gastscherm |
| `OFL.txt` | de licentie — hoort mee te gaan |

## Twee dingen die misgaan als je ze niet weet

**Geen WOFF2.** Qt leest TTF en OTF, en verder niets. De bestanden uit het
webproject van MyBoothBox (`@fontsource-variable/...`) zijn WOFF2 en dus niet
bruikbaar. Er moeten echte TTF's in.

**Geen variabel lettertype.** Van een variabel bestand — één bestand voor alle
diktes — laadt Qt5 alleen de standaarddikte. Vet wordt dan door de computer
nagemaakt en dat ziet er slecht uit. Lever daarom de vaste snedes los aan, zoals in
de tabel hierboven.

## De licentie

DM Sans en Plus Jakarta Sans staan allebei onder de **SIL Open Font License 1.1**.
Die staat uitdrukkelijk toe om een lettertype met software mee te leveren, op twee
voorwaarden: het licentiebestand gaat mee, en het lettertype wordt niet los
verkocht. Aan allebei is hier vanzelf voldaan.

Zet de licentietekst als `OFL.txt` in deze map. Eén bestand volstaat voor allebei
als je de twee copyrightregels bovenaan zet; anders `OFL-DMSans.txt` en
`OFL-PlusJakartaSans.txt`.

## Controleren of het gelukt is

Start de software en kijk in het logboek. Er hoort te staan:

```
[LETTER] 5 bestand(en) geladen uit ...\fonts: DM Sans, Plus Jakarta Sans
[LETTER] in gebruik — lopend: DM Sans, koppen: Plus Jakarta Sans
```

Staat er `LET OP: DM Sans ontbreekt — teruggevallen op Segoe UI`, dan zijn de
bestanden niet gevonden of niet leesbaar.
