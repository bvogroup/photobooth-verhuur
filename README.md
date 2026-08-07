# photobooth-verhuur — de installers

Deze repo bevat **geen broncode meer**. Hij bestaat nog om één reden: dit is het
adres waar de photobooths hun updates ophalen.

In `updater.py` van de boothsoftware staat
`api.github.com/repos/bvogroup/photobooth-verhuur/releases` hardgecodeerd, en dat
staat op elk apparaat in het veld. Zou deze repo verdwijnen of privé worden, dan
kan geen enkele booth nog updaten — ook niet naar een versie die naar een ander
adres wijst. **Dit adres verandert dus nooit.**

## Waar de broncode staat

`bvogroup/photobooth-broncode` (privé). Daar wordt ontwikkeld, daar staan alle
takken en tags.

## Bouwen

De bouwstraat hier haalt de broncode op uit de privé-repo met een alleen-lezen
deploy key (geheim `BRONCODE_SLEUTEL`):

```
gh workflow run build-installer.yml --repo bvogroup/photobooth-verhuur \
  --ref main -f bron_ref=<tak-of-tag-in-de-broncode>
```

Er wordt nooit vanzelf een release gepubliceerd — dat blijft een menselijke
keuze, omdat publiceren de booths bij de eerstvolgende overdracht laat bijwerken.

## Een release uitbrengen

1. bouwen zoals hierboven, en controleren dat de run groen is
2. concept-release aanmaken in **deze** repo
3. de tag naar **beide** repo's pushen — de bouwstraat hangt de installer dan aan
   de release
4. de release publiceren

## Let op

De git-geschiedenis van deze repo bevat de oude broncode, inclusief sleutels die
daarin hebben gestaan. Het weghalen van de bestanden verandert daar niets aan.
