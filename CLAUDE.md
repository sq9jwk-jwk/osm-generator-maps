# CLAUDE.md — OSM Tile Renderer (roboczo: `osm-tilegen`)

> **Język projektu: rozmawiamy po polsku i odpowiadamy po polsku.**
> Kod, identyfikatory, komentarze w kodzie i commity po angielsku.

## 1. Cel projektu

Aplikacja dla **Debiana 13 (trixie)**, **amd64 i arm64** (zwykły PC oraz
Raspberry Pi 4/5 z 64-bitowym systemem). Działa w tle jako usługa systemowa i
przez interfejs webowy pozwala:

1. wgrać jeden lub wiele plików `.osm.pbf` (biblioteka map),
2. zobaczyć zarys obszaru, który obejmuje dany plik,
3. wybrać styl mapy (widok) i obejrzeć go w podglądzie,
4. wybrać docelowy poziom zoomu i rozmiar kafelka,
5. zaznaczyć klikaniem kwadraty siatki kafelków obszaru, który ma zostać
   wygenerowany,
6. wygenerować kafelki rastrowe (PNG/JPG) do użycia jako **mapa offline na
   karcie SD lub dysku** w różnych urządzeniach.

**Całość działa bez internetu.** Wszystkie zasoby (style, fonty, ikony,
biblioteki JS, polygony mórz) są w pakiecie. Podgląd jest renderowany
wyłącznie z wgranych plików.

## 2. Ustalenia (decyzje użytkownika)

| # | Temat | Decyzja |
|---|---|---|
| 1 | Format | **PNG domyślnie** (de facto standard kafelków OSM). Do wyboru JPG z regulowaną jakością. Opcjonalnie PNG 8-bit z paletą (mniejsze pliki na SD). |
| 2 | Zastosowanie | Mapa offline na SD lub dysku w różnych urządzeniach, więc struktura katalogów `z/x/y.ext`. |
| 3 | Widoki | Plik `.osm.pbf` zawiera **same dane** (punkty, linie, relacje z tagami) i nie zawiera stylów. „Widoki” to style dostarczane z aplikacją. Użytkownik wybiera styl i widzi go w podglądzie. |
| 4 | Zoom | Użytkownik ustawia poziom wyjściowy (opcjonalnie zakres min–max). Siatka zaznaczania służy do precyzyjnego określenia obszaru, żeby nie generować zbędnych kafelków. |
| 5 | Rozmiar kafelka | **256 px domyślnie** (konwencja OSM). Do wyboru 128 px i 512 px (retina). |
| 6 | Sprzęt | Raspberry Pi i zwykły PC z Debianem. |
| 7 | Internet | Brak. Działamy tylko na zaimportowanych plikach. |
| 8 | Mapy | Wiele map (biblioteka). |
| 9 | Dostęp | Jeden użytkownik, bez logowania. |
| 10 | Wyniki | Zostają na dysku serwera, z historią i możliwością pobrania. |
| 11 | System | Debian 13 trixie, amd64 i arm64. |

## 3. Kluczowe założenia techniczne

- **Siatka kafelków = siatka wyjściowa.** Schemat Slippy Map / XYZ (Web
  Mercator EPSG:3857, `z/x/y`). Kwadrat w UI na poziomie `z` to dokładnie
  kafelek `z/x/y`.
- **Rozmiar kafelka a siatka.** Przy 512 px jeden kafelek pokrywa ten sam
  obszar co kafelek 256 px na tym samym `z`, tylko w podwójnej rozdzielczości.
  Przy 128 px jest to połowa rozdzielczości. Siatka geograficzna się nie
  zmienia, zmienia się tylko skala renderu (pixel ratio 0.5 / 1 / 2).
- **Opcja numeracji Y: XYZ (domyślnie) lub TMS**, bo część urządzeń oczekuje
  TMS (odwrócone y).
- **Długie operacje (import, render) idą w tle** jako zadania z postępem i
  możliwością anulowania.

