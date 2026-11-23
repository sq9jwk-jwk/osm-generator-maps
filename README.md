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
   Opcjonalnie powstaje też indeks wyszukiwania adresów (ulice, kody pocztowe).
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
rm -f build/*.deb          # stare paczki w build/ psują glob poniżej
make deb
sudo apt install -y --reinstall ./build/osm-tilegen_*_$(dpkg --print-architecture).deb
```

`--reinstall` działa także wtedy, gdy numer wersji się nie zmienił. Komunikat
`Download is performed unsandboxed as root ... _apt` jest nieszkodliwy
(plik leży w katalogu roota).

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
  zwykły `apt install` uzna pakiet za już zainstalowany. Użyj
  `apt install --reinstall` (jak wyżej) albo podnieś wersję przed `make deb`.
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

## Funkcje

**Biblioteka map i eksporty**
- Upload `.osm.pbf` porcjami (8 MiB) z wznawianiem, walidacja i kontrola miejsca.
- Model *źródło → eksporty*: z jednego wgranego pliku powstaje dowolnie wiele
  eksportów (własny profil, moduły i obszar). Eksporty są równorzędne, można je
  usuwać osobno. Nieudany eksport zostaje z logiem, aż go usuniesz.
- Obszar eksportu: cały plik albo kilka prostokątów (nakładające się łączą się,
  PBF jest wycinany `osmium extract`, więc szlaki w okolicy zostają kompletne).
- **Próbka** przed importem: mały fragment (ok. 10×10 km) zaimportowany wybranym
  profilem, bez tworzenia eksportu.
- Okno „Zarządzaj mapami”: rozmiary, zawartość, usuwanie plików źródłowych,
  eksportów i wyników (wyniki nigdy nie znikają bez zgody).

**Profile danych (tilemaker)**
- `classic` (domyślny): drogi z obwódkami, mosty/tunele, koleje, wody, użytki,
  etykiety, numery domów, ogólne POI.
- `tourist`: dodatkowo szlaki turystyczne, szczyty, schroniska, źródła, punkty
  widokowe, ścieżki wg `sac_scale`, obszary chronione.
- `full`: oficjalny profil OpenMapTiles (wymagany przez style Bright, Positron,
  Dark Matter, Liberty).
- `custom`: własny zestaw modułów wybieranych checkboxami.

**Podgląd i style** (MapLibre GL JS, w pełni offline, fonty i sprite'y w pakiecie)
- Style: Klasyczny, Turystyczny, Podstawowy (bez etykiet), OSM Bright, Positron,
  Dark Matter, OSM Liberty. Lista pokazuje tylko style zgodne z profilem eksportu.
- Obrys obszaru eksportu na mapie i atrybucja „© OpenStreetMap contributors”.

**Wyszukiwarka adresów (offline)**
- Pole na mapie z podpowiedziami: ulice, adresy z numerem, kody pocztowe,
  miejscowości i miejsca (szczyty, sklepy, atrakcje).
- Działa bez polskich znaków (`zolkiewskiego` → „Żółkiewskiego”), kod pocztowy z
  myślnikiem i bez, nawigacja strzałkami, Enter, Escape.
- Indeks (`search.sqlite`, SQLite FTS5) powstaje razem z eksportem (checkbox w
  oknie eksportu) albo później przyciskiem „Zbuduj indeks wyszukiwania”.
  Dla Małopolski (200 MB): ok. 80 s, 270 MB, szczyt RAM ok. 2,3 GB; na Raspberry Pi
  przy dużych plikach lepiej go odznaczyć.

**Siatka i generowanie kafelków**
- Siatka zaznaczania XYZ na dowolnym poziomie ≤ zoom wyjściowy: klik,
  Shift+przeciągnij (zaznacz prostokąt), Alt+przeciągnij (odznacz), zachowanie
  zaznaczenia przy zmianie poziomu siatki, liczniki i szacowany rozmiar.
  Kafelki poza danymi są niedostępne (maska pokrycia).
- Parametry: zoom lub zakres zoomów, kafelek 128/256/512 px, format
  PNG / PNG-8 / JPG (jakość), numeracja XYZ lub TMS.
- Render w tle z postępem, anulowaniem i wznawianiem po restarcie (pomija gotowe
  kafelki). Wynik: `z/x/y.ext` + `metadata.json`, historia, pobieranie jako ZIP
  (strumieniowo).
- Zaznaczenia można zapisywać i nazywać (API gotowe, UI w toku).

**System**
- Pakiet `.deb` (amd64/arm64), dwie usługi systemd (API+UI i worker), konfiguracja
  w `/etc/osm-tilegen/config.toml`, profil oszczędny dla Raspberry Pi.
- Całość działa bez internetu (brak CDN i zewnętrznych adresów).

## Stan i ograniczenia

- Kafelki rysuje na razie silnik `debug` (ramka + napis z/x/y). **Prawdziwy
  renderer (MapLibre Native, spike M0.5) jest największym brakującym elementem.**
  Podgląd w przeglądarce i wyszukiwarka działają już na prawdziwych danych.
- Brak poligonów mórz (morze renderuje się jak ląd) i warstnic (wymagają DEM).
- Profile `tourist`, `custom` i rozszerzone `classic` nie były testowane na wielkich
  plikach na Raspberry Pi.

Pełna lista zadań: CLAUDE.md, sekcja 12 „Stan projektu”.

Dane mapy © OpenStreetMap contributors, licencja ODbL.
