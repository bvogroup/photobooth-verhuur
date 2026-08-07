# De merkletters

Hier staan de lettertypen van MyBoothBox. Ze gaan mee in de build (zie
`bootharoo.spec`) en worden bij het opstarten ingelezen door `lettertype.py`. Er wordt
niets op Windows geïnstalleerd — geen beheerdersrechten, geen handmatige stap per tablet.

**Staat deze map leeg, dan werkt de software gewoon**, maar valt hij terug op Segoe UI:
de letter van Windows zelf. Dan ziet de bediening er weer generiek uit. Dat vangnet is er
met opzet: een photobooth op een feest moet altijd opkomen, ook als er iets mis is met
een lettertype.

## Wat er staat

| Bestand | Familie | Snede |
|---|---|---|
| `DMSans-Regular.ttf` | DM Sans | Regular |
| `DMSans-Bold.ttf` | DM Sans | Bold |
| `PlusJakartaSans-Regular.ttf` | Plus Jakarta Sans | Regular |
| `PlusJakartaSans-Bold.ttf` | Plus Jakarta Sans | Bold |
| `OFL-DMSans.txt` | de licentie | |
| `OFL-PlusJakartaSans.txt` | de licentie | |

Samen ruim 400 kB. Opgehaald uit de bronrepositories van de makers:
`googlefonts/dm-fonts` en `tokotype/PlusJakartaSans`.

## Drie dingen die misgaan als je ze niet weet

**Geen WOFF2.** Qt leest TTF en OTF, en verder niets. De bestanden uit het webproject van
MyBoothBox (`@fontsource-variable/...`) zijn WOFF2 en dus niet bruikbaar.

**Geen variabel lettertype.** Van een variabel bestand — één bestand voor alle diktes —
laadt Qt5 alleen de standaarddikte. Vet wordt dan door de computer nagemaakt en dat ziet
er slecht uit. Vandaar vaste snedes.

**Alleen Regular en Bold, en dat is geen bezuiniging.** Dit is nagemeten in de
naamtabellen van de bestanden zelf. Een lettertypebestand draagt twee soorten
familienaam, en Qt5 kijkt naar de eerste:

| Bestand | Familie volgens Qt (nameID 1) | Snede |
|---|---|---|
| `DMSans-Regular.ttf` | `DM Sans` | Regular |
| `DMSans-Bold.ttf` | `DM Sans` | Bold |
| ~~`DMSans-Medium.ttf`~~ | **`DM Sans Medium`** | Regular |
| ~~`PlusJakartaSans-ExtraBold.ttf`~~ | **`Plus Jakarta Sans ExtraBold`** | Regular |

Medium en ExtraBold melden zich dus aan als een **eigen familie**, niet als een dikte
binnen de familie. `QFont("DM Sans")` komt daar nooit bij uit; je zou letterlijk
`QFont("DM Sans Medium")` moeten vragen. Ze zijn daarom weggelaten: `merk.letter()` werkt
met Regular en Bold, en die twee zitten wél netjes in één familie.

Wil je later toch een Medium, vraag hem dan op zijn eigen naam aan — en zet dat hier
erbij, anders zoekt de volgende persoon zich suf.

## De licentie

DM Sans en Plus Jakarta Sans staan allebei onder de **SIL Open Font License 1.1**. Die
staat uitdrukkelijk toe om een lettertype met software mee te leveren, op twee
voorwaarden: het licentiebestand gaat mee, en het lettertype wordt niet los verkocht. Aan
allebei is hier voldaan — vandaar de twee `OFL-*.txt` in deze map, die ook meegaan in de
build.

## Controleren of het werkt

`test_lettertype.py` doet dat, en draait mee in de bouwstraat. Hij toetst drie dingen: de
twee families worden echt geladen met hun beide snedes, een lege map valt terug op Segoe
UI, en een stukgemaakt bestand wordt overgeslagen zonder de boel te laten vallen.

Op de booth zelf kun je het in het logboek zien:

```
[LETTER] 4 bestand(en) geladen uit ...\fonts: DM Sans, Plus Jakarta Sans
[LETTER] in gebruik — lopend: DM Sans, koppen: Plus Jakarta Sans
```

Staat er `LET OP: DM Sans ontbreekt — teruggevallen op Segoe UI`, dan zijn de bestanden
niet gevonden of niet leesbaar.