## 4. Silnik renderujący — decyzja

**Wariant główny (A): tilemaker → wektorowe MBTiles → raster przez MapLibre
Native.**

- `tilemaker` jest w oficjalnym repozytorium Debiana 13 (pakiet `tilemaker`
  3.0.0) dla amd64 i arm64. Nie wymaga bazy danych, więc nadaje się na RPi.
- Domyślnie generuje schemat **OpenMapTiles**, do którego istnieje wiele
  otwartych stylów MapLibre. To daje użytkownikowi kilka widoków z tych samych
  danych, np.:
  - **OSM Bright** (klasyczny, kolorowy),
  - **Positron** (jasny, minimalistyczny),
  - **Dark Matter** (ciemny),
  - **OSM Liberty** (bliski wyglądowi OSM),
  - własny styl „uproszczony / wysoki kontrast” (do małych wyświetlaczy).
  Licencje stylów do weryfikacji przed dołączeniem.
- Podgląd w przeglądarce: **MapLibre GL JS** renderuje bezpośrednio z
  wektorowych MBTiles serwowanych przez backend, więc przełączanie stylów jest
  natychmiastowe.
- Render do plików: **MapLibre Native** (headless).
  - **Ryzyko:** gotowe binaria `@maplibre/maplibre-gl-native` są budowane pod
    Ubuntu (amd64/arm64), a na Linuksie headless zwykle potrzebny jest `xvfb`
    lub EGL/OSMesa.
  - Alternatywa: wrapper Pythona `mlnative` (Linux x64/ARM64, Python 3.12+),
    a jeśli żadna z gotowych binarek nie zadziała, budowa `mbgl-render` ze
    źródeł i dołączenie do pakietu.
  - Do sprawdzenia w spike'u M0.5 na obu architekturach.
  - Kafelki renderujemy z marginesem (metatile, np. 2×2 lub 4×4), żeby
    etykiety nie były ucinane na krawędziach.

**Wariant zapasowy (B): osm2pgsql + PostGIS + Mapnik + openstreetmap-carto.**
Wygląd 1:1 z openstreetmap.org, ale wymaga bazy danych, dużo RAM i dysku oraz
długiego importu na RPi. Projektujemy interfejs `RenderEngine`, żeby B dało
się dodać później bez zmian w reszcie systemu.

### Morza i linia brzegowa

Pliki `.osm.pbf` nie zawierają gotowych poligonów mórz. Bez nich morze
renderuje się jak ląd. Rozwiązanie: dołączamy do pakietu (lub jako osobny
pakiet `osm-tilegen-data`) przycięte **water polygons** i **land polygons
dla niskich zoomów**, które tilemaker ładuje jako shapefile. Do ustalenia, czy
pakujemy cały świat (~1 GB), czy tylko Europę.

## 5. Architektura

```
┌──────────────────── przeglądarka ─────────────────────┐
│ SPA (Vite + TS): MapLibre GL JS, siatka kafelków,     │
│ upload, wybór stylu/zoomu/rozmiaru/formatu, zadania   │
└───────────────▲───────────────────────────────────────┘
                │ HTTP/JSON + SSE (postęp)
┌───────────────┴───────────────────────────────────────┐
│ Backend: Python 3.13 (Debian 13), FastAPI + uvicorn   │
│  api/     upload (porcjami), maps, styles, selections,│
│           jobs, outputs, serwowanie MBTiles do podglądu
│  jobs/    kolejka w SQLite + osobny proces worker     │
│  db       SQLite (SQLModel)                           │
└──────┬───────────────┬─────────────────┬──────────────┘
       │               │                 │
  ┌────▼─────┐   ┌─────▼──────┐   ┌──────▼─────────────┐
  │ pyosmium │   │ tilemaker  │   │ renderer (A):      │
  │ /osmium- │   │ pbf→MBTiles│   │ MapLibre Native    │
  │ tool     │   │ (wektor)   │   │ → PNG/JPG (Pillow) │
  └──────────┘   └────────────┘   └────────────────────┘
```

