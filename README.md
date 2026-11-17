# osm-tilegen

Generator kafelków map OSM (PNG/JPG, `z/x/y`) do map offline na kartach SD i
dyskach. Usługa systemowa na Debianie 13 (amd64/arm64, także Raspberry Pi) z
interfejsem webowym. Działa bez internetu.

Plan, decyzje i konwencje: **[CLAUDE.md](CLAUDE.md)**.

## Szybki start (development na Debianie 13)

```bash
make deps-system      # sudo apt: python3-fastapi, uvicorn, pillow, osmium-tool, tilemaker, nodejs, npm
make venv             # .venv z dostępem do pakietów systemowych + narzędzia dev
make frontend         # zbudowanie UI
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
- podgląd w stylu „Podstawowy” (MapLibre GL JS, offline), obrys pliku,
- siatka zaznaczania XYZ: klik, Shift+przeciągnij, Alt+przeciągnij, przenoszenie
  zaznaczenia między poziomami siatki, liczniki i szacowany rozmiar,
- zadania render z postępem, anulowaniem i wznawianiem; PNG / PNG-8 / JPG,
  128/256/512 px, XYZ/TMS, `metadata.json`, pobieranie ZIP (strumieniowo),
- pakiet `.deb` + usługi systemd.

Do zrobienia: patrz CLAUDE.md, sekcja 12 „Stan projektu”. Najważniejsze to
**prawdziwy renderer (spike M0.5)**. Teraz kafelki rysuje silnik `debug`
(ramka + napis z/x/y).

## Instalacja pakietu

```bash
make deb                              # na maszynie docelowej architektury
sudo apt install ./build/osm-tilegen_0.1.0_arm64.deb
# UI: http://<adres-rpi>:8080, konfiguracja: /etc/osm-tilegen/config.toml
```

Dane mapy © OpenStreetMap contributors, licencja ODbL.
