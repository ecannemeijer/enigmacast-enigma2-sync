# EnigmaCast Sync Server

De Sync Server draait op de Enigma2-ontvanger. De EnigmaCast-app bewaart daar profielen, favorieten en kijkvoortgang. Daarnaast is er een aparte webpagina om de server te bekijken en de Sync API te starten of te stoppen.

De installatie wijzigt geen zenders, bouquets, tuners, OSCam of streaminginstellingen.

## Twee diensten

| Dienst | Poort | Startscript |
| --- | --- | --- |
| Sync API | 8740 | `/etc/init.d/enigmacast-sync` |
| Webpagina | 8766 | `/etc/init.d/enigmacast-web` |

De webpagina blijft bereikbaar als de Sync API gestopt is. De Sync API blijft werken als de webpagina gestopt is.

Gegevens staan op de eerste schrijfbare schijf, in deze volgorde: `/media/hdd`, `/media/usb`, `/media/mmc`, `/media/cf`. De map heet `enigmacast-sync`. Is geen van die schijven schrijfbaar, dan starten de diensten niet. Ze schrijven niet naar het interne flashgeheugen.

Beide processen draaien als de systeemgebruiker `enigmacast`, niet als root.

## Wat er gesynchroniseerd wordt

Elke EnigmaCast-app is een apparaat met een eigen sleutel. Het eerste gekoppelde apparaat is de beheerder. Er is steeds één beheerder. Latere apparaten zijn clients en zien alleen de profielen die de beheerder toewijst.

Een profiel bevat de favorieten en de kijkvoortgang van films en series. De app stuurt de profielnamen mee zodra ze gekoppeld is, en daarna bij elke wijziging. De webpagina toont die namen. Heeft een profiel al favorieten of voortgang, maar nog geen naam op de ontvanger, dan staat het er met zijn uuid. De app stuurt wijzigingen naar de ontvanger. Een oudere wijziging wordt geweigerd. Kijkgeschiedenis, app-versie en een wachtrij worden niet bewaard.

De webpagina gebruikt dit wachtwoord niet voor de app. De app koppelt met een koppelcode. Die code en de herstelcode ontstaan bij de eerste start. Bewaar de herstelcode buiten de ontvanger. Daarmee kun je alle apparaten intrekken en één nieuwe beheerder maken als de huidige beheerder weg is.

## Zelf installeren

Pak het IPK-bestand van de GitHub-release. De bestandsnaam eindigt op `_all.ipk`.

https://github.com/ecannemeijer/enigmacast-enigma2-sync/releases/latest

Kopieer het bestand naar de ontvanger, bijvoorbeeld naar `/tmp/enigmacast-sync.ipk`, en installeer het:

```sh
opkg install --force-reinstall --force-overwrite /tmp/enigmacast-sync.ipk
```

Het pakket zet de plugin, de twee startscripts en een OpenWebif-menuitem. Daarna start het beide diensten. Een upgrade laat de database, het certificaat, de koppeling en het dashboardwachtwoord staan.

Tijdens installeren of verwijderen kan `chown` de melding `Operation not permitted` geven. Dat gebeurt op schijven die geen Unix-eigenaar bewaren, zoals veel NTFS-schijven. De installatie gaat daarna gewoon door.

Controleren:

```sh
wget -qO- --no-check-certificate https://127.0.0.1:8740/api/v1/health
```

Een gezonde Sync API antwoordt met `"ok": true`.

## Webpagina

Open in een browser op hetzelfde netwerk:

```text
https://192.168.1.209:8766/
```

Vervang het adres door het IP-adres van de ontvanger. De browser waarschuwt voor het certificaat, omdat de ontvanger dat zelf aanmaakt. Ga verder naar de site. De pagina is alleen bereikbaar vanaf een lokaal of privé-adres, niet vanaf internet.

In OpenWebif staat onder Extras de regel **EnigmaCast Sync Server**. Die opent dezelfde pagina. Verschijnt de regel niet, herstart dan de GUI van de ontvanger. Dat is geen fabrieksreset.