## 6. Przepływ użytkownika

1. **Biblioteka map:** lista wgranych map (nazwa, rozmiar, data, obszar,
   status importu) z możliwością usunięcia.
2. **Upload** `.osm.pbf`: porcjami i wznawialny (pliki krajów mają GB),
   walidacja nagłówka PBF, kontrola wolnego miejsca.
3. **Import (zadanie w tle):**
   - bbox z nagłówka (`osmium fileinfo`), a jeśli go brak, liczymy go z danych,
   - **maska pokrycia:** zbiór kafelków na z10–z12, w których są dane,
     zapisany jako GeoJSON (obrys + lista kafelków),
   - `tilemaker` tworzy wektorowe MBTiles (do podglądu i renderu).
4. **Podgląd:** mapa z obrysem obszaru. Kafelki poza danymi są wyszarzone i
   nie da się ich zaznaczyć. Przełącznik stylów zmienia podgląd na żywo.
5. **Parametry wyjścia:** zoom wyjściowy (lub zakres), rozmiar 128/256/512,
   format PNG / PNG-8 / JPG (jakość), numeracja XYZ/TMS.
6. **Zaznaczanie:**
   - poziom siatki zaznaczania jest wybierany niezależnie i wynosi co najwyżej
     tyle, co zoom wyjściowy (gruba siatka do szybkiego zaznaczania dużego
     obszaru, drobna do precyzji),
   - klik przełącza kwadrat, przeciągnięcie zaznacza prostokąt, Shift/prawy
     przycisk odznacza,
   - zmiana poziomu siatki zachowuje zaznaczenie: przy przejściu w górę kwadrat
     dzieli się na 4^Δz, przy przejściu w dół rodzic jest zaznaczony, jeśli
     zawiera zaznaczone dzieci (do ustalenia: całe czy częściowe),
   - na żywo widać liczbę kafelków wyjściowych, szacowany rozmiar na dysku i
     szacowany czas,
   - zaznaczenia można zapisać i nazwać, żeby użyć ich ponownie.
7. **Generowanie:** zadanie w tle, postęp przez SSE, anulowanie, wznowienie
   po restarcie usługi (pomija gotowe kafelki).
8. **Wyniki:** zostają w `/var/lib/osm-tilegen/output/<job>/z/x/y.ext` razem
   z `metadata.json` (bbox, zoomy, styl, rozmiar, format, źródło, licencja
   ODbL). Pobieranie jako ZIP (strumieniowo) i przeglądanie wyniku w UI.
   Opcjonalnie eksport do MBTiles (raster).

## 7. Struktura repozytorium

```
osm-tilegen/
├── CLAUDE.md
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/          # uploads, maps, styles, selections, jobs, outputs, preview
│   │   ├── core/         # config (TOML), ścieżki, logowanie
│   │   ├── tiles/        # matematyka XYZ/TMS, maski, operacje na zbiorach kafelków
│   │   ├── osm/          # analiza pbf, bbox, maska pokrycia
│   │   ├── importer/     # wywołanie tilemakera, konfiguracja, shapefile mórz
│   │   ├── render/       # RenderEngine (interfejs), maplibre_native.py, (mapnik.py)
│   │   ├── jobs/         # kolejka, worker, postęp, anulowanie, wznawianie
│   │   └── db.py
│   └── tests/
├── frontend/             # Vite + TypeScript + MapLibre GL JS (bez CDN)
├── styles/               # style MapLibre + sprite'y + glify (fonty PBF), lokalnie
├── data/                 # skrypty przygotowania water/land polygons
├── packaging/
│   ├── debian/           # control, rules, postinst, prerm, postrm, conffiles
│   ├── systemd/          # osm-tilegen.service, osm-tilegen-worker.service
│   └── config.toml.example
└── scripts/              # dev: make dev, pobranie małego pbf testowego
```

