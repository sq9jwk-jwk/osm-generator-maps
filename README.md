# osm-tilegen

Generator kafelków map OSM (PNG/JPG, `z/x/y`) do map offline na kartach SD i
dyskach. Usługa systemowa na Debianie 13 (amd64/arm64, także Raspberry Pi) z
interfejsem webowym. Działa bez internetu.

Plan, decyzje i konwencje: **[CLAUDE.md](CLAUDE.md)**.

## Jak to działa (w skrócie)

1. Wgrywasz plik `.osm.pbf` (np. z Geofabrik) w przeglądarce. Upload jest
   porcjowany i wznawialny.
2. **Import** (zadanie w tle): `osmium` odczytuje obszar pliku, `tilemaker`
   zamienia dane na kafelki wektorowe (MBTiles), a aplikacja liczy maskę
   pokrycia.
3. **Podgląd:** MapLibre GL JS rysuje mapę w przeglądarce z lokalnych kafelków
   wektorowych. Style (widoki), fonty i ikony są w pakiecie.
4. Wybierasz styl, zoom, rozmiar kafelka (128/256/512 px), format
   (PNG/PNG-8/JPG) i numerację (XYZ/TMS), po czym zaznaczasz kwadraty siatki.
5. **Render** (zadanie w tle, z postępem, anulowaniem i wznawianiem) zapisuje
   kafelki do `/var/lib/osm-tilegen/output/<zadanie>/z/x/y.png` wraz z
   `metadata.json`. Wynik można pobrać jako ZIP.

Dwie usługi systemd: `osm-tilegen` (API + UI, port 8080) i
`osm-tilegen-worker` (import i render). Dane leżą w `/var/lib/osm-tilegen/`.

> **Uwaga:** brak logowania. Uruchamiaj tylko w zaufanej sieci lokalnej.

## Instalacja (Debian 13, amd64 lub arm64)

Pakiet `.deb` buduje się na maszynie o tej samej architekturze co docelowa
(PC → amd64, Raspberry Pi → arm64).

```bash
# 1. Narzędzia do budowania
sudo apt update
sudo apt install -y git make python3 python3-venv nodejs npm dpkg-dev

# 2. Pobranie kodu
git clone https://github.com/sq9jwk-jwk/osm-generator-maps.git osm-tilegen
cd osm-tilegen

# 3. Zbudowanie pakietu (UI + .deb) -> build/osm-tilegen_<wersja>_<arch>.deb
make deb

# 4. Instalacja (apt dociągnie zależności: fastapi, uvicorn, pillow,
#    osmium-tool, tilemaker)
sudo apt install -y ./build/osm-tilegen_*_$(dpkg --print-architecture).deb
```

Po instalacji usługi startują automatycznie. Otwórz w przeglądarce:
`http://<adres-maszyny>:8080` (lokalnie: `http://localhost:8080`).

Sprawdzenie stanu:

```bash
systemctl status osm-tilegen osm-tilegen-worker
journalctl -u osm-tilegen -u osm-tilegen-worker -f
```

Konfiguracja: `/etc/osm-tilegen/config.toml` (port, wątki renderu,
`store_on_disk = true` dla Raspberry Pi z małą ilością RAM). Po zmianie:

```bash
sudo systemctl restart osm-tilegen osm-tilegen-worker
```

## Aktualizacja

```bash
cd osm-tilegen
git pull
make deb
sudo apt install -y ./build/osm-tilegen_*_$(dpkg --print-architecture).deb
```

Podniesienie wersji przed budową (żeby `apt` na pewno zainstalował nowy pakiet):

```bash
grep __version__ backend/app/__init__.py                       # aktualna wersja
sed -i 's/^__version__ = ".*"/__version__ = "0.1.3"/' backend/app/__init__.py
```

Szybka aktualizacja samych stylów lub profili importu (bez budowania `.deb`),
np. po `git pull` zmieniającym tylko `styles/` albo `tilemaker/`:

```bash
cd osm-tilegen && git pull
sudo cp -r styles/. /usr/share/osm-tilegen/styles/
sudo cp -r tilemaker/. /usr/share/osm-tilegen/tilemaker/
sudo systemctl restart osm-tilegen osm-tilegen-worker
```

Zmiany w `styles/` widać od razu po odświeżeniu przeglądarki (Ctrl+F5). Zmiany
w `tilemaker/` (nowe warstwy danych, np. POI, numery domów) dotyczą dopiero
**nowych eksportów**: utwórz nowy eksport przyciskiem „+ Eksport” (stare eksporty zostają bez zmian).

Uwagi:
- Jeśli zmieniła się tylko treść, a numer wersji nie (`backend/app/__init__.py`),
  `apt` może uznać pakiet za już zainstalowany. Wtedy użyj:
  `sudo dpkg -i build/osm-tilegen_*_$(dpkg --print-architecture).deb`
  albo podnieś wersję przed `make deb`.
- Aktualizacja zachowuje mapy, wyniki (`/var/lib/osm-tilegen`) i
  `/etc/osm-tilegen/config.toml`. Usługi są zatrzymywane i uruchamiane
  ponownie automatycznie.
- Jeśli pojawił się nowy profil importu lub zmienił się `tilemaker/`, po
  aktualizacji utwórz nowy eksport.

## Odinstalowanie

```bash
sudo apt remove osm-tilegen          # usuwa program, zostawia konfigurację i dane
sudo apt purge osm-tilegen           # usuwa też /etc/osm-tilegen
sudo rm -rf /var/lib/osm-tilegen     # ręcznie: mapy i wygenerowane kafelki (nieodwracalne)
```

## Development (Debian 13)

```bash
make deps-system            # sudo apt: python3-fastapi, uvicorn, pillow, osmium-tool, tilemaker, nodejs, npm
make venv                   # .venv z dostępem do pakietów systemowych + narzędzia dev
make frontend               # zbudowanie UI
./scripts/get-test-pbf.sh   # mały plik testowy (Monako)
```

W trzech terminalach (albo VS Code → Run and Debug → „API + Worker”):

```bash
make dev-api          # http://localhost:8080 (API + zbudowane UI, /docs = dokumentacja API)
make dev-worker       # import i renderowanie w tle
make dev-frontend     # opcjonalnie: UI z hot-reload na http://localhost:5173
```

Testy: `make test`.

## Stan

Działa:
- upload `.osm.pbf` porcjami z wznawianiem, biblioteka map, usuwanie,
- import: bbox (osmium) → kafelki wektorowe (tilemaker) → maska pokrycia,
- podgląd w kilku stylach (MapLibre GL JS, offline), obrys pliku,
- siatka zaznaczania XYZ: klik, Shift+przeciągnij, Alt+przeciągnij, przenoszenie
  zaznaczenia między poziomami siatki, liczniki i szacowany rozmiar,
- zadania render z postępem, anulowaniem i wznawianiem; PNG / PNG-8 / JPG,
  128/256/512 px, XYZ/TMS, `metadata.json`, pobieranie ZIP (strumieniowo),
- pakiet `.deb` + usługi systemd.

Do zrobienia: patrz CLAUDE.md, sekcja 12 „Stan projektu”. Najważniejsze to
**prawdziwy renderer (spike M0.5)**. Teraz kafelki rysuje silnik `debug`
(ramka + napis z/x/y).

Dane mapy © OpenStreetMap contributors, licencja ODbL.