De pagina heeft vijf onderdelen:

- **Overzicht**: of de API en de webpagina draaien, versie, uptime, CPU, geheugen, schijf, database, aantal profielen en apparaten, laatste synchronisatie.
- **Serverbeheer**: Sync API starten, stoppen of herstarten, logs, een databaseback-up, en of er een nieuwere release is.
- **Profielen**: naam, uuid, favorieten, VOD-voortgang, series en afleveringen, laatste synchronisatie.
- **Apparaten**: naam, beheerder of client, laatste synchronisatie, of de koppeling nog geldt.
- **Synchronisatie**: toegepaste wijzigingen. Geweigerde conflicten worden niet bewaard.

De sessie blijft acht uur geldig.

## Wachtwoord

Bij de eerste start is het wachtwoord `admin`. Het staat dan ook in:

```text
/media/hdd/enigmacast-sync/web-password-once
```

Gebruik de map die op jouw ontvanger bestaat als de gegevens niet op `/media/hdd` staan.

```sh
cat /media/hdd/enigmacast-sync/web-password-once
```

De EnigmaCast-wizard leest dit bestand en verwijdert het daarna. Blijft het bestand staan, dan is de wizard niet gebruikt of heeft die het bestand niet gevonden. Het wachtwoord zelf staat daarna alleen nog als hash in `web-auth.json`. Een ontvanger die al een wachtwoord had, houdt dat wachtwoord. `admin` geldt alleen zolang `web-auth.json` nog niet bestond.

Op de webpagina, onder **Serverbeheer**, vul je het huidige wachtwoord en twee keer het nieuwe in. Kies minstens 4 tekens. Daarna moet iedereen opnieuw inloggen.

### Resetten

Dit zet het wachtwoord terug op `admin`. De database, de profielen en het certificaat blijven staan.

```sh
/etc/init.d/enigmacast-web stop
rm -f /media/hdd/enigmacast-sync/web-auth.json \
      /media/hdd/enigmacast-sync/web-password-once \
      /media/hdd/enigmacast-sync/web.secret
/etc/init.d/enigmacast-web start
cat /media/hdd/enigmacast-sync/web-password-once
```

`web.secret` hoort bij de browsersessie. Door dat bestand ook te verwijderen moet iedereen opnieuw inloggen. Zonder die regel blijft een al geopende sessie nog geldig, ook al is het wachtwoord nieuw.

### Wijzigen

Log in op de webpagina en open **Serverbeheer**. Vul het huidige wachtwoord in en twee keer het nieuwe. Minstens 4 tekens. De pagina meldt het als het gelukt is. Andere browsers moeten opnieuw inloggen.

## Diensten

```sh
/etc/init.d/enigmacast-sync start
/etc/init.d/enigmacast-sync stop
/etc/init.d/enigmacast-sync restart
/etc/init.d/enigmacast-web start
/etc/init.d/enigmacast-web stop
/etc/init.d/enigmacast-web restart
```

Logs staan in `/media/hdd/enigmacast-sync/logs/sync.log` en `web.log`. Regels met een wachtwoord, token of koppelcode worden in de webpagina weggelaten.

Is poort 8766 al in gebruik, dan start de webpagina niet en kiest ze geen andere poort. De melding staat in `web.log`.

## Verwijderen

```sh
opkg remove enigmacast-sync
```

De plugin, de startscripts en het OpenWebif-item gaan weg. De map `/media/hdd/enigmacast-sync` blijft, inclusief database en certificaat. `chown`-meldingen tijdens het verwijderen zijn hetzelfde schijfgedrag als bij de installatie. Het pakket is weg als `opkg status enigmacast-sync` geen pakket meer toont.

Voor een lege eerste installatie, met een nieuw wachtwoord en een nieuw certificaat:

```sh
opkg remove enigmacast-sync
rm -rf /media/hdd/enigmacast-sync
```

De app moet daarna opnieuw koppelen. De certificaatpin verandert.