## 8. Instalacja i działanie w systemie

- Pakiety `.deb` budowane osobno dla **amd64 i arm64** (MapLibre Native jest
  natywny).
- Zależności wyłącznie z Debiana: `python3-fastapi`, `python3-uvicorn`,
  `python3-pil`, `tilemaker`, `osmium-tool`, oraz `xvfb` lub biblioteki EGL
  (zależnie od wyniku spike'u). **Bez venv i bez pip w pakiecie**, więc
  instalacja działa offline. Baza danych to stdlib `sqlite3` (bez ORM).
- Kod backendu w `/usr/lib/osm-tilegen/app`, UI w `/usr/share/osm-tilegen/ui`,
  style w `/usr/share/osm-tilegen/styles`. Użytkownik systemowy `osmtile`.
- API startuje przez `python3 -m app` (host i port z `config.toml`).
- Dane w `/var/lib/osm-tilegen/` (`uploads/`, `maps/`, `output/`,
  `db.sqlite`), konfiguracja w `/etc/osm-tilegen/config.toml`, logi w journald.
- systemd: `osm-tilegen.service` (API + UI) oraz
  `osm-tilegen-worker.service` (import/render), `Restart=on-failure`,
  `Nice=10`, `MemoryMax` z konfiguracji.
- Nasłuch domyślnie na `0.0.0.0:8080`, bez logowania (zgodnie z decyzją).
  Ostrzeżenie w README: tylko sieć lokalna.
- `apt purge` usuwa konfigurację. Dane map i wyniki usuwa tylko po
  potwierdzeniu (debconf).

### Profile wydajności

- **RPi:** worker z 1–2 wątkami renderu, tilemaker z `--store` na dysku
  (mniej RAM), ostrzeżenie w UI przy dużych plikach.
- **PC:** liczba wątków = liczba rdzeni, tilemaker w RAM.

## 9. Etapy

1. **M0 — Szkielet:** FastAPI + statyczny frontend, konfiguracja, SQLite,
   `make dev`, testy matematyki kafelków.
2. **M0.5 — Spike renderu (krytyczny):** na Debianie 13 amd64 i arm64 (RPi):
   tilemaker na małym pbf, potem render PNG 128/256/512 przez MapLibre Native
   headless. Wybór sposobu dostarczenia renderera. Pomiar czasu i RAM.
3. **M1 — Upload i import:** wznawialny upload, biblioteka map, bbox, maska
   pokrycia, tilemaker, obrys na mapie.
4. **M2 — Podgląd i style:** serwowanie MBTiles, lokalne style/fonty/sprite'y,
   przełącznik stylów.
5. **M3 — Siatka i zaznaczanie:** nakładka siatki, poziom siatki ≤ zoom
   wyjściowy, klik/drag, zapis zaznaczeń, liczniki i szacunki.
6. **M4 — Render i wyniki:** kolejka, metatile, PNG/PNG-8/JPG, XYZ/TMS,
   postęp, anulowanie, wznawianie, ZIP, historia wyników.
7. **M5 — Pakiety Debiana:** `.deb` amd64/arm64, systemd, postinst, test
   instalacji na czystym Debianie 13 i na RPi.
8. **M6 — Dodatki:** eksport raster MBTiles, scalanie map
   (`osmium merge`) dla obszarów na styku plików, własne style, sprzątanie
   dysku, wariant B (Mapnik).

## 10. Konwencje

- Python: `ruff` + `black`, typowanie (`mypy --strict` w `tiles/`, `osm/`),
  `pytest`. Matematyka kafelków w 100% pokryta testami.
- Frontend: TypeScript, wszystkie zależności lokalnie, żadnych CDN ani
  zewnętrznych URL-i. To jest test akceptacyjny: aplikacja działa z odłączonym
  kablem sieciowym.
- Nie ufamy nazwom plików z uploadu. Ścieżki zawsze bierzemy z konfiguracji.
- API nigdy nie blokuje się na długiej operacji, wszystko idzie przez kolejkę.
- Plik testowy: mały wycinek z Geofabrik (np. jedno województwo albo Monako),
  przechowywany lokalnie w `scripts/`.
- Atrybucja „© OpenStreetMap contributors” w podglądzie i w `metadata.json`.

## 11. Otwarte kwestie

- [ ] Wynik spike'u M0.5: sposób dostarczenia MapLibre Native (npm / mlnative
      / własna kompilacja) i tryb headless (xvfb vs EGL).
- [ ] Zakres poligonów mórz w pakiecie (świat / Europa / osobny pakiet).
- [ ] Zachowanie zaznaczenia przy przejściu na grubszą siatkę (całe vs
      częściowe pokrycie).
- [ ] Czy zoom wyjściowy to jeden poziom, czy zakres (domyślnie jeden poziom,
      zakres jako opcja).
- [ ] Czy można zaznaczać obszar obejmujący kilka map naraz (wymaga scalenia).

## 12. Stan projektu (aktualizuj po każdym etapie)

**M0 gotowe** (17 testów `pytest` przechodzi, frontend buduje się `tsc` + `vite`):

- `backend/app/tiles/xyz.py`: matematyka XYZ/TMS, regrid, szacunki (testy w
  `tests/test_xyz.py`). Lustrzana wersja dla UI w `frontend/src/tiles.ts`.
  **Zmiany wprowadzaj w obu plikach.**
- Upload porcjami po 8 MiB: `POST /api/uploads` → `PUT /api/uploads/{id}?offset=`
  → `POST /api/uploads/{id}/complete`. Przy złym offsecie serwer zwraca 409 z
  oczekiwanym offsetem (wznowienie).
- Kolejka zadań w SQLite (`jobs/queue.py`) i osobny proces `app.jobs.worker`.
  Handlery: `import` (osmium bbox → tilemaker → `coverage.json` z indeksu
  MBTiles na z10) oraz `render` (wznawianie: pomija istniejące pliki, zapis
  przez `.part`).
- Maska pokrycia jest liczona z indeksu kafelków MBTiles tilemakera, a nie
  przez skanowanie węzłów w Pythonie (za wolne dla dużych plików).
- Podgląd: `/api/maps/{id}/tiles/{z}/{x}/{y}.pbf` oraz
  `/api/maps/{id}/style/{style}.json` (style z przepisanymi źródłami, glifami i
  sprite'ami na lokalne URL-e).
- Styl wbudowany `styles/basic` (schemat OpenMapTiles, bez etykiet, więc nie
  potrzebuje fontów).
- Silnik `debug` (ramka + z/x/y) pozwala testować cały pipeline end-to-end.
- Postęp zadań: UI odpytuje `/api/jobs` co 2 s (SSE odłożone na później).
- ZIP strumieniowy (`ZIP_STORED`, bez kompresji, bo PNG/JPG już są
  skompresowane).
- `packaging/build-deb.sh` buduje pakiet, są gotowe usługi systemd.

**Biblioteka map i profile importu** (po M0, przed etykietami):

- Okno „Zarządzaj mapami…”: rozwijana lista zawartości mapy (PBF, dane importu,
  log, każdy wynik osobno) z rozmiarami i usuwaniem pojedynczych części
  (`GET /api/maps/{id}/contents`, `DELETE /api/maps/{id}/parts/{source|import|log}`).
  Wyniki renderu nigdy nie są usuwane bez wyraźnej zgody użytkownika.
- **Profile importu** to katalogi `tilemaker/profiles/<id>/{profile.json,config.json,process.lua}`
  (`importer/profiles.py`). Są `classic` (domyślny, z warstwami `place` i
  `transportation_name`) oraz `tourist` (+ szlaki `hiking`, `mountain_peak`, schroniska
  w `poi`). `tourist/process.lua` jest kopią classic z dodatkami: zmiany wprowadzaj w obu.
- Import przyjmuje `{profile, bbox}` (`POST /api/maps/{id}/import`). Przy `bbox` PBF jest
  najpierw wycinany `osmium extract --strategy smart`, więc szlaki w okolicy, a nie w
  całym kraju. Mapa zapamiętuje `profile` i `region` (kolumny w `maps`, migracja w `init_db`).
- **Próbka przed importem:** zadanie `sample` robi mały import (max ok. 10×10 km, wokół środka
  obszaru lub widoku) do `sample.mbtiles`, serwowany jako `?variant=sample`. Nie zmienia
  statusu ani prawdziwego importu mapy. UI pokazuje ją z pasem „Importuj z tym profilem”.
- Styl może wymagać profilu: `metadata["osm-tilegen"].profiles` w `style.json`
  (pusta lista = działa z każdym). UI pokazuje tylko style zgodne z profilem mapy.
- Profil `full` = niezmieniona kopia oficjalnego `process-openmaptiles.lua` z tilemakera
  v3.0.0 (aktualizuj z upstreamu, nie edytuj ręcznie) + `config.json` bez warstw z
  shapefile'ami (ocean, urban_areas, ice_shelf, glacier). Wymagają go style
  `bright`, `positron`, `darkmatter`, `liberty` (pobrane z oficjalnych repozytoriów, licencje
  w `styles/<id>/LICENSE.md`, opis zmian w `styles/README.md`). Atrybucja w podglądzie:
  „© OpenMapTiles © OpenStreetMap contributors”.
- Fonty: `styles/fonts/` (Noto Sans Regular/Bold/Italic, glify PBF, OFL). Style
  trzecie mają fonty Metropolis/Roboto zamienione na Noto Sans.
- Style `classic` (domyślny) i `tourist` mają etykiety; `basic` jest bez etykiet.
- Profil `tourist` (rozszerzony wg legend OpenTopoMap/OpenHikingMap): POI w warstwie `poi`
  (klasy hut, shelter, viewpoint, spring, water, saddle, camp, parking, info, castle, shrine,
  cave, waterfall), landcover rock/scree/scrub/wetland/glacier/sand, ścieżki wg `sac_scale`,
  obrys obszarów chronionych. Nie przetestowane z prawdziwym tilemakerem (brak go na Windows).
  Brak: warstnic i cieniowania (wymagają DEM/SRTM, OSM ich nie zawiera).
- `classic` i `tourist` (porównanie z OSM Carto): obwódki dróg, mosty/tunele (`brunnel`), tramwaje/metro/kolej
  wąskotorowa, promy, pasy startowe, więcej `landuse` (cmentarze, przemysł, szkoły, boiska, lotniska...),
  nazwy rzek i jezior (`water_name`), numery domów (`housenumber`), wyspy, ogólne POI (stacje, szpitale,
  sklepy, gastronomia...) w funkcji `general_poi()` (**ta sama w obu `process.lua`, zmieniaj w obu**),
  granice admin. 5–8. Nie testowane z prawdziwym tilemakerem.
- Wciąż brak: poligonów mórz (shapefile do tilemakera); wielu importów jednej mapy naraz
  (dziś jeden import na mapę, nowy zastępuje stary). Profil `tourist` nie rozszerza `full`.

**Następne kroki:**

1. **M0.5:** `scripts/spike-render.sh` na PC i RPi, potem implementacja
   `render/maplibre_native.py` (metatile, pixel ratio = tile_px/256).
2. Style z etykietami: OSM Bright, Positron, Dark Matter, Liberty, razem z
   fontami (glify PBF w `styles/fonts/`) i sprite'ami. Sprawdzić licencje.
3. Poligony mórz dla tilemakera (konfiguracja `config-openmaptiles.json` +
   shapefile).
4. Wielowątkowy render (`render.threads`), przy czym instancja silnika
   przypada na wątek.
5. Zapisywanie i wczytywanie nazwanych zaznaczeń w UI (API już jest).
