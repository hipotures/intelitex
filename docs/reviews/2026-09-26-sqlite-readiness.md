# Przegląd kodu przed wdrożeniem cache SQLite

Data: 2026-09-26. Przejrzany commit: `063ca1d770dde06745e56c31ecc7bb59a6818bd0`.
Podstawa wymagań: [issue #1](https://github.com/hipotures/intelitex/issues/1), wraz z trzema komentarzami, odczytane podczas przeglądu; ostatnia aktualizacja issue: 2026-09-24.

**Aktualizacja decyzji architektonicznej: 2026-09-27.** Docelowy cache obejmuje **pełną zawartość JSON-ów**, odwzorowaną na dedykowane tabele, typowane pola, relacje i indeksy. Dotyczy to również tekstu książki, wejść i wyników P1–P5 oraz historii prób. Zastępuje to wcześniejsze zalecenie ograniczenia cache do małych projekcji. Sekcje „Proponowana architektura cache”, „Kolejność prac” i „Kryteria gotowości i pomiary” określają nowy kierunek; ustalenia F01–F12 i końcowa „Wykonana weryfikacja” są zapisem audytu z 2026-09-26, a nie ponownym audytem obecnego kodu. Późniejsze poprawki opisuje [workflow-revisions.md](../workflow-revisions.md).

**Doprecyzowanie źródła prawdy:** obecny podział trwałego stanu między JSON-y i `state.sqlite3` jest stanem przejściowym. Docelowo wszystkie trwałe dane i decyzje mają reprezentację plikową, wystarczającą do odbudowy baz SQLite bez uruchamiania modeli. Wycofujemy wcześniejszą propozycję zachowania `state.sqlite3` jako drugiego, niezależnego właściciela stanu obok nowego cache. Wymaga to migracji istniejących projektów, opisanej poniżej; dzisiejszej bazy nie wolno jeszcze usuwać.

## Ocena

Pierwszy etap implementacji opisuje [migracja checkpointów do JSON](../state-json-migration.md): pełny eksport Store, atomowy HEAD, przełączenie jego odczytów/zapisów i kontrola pozostawionych baz. SQL działa w tym etapie wyłącznie w pamięci procesu jako mechanizm zapytań. Trwały cache obejmujący wszystkie JSON-y książki oraz migracja rejestru runtime są kolejnymi etapami. Architektura poniżej opisuje pełny cel; szczegóły wdrożonego etapu i jego granice podaje wskazany dokument.

**Osobny, odbudowywalny cache SQLite ma uzasadnienie, ale samo dodanie tabel nie rozwiąże problemów z odczytem.** Audyt z 2026-09-26 wskazał koszty budowania projekcji od początku: parsowania i hashowania manifestów, sprawdzania artefaktów, ponownego składania informacji o publikacji oraz wielokrotnego odczytywania całej historii zadań. Frontend dodatkowo uruchamiał osobne podsumowanie dla każdego workspace’u. Część tych ścieżek została później poprawiona; projekt cache nadal wymaga aktualnych pomiarów.

Przed wykorzystaniem nowego cache jako źródła strony Work należy rozdzielić dane trwałe od pochodnych, poprawić identyfikację źródeł i zapewnić izolację błędów pojedynczych workspace’ów. Pełna treść dokumentów i artefaktów JSON ma znaleźć się w relacyjnym cache SQLite. Docelowym źródłem prawdy są zatwierdzone wersje plików wraz z plikowym rejestrem decyzji; istniejący stan zapisany wyłącznie w SQLite trzeba najpierw bezstratnie wyeksportować. Nie zmieniamy przy tym historycznej tożsamości checkpointów ani reguł pipeline’u.

Ten dokument nie wdraża cache ani migracji. Zaktualizowana architektura jest wymaganiem dla kolejnych prac, a nie opisem istniejącej implementacji.

## Zakres i ograniczenia audytu z 2026-09-26

Przejrzano przepływy CLI → application → Store, import i Inspect, katalog źródeł i draftów, odczyty Work/pipeline, publikację, registry/supervisor/SSE oraz frontendowe zapytania i ich odświeżanie. Transporty i Reader/Review objęto analizą wybranych ścieżek i istniejącą regresją; nie jest to audyt każdego wiersza ani ocena jakości tłumaczeń.

Wykonano testy bez żywych modeli, analizę statyczną oraz dodatkowe reprodukcje na tymczasowych projektach. Nie odczytywano prywatnych workspace’ów użytkownika. Liczby dotyczą tego środowiska i małych fixture’ów, nie wydajności rzeczywistej biblioteki ani wcześniejszego problemu 25-sekundowego ładowania.

Zgodnie z późniejszą instrukcją użytkownika nie kontynuowano testów przeglądarkowych i nie instalowano niczego dla przeglądarki. Wcześniej uruchomiony zestaw zakończył się błędami uruchomienia Chromium (`libnspr4.so`); nie dostarczył dowodu poprawności UI. Nie wykonano porównania wizualnego ani pomiaru czasu renderowania w przeglądarce.

## Najważniejsze ustalenia

P1 oznacza problem do rozwiązania przed oparciem nowego cache na danym mechanizmie lub istotną regresję funkcjonalną. P2 oznacza kolejny etap optymalizacji/poprawności. P3 oznacza porządki. „Reprodukcja” oznacza faktycznie wykonany eksperyment; „analiza kodu” nie oznacza zmierzonego wpływu produkcyjnego.

| ID | Priorytet | Ustalenie | Dowód |
| --- | --- | --- | --- |
| F01 | P1 | Poprawne starsze checkpointy P3/P5 nie pasują do aktualnego fingerprintu | Test regresyjny i reprodukcja |
| F02 | P1 | Fingerprint katalogu źródłowego nie rozdziela nazw i zawartości plików | Reprodukcja identycznego hasha dla różnych drzew |
| F03 | P1 | `.intelitex-web.json` zawiera stan trwały; nie wolno potraktować go jako usuwalnego cache | Reprodukcja utraty archiwizacji draftu |
| F04 | P1 | Jeden uszkodzony projekt przerywa odczyt całej listy Work | Reprodukcja |
| F05 | P2 | Work nadal czyta pełne manifesty i sprawdza checkpointy; opublikowane książki uruchamiają kosztowną ścieżkę publikacji | Instrumentacja i analiza kodu |
| F06 | P2 | Historia wszystkich zadań jest dekodowana wielokrotnie dla jednego odczytu Work | 10 wywołań pełnej listy dla 5 workspace’ów |
| F07 | P2 | SSE/polling odświeża szeroki zestaw zapytań, także dla niezmienionych workspace’ów | Analiza kodu |
| F08 | P2 | Discovery/Inspect nie mają trwałego cache, a Reader korzysta z pełnego katalogu | Analiza kodu |
| F09 | P2 | Inspect i Source Reader inaczej interpretują odnośniki w EPUB | Reprodukcja poprawnego EPUB z `%20` |
| F10 | P2 | Bezpośredni URL Source Reader nie jest obsługiwany przez serwer SPA | HTTP TestClient: 404 |
| F11 | P2 | Baseline kontraktu API jest nieaktualny | 20 z 35 śledzonych plików ma inny hash |
| F12 | P3 | Są drobne pozostałości, ale większość wskazań „unused” dotyczy używanego API i kompatybilności | Ruff, Vulture i sprawdzenie referencji |

### F01 — regresja tożsamości checkpointów P3/P5

Miejsca: [engine.py](../../bookpipe/engine.py#L108), `response_schema`, `Runner.fingerprint`, `Runner.run`; [test_p1_compact.py](../../tests/test_p1_compact.py#L213).

Commit `0b20fb7` dodał `minItems`, `maxItems` i enum ID bloków do schematów P3/P5. Schemat jest częścią kanonicznego fingerprintu zadania. Prompt i wejście mogą pozostać identyczne, a zapisany wcześniej checkpoint nie zostaje znaleziony pod nowym hashem.

Pełny zestaw testów zgłosił błąd `test_p2_p5_canonical_fingerprint_fixtures`; powtórzenie tego testu osobno również zakończyło się błędem. Dodatkowo zapisano checkpointy według wcześniejszego schematu, sprawdzono ich wynik aktualnym `validate_result`, a następnie wywołano `Runner.run(..., allow_generate=False)`. Zarówno P3, jak i P5 zwróciły `has no current saved result` mimo poprawnego wyniku.

Wpływ: wznowienie częściowo wykonanej pracy lub uruchomienie zależnego pojedynczego passa może nie wykorzystać istniejącego wyniku. W ścieżce dopuszczającej generację powstaje ryzyko dodatkowego wywołania modelu. Nie oznacza to automatycznego ponownego tłumaczenia wszystkich chunków oznaczonych `done`; reprodukcja dotyczy wyszukania checkpointu w Runnerze. Odzyskiwanie attemptów ma osobne warunki i nie jest ogólnym zamiennikiem tej kompatybilności.

Zalecenie: zachować ostrzejsze wymagania odpowiedzi, ale jawnie rozwiązać zgodność checkpointów. Możliwe rozwiązania to rozdzielenie schematu transportowego od stabilnej tożsamości semantycznej lub wąski odczyt starego fingerprintu z obowiązkową aktualną walidacją. Dodać przypadek wznowienia starego P3 → P4 oraz starego P5. **Samo podmienienie oczekiwanych hashy w teście ukryłoby problem.** Cache webowy nie może naprawiać ani redefiniować tej tożsamości.

### F02 — niejednoznaczny fingerprint drzewa źródłowego

Miejsce: [source_preflight.py](../../bookpipe/application/source_preflight.py#L27), `source_signature`.

Hash otrzymuje kolejno nazwę pliku i jego bajty, bez długości, separatora strukturalnego ani per-file digestu. Następujące różne drzewa dają ten sam strumień wejściowy SHA-256:

```text
Wersja A: a.html = b'xb.htmlY'
Wersja B: a.html = b'x', b.html = b'Y'
Strumień obu wersji: b'a.htmlxb.htmlY'
```

Potwierdzono jednakowy `source_signature`. To nie jest kolizja algorytmu SHA-256, lecz niejednoznaczne kodowanie manifestu. Funkcja zabezpiecza zgodność źródła między preflight, Save i Prepare, więc problem istnieje już przed cache.

Zalecenie: wersjonowany fingerprint z uporządkowanego manifestu `(relative_path, size, content_digest)`, zakodowanego jednoznacznie. Nie nadpisywać przy okazji istniejących fingerprintów w `workspace.json`; zapewnić zgodność lub jawnie rozróżnić wersje.

Dodatkowa obserwacja: identyczny EPUB pod dwiema nazwami otrzymuje różne obecne podpisy, bo podpis obejmuje nazwę. Dla cache należy rozdzielić **lokalizację źródła**, **hash zawartości** i **tożsamość książki używaną przez UI**. Utożsamienie tych trzech pojęć utrudni deduplikację i realizację stabilnych okładek z issue #1.

### F03 — katalog webowy nie jest wyłącznie indeksem

Miejsce: [catalog.py](../../bookpipe/application/catalog.py#L16), `draft`, `save_setup`, `entries`, `archive_draft`.

`.intelitex-web.json` przechowuje m.in. `scope_id`, starsze powiązania source → draft, request keys, indeks nowych draftów, kolory profili i archiwizację draftów. Nowy `workspace.json` pozwala odbudować podstawowe dane nowego draftu i odnaleźć wcześniejsze Save, ale nie zawiera jego stanu archiwizacji. `entries()` dla odzyskanego workspace’u ustawia `archived: False`.

Reprodukcja: Save → archive draft → usunięcie katalogu webowego → ponowna inicjalizacja katalogu. Workspace nadal istnieje, lecz `archived` zmienia się z `True` na `False`. Starsze drafty bez własnego manifestu mają jeszcze silniejszą zależność od tego pliku.

Zalecenie: przed migracją spisać własność każdego pola. Przenieść trwały lifecycle draftu do workspace’u, analogicznie do `web.lifecycle.json` przygotowanych projektów. Zachować trwałość legacy draftów i idempotencji. `scope_id` wpływa na przestrzeń preferencji klienta; jego zmiany również nie mogą być przypadkowym skutkiem czyszczenia cache. Można tymczasowo pozostawić obecny plik jako trwały katalog i zbudować cache obok niego. **Nie przenosić go w całości pod XDG cache ani nie usuwać w ramach rebuild/prune.**

### F04 — brak izolacji błędów na stronie Work i podczas startu

Miejsca: [service.py](../../bookpipe/server/service.py#L30), konstruktor i `list_workspaces`; [web.py](../../bookpipe/application/web.py#L48), `metadata`; [projects.py](../../bookpipe/application/projects.py#L86), `load_valid_book`.

`list_workspaces` składa całą odpowiedź w jednej pętli, bez obsługi błędów pojedynczego rekordu. Uszkodzenie jednego `book.json` do treści `{` powoduje `JSONDecodeError` dla całej listy, mimo czterech poprawnych projektów w fixture. Podobnie błędny fingerprint manifestu może przerwać listowanie. Konstruktor serwera dodatkowo odczytuje profile wszystkich przygotowanych workspace’ów; wadliwe ustawienia mogą uniemożliwić uruchomienie serwera — ten ostatni przypadek wynika z analizy ścieżki, bez osobnej reprodukcji.

Zalecenie: błędy lokalizować do workspace’u; zwracać dostępną tożsamość, stan `unavailable` i bezpieczny kod błędu. Ostatnią poprawną projekcję można pokazać z oznaczeniem nieaktualności. Odbudowa jednego wpisu nie może blokować pozostałych ani zamieniać nieudanego odczytu w „pusty projekt”. Rejestrację profili przy starcie również uniezależnić od poprawności wszystkich książek.

### F05 — kompaktowa odpowiedź nie oznacza taniego obliczenia

Miejsca: [service.py](../../bookpipe/server/service.py#L421), `pipeline_summary`; [workflow.py](../../bookpipe/application/workflow.py#L56), `pipeline_with_evidence`; [publishing.py](../../bookpipe/application/publishing.py#L383), `card_snapshot`, `_status_from`; [util.py](../../bookpipe/util.py#L119), `plan_fingerprint`.

Obecna optymalizacja jest wartościowa: Work nie pobiera `/pipeline`, pomija usage/attempt history, a dla nieopublikowanej książki nie składa gotowości EPUB. Jednak nadal:

- Lista workspace’ów parsuje `book.json`, wylicza fingerprint i liczbę słów.
- Każde `/summary` ponownie ładuje manifest, plan P1 i Review, sprawdza checkpointy oraz składa tablice wszystkich jednostek, mimo że odpowiedź zawiera głównie liczniki.
- Gdy `publication.json` zawiera `last_success`, `card_snapshot` wraca do pełnego `_status_from`, a ten może wywołać `_prepare`, sprawdzić źródła i odczytać opublikowany EPUB do hashowania.

Instrumentacja małego opublikowanego projektu wykazała dla pojedynczego summary jedno wywołanie `publication_builder.inspect` oraz odczyty źródeł, wyników P1/P5 i finalnego EPUB. Zliczono 5 449 bajtów przez `Path.read_bytes`; liczba nie obejmuje odczytów tekstowych JSON. Czas około 1,9 ms na małym, rozgrzanym fixture nie jest prognozą dla dużych książek. Istotny jest potwierdzony rodzaj I/O.

Zalecenie: wydzielić projekcję Work z metadanymi i licznikami, przebudowywaną po zmianach. Pełna walidacja pozostaje przy poleceniach i autorytatywnych odczytach publikacji/Readera. Na karcie można pokazywać wynik ostatniej weryfikacji z czasem i stanem świeżości; nie nazywać niezweryfikowanego cache bieżącym dowodem poprawności publikacji.

### F06 — wielokrotne odczyty historii zadań i katalogu

Miejsca: [service.py](../../bookpipe/server/service.py#L39), `last_job`, `list_workspaces`; [supervisor.py](../../bookpipe/runtime/supervisor.py#L55), `list`, `active_for_project`; [registry.py](../../bookpipe/runtime/registry.py#L90), `list`, `events`; [catalog.py](../../bookpipe/application/catalog.py#L215), `entries`.

`active_for_project` i `last_job` niezależnie pobierają całą historię zadań. Dla pięciu przygotowanych workspace’ów jedno listowanie wywołało `JobRegistry.list()` dziesięć razy, nawet przy pustej historii. Przy W workspace’ach i J zadaniach koszt dekodowania zbliża się do W × J. `events()` również odczytuje zadania, pobiera wszystkie późniejsze eventy przed filtrowaniem i wielokrotnie dekoduje ich JSON. Limit eventów jest per job, a nie globalny.

Drafty dokładają powtarzane odczyty katalogu i skanowanie folderów przez `setup_metadata → source → entries` oraz `draft_lifecycle → entries`; przy wielu draftach praca może rosnąć kwadratowo.

Zalecenie: najpierw jeden snapshot katalogu i zadań na odczyt strony oraz mapy według workspace’u. Następnie indeksowane pola registry (`workspace_root`, projekt/workspace, stan, kolejność/czas) i zapytania wybierające tylko potrzebne rekordy. Registry pozostaje trwałym stanem runtime. Ewentualne usuwanie historii musi zachować semantykę receipts/idempotencji i replay; nie łączyć go bezrefleksyjnie z prune nowego cache.

### F07 — odświeżanie wykracza poza zmieniony workspace

Miejsca: [Home.tsx](../../web/src/features/work/Home.tsx#L23), `WorkspaceRow`; [coordinator.tsx](../../web/src/realtime/coordinator.tsx#L18), `refresh`, `schedule`, polling.

Każdy przygotowany wiersz Work uruchamia osobne `/summary`. Przy zdrowym SSE ogólne odświeżenie następuje także co 15 s. Zdarzenie postępu po debouncingu unieważnia wszystkie obserwowane zapytania scope, poza wyłączeniami dla Library i treści rozdziałów Readera. Jedna aktywna książka może więc wywoływać odczyty wielu niezmienionych książek. To ustalenie z kodu; nie wykonano pomiaru sieci w przeglądarce.

Zalecenie: zbiorczy model Work zawierający projekcje kart, odświeżanie według `workspace_id` i rodzaju zdarzenia oraz scalanie wielu żądań przebudowy tego samego wpisu. Reconnect/gap nadal wymaga szerszego uzgodnienia stanu. Zachować okresową kontrolę zmian CLI — samo SSE serwera nie widzi wszystkich zewnętrznych modyfikacji.

### F08 — niespójne koszty discovery, Inspect i Readera

Miejsca: [imports.py](../../bookpipe/application/imports.py#L49), `sources`, `sources_page`; [source_preflight.py](../../bookpipe/application/source_preflight.py#L141), `inspect_source`; [service.py](../../bookpipe/server/service.py#L242), `library`; [Reader.tsx](../../web/src/features/reader/Reader.tsx#L26).

Work korzysta z paginacji i `links=false`, co ogranicza pracę. Kolejne strony nadal sortują nazwy wszystkich źródeł, a metadane EPUB są ponownie czytane. Dla folderów `_folder_metadata` wykonuje rekurencyjne wyszukiwanie OPF/HTML. Library nie hashuje wszystkich bajtów wszystkich książek — nie należy przypisywać jej kosztu, którego nie ponosi.

Pełny hash wybranego źródła powstaje natomiast w Inspect/preflight, ponownie przy Save i ponownie przed Prepare. Ograniczona próbka językowa nie oznacza ograniczonego całkowitego I/O tej operacji. Inspect i preflight mają osobne klucze zapytań klienta i nie współdzielą trwałego wyniku backendu.

Reader nadal pobiera niepaginowane `/api/library`, które odczytuje metadane wszystkich źródeł i manifesty przygotowanych projektów dla powiązań. Dlatego stary pełny endpoint **jest używany** i nie można usunąć go jako martwego kodu po optymalizacji Work.

Zalecenie: wspólny indeks źródeł dla obu ekranów, stat-signature przed odczytem bajtów, hash tylko nowych/zmienionych źródeł oraz wersjonowany wynik Inspect współdzielony przez setup. Przy hashowaniu sprawdzić stat przed i po odczycie; dla katalogów użyć manifestu plików, nie samego mtime katalogu. Jawny refresh powinien mieć możliwość wymuszenia weryfikacji. Nie rezygnować z kontroli aktualności źródła przed Prepare.

Brakuje również obsługi okładek i ich negative cache w obecnym modelu Library. `Cover` renderuje inicjały; nie otrzymuje source identity. To niezrealizowana część issue #1, nie uszkodzenie istniejącego cache okładek. Lokalny detektor języka jest małą heurystyką dla sześciu języków, bez znalezionego benchmarku reprezentatywnej biblioteki; przed utrwaleniem jego wyników trzeba wersjonować detektor i zmierzyć jakość/czas.

### F09 — rozbieżne parsowanie EPUB

Miejsca: [source_preflight.py](../../bookpipe/application/source_preflight.py#L56), `_epub_samples`; [source_reader.py](../../bookpipe/application/source_reader.py#L17), `_spine`.

Source Reader dekoduje URL i normalizuje ścieżkę odnośnika OPF. Inspect łączy `Path` z surowym `href`, bez dekodowania. Reprodukcja EPUB z `href="chapter%201.xhtml"` i rzeczywistym plikiem `chapter 1.xhtml`: Reader zwraca jeden rozdział, Inspect kończy się `PipelineError: Source inspection unavailable`. To blokuje preflight/setup dla części poprawnych książek. Różnice dotyczą też normalizacji odnośników względnych.

Zalecenie: współdzielona funkcja interpretacji OPF/spine, z dekodowaniem i sprawdzeniem końcowej ścieżki wewnątrz archiwum. Zachować ochronę przed wyjściem poza dozwoloną przestrzeń. Cache powinien korzystać z tego samego parsera, zamiast utrwalać różne interpretacje książki.

### F10 — Source Reader nie przeżywa bezpośredniego wejścia pod URL

Miejsca: [router.tsx](../../web/src/router.tsx#L30), trasa `/reader/source/$sourceId`; [asgi.py](../../bookpipe/server/asgi.py#L142), lista dozwolonych tras SPA.

Backend dopuszcza `/reader` i jeden segment po `/reader`, ale nie dwa segmenty wymagane przez `/reader/source/book.epub`. Test bez przeglądarki, przez produkcyjny `create_app` i HTTP TestClient, dał:

```text
/work                         200 text/html
/reader                       200 text/html
/reader/prepared              200 text/html
/reader/source/book.epub      404 application/json
```

Zalecenie: uzupełnić dopuszczone trasy i testy HTTP dla zakodowanych source ID. Jest to błąd funkcjonalny niezależny od SQLite. Nie potrzeba uruchamiania przeglądarki do pokrycia tego kontraktu.

### F11 — kontrola kontraktu nie jest aktualnym potwierdzeniem API

`check-api-contract.py --repo .` zwrócił `needs_reaudit`, kod wyjścia 3: 20 zmienionych i 15 zgodnych plików względem zapisanego baseline. Integralność oryginalnego v33 i niezmiennego dokumentu referencyjnego przeszła kontrolę.

Różnica hashy nie dowodzi błędu endpointów. Oznacza, że starsza deklaracja zgodności nie obejmuje obecnego kodu. Przed zmianą DTO pod cache należy ręcznie zaktualizować mapę kontraktu i testy, a dopiero potem baseline. Nie odświeżać samych hashy w celu uzyskania zielonego wyniku.

### F12 — kod nieużywany, kompatybilność i utrzymanie

Ruff znalazł 11 wskazań F401, bez F811/F821/F841 w wybranym zakresie. Siedem importów typów usage w `application/results.py` jest dalej eksportowanych przez `application/__init__.py`. `RequestConflict` w supervisorze jest importowany z tego modułu przez adaptery. Automatyczne `--fix` byłoby tu błędem.

Po sprawdzeniu referencji rzeczywistymi kandydatami do małego cleanupu są:

| Element | Ocena i zalecenie |
| --- | --- |
| `application/ports.py:21` — `Closeable` | Nie znaleziono użycia; usunąć albo rzeczywiście wykorzystać jako kontrakt zasobów. |
| `openai_transport.py:58` — `_text_counts` | Tworzony pusty słownik bez odczytu/zapisu; usunąć, nie traktować jako istniejącego cache tokenizacji. |
| `runtime/models.py:10` — `TERMINAL` | Brak referencji w repo; użyć do ujednolicenia stanów albo usunąć. |
| `runtime/protocol.py:29` — `PATH_FIELDS` | Brak użycia; sanitizacja działa przez `PROGRESS_FIELDS`. Usunąć mylącą pozostałość lub jasno powiązać ją z testem. |
| `engine.py` — `Callable`, `ContextFull`; `runtime/processes.py` — `signal` | Brak użycia w module; przed usunięciem `ContextFull` potwierdzić politykę ewentualnego zewnętrznego re-eksportu. |
| `application/review.py:308` — `ReviewSession.summary`; `contracts.preflight_display` | Brak produkcyjnych wywołań pierwszego, drugie używane w testach; kandydaci do przeglądu publicznego API, nie automatycznego kasowania. |

Zachować do czasu jawnego wycofania kompatybilności: `translate.py`, wrappery `engine.analyze/translate`, samodzielne Review/Reader, `server/http.py`, starszy `bookpipe.web` i `create_draft`. Są wywołania CLI, trasy, testy lub dokumentacja tej kompatybilności. Eksperyment `experiments/pass1_compact_transport.py` również ma własne testy i rolę badawczą; nie jest przypadkową kopią do usunięcia.

Vulture przy 90% confidence wskazuje głównie niewykorzystywane argumenty callbacków HTTP i zgodności `serve_forever`, co nie uzasadnia zmiany sygnatur. Przy niższym progu wskazuje m.in. pola dataclassów i metody wywoływane przez frameworki; nie są to dowody martwego kodu. Nie znaleziono podstaw do hurtowego usuwania zadeklarowanych zależności frontendu.

Utrzymaniowo warto ograniczyć zagęszczone, wielozadaniowe funkcje w `server/service.py`, `application/catalog.py` i duże komponenty React. Wydzielenie buildera projekcji oraz repozytorium cache jest konkretną granicą refaktoryzacji; ogólne przepisywanie wszystkich warstw nie jest potrzebne.

## Co już stanowi dobrą podstawę

- Oddzielne application/infrastructure, composition root i odczytowe `ProjectReadScope`/`ReadStore`; zwykłe query nie potrzebuje modelu ani długiego writer locka.
- Atomowe zapisy JSON z fsync oraz projektowe blokady mutacji.
- Checkpointy z hashami artefaktów; rozróżnienie `retained`, bieżącego P5, approval i publikacji.
- Revision tokens, idempotency receipts i niezależny supervisor z workerami; nowy cache nie musi przejmować tych zadań.
- Osobny kompaktowy `/summary`, paginacja Library i wyłączenie usage z kart Work.
- Rozbudowana regresja offline, w tym transporty, recovery, Review, Reader i wieloprojektowy runtime.

## Proponowana architektura cache

### Pełne odwzorowanie JSON-ów

**Wszystkie dokumenty JSON należące do danych workspace’u i aplikacyjnego katalogu mają mieć pełne odwzorowanie w SQLite.** Obejmuje to `book.json`, konfigurację, plany, Review, zatwierdzony leksykon, pamięć, publikacje, `analysis_inputs/*.json`, wszystkie JSON-y w `artifacts/` i zachowaną historię. Nie wybieramy tylko pól używanych dzisiaj przez Work. Pełne pokrycie jest kontraktem importera; odczyt API nadal wybiera tylko potrzebne kolumny i rekordy.

Nie zapisujemy całego dokumentu ani jego zagnieżdżonych obiektów jako JSON w kolumnie TEXT/BLOB. Obiekty otrzymują dedykowane pola i tabele, tablice — rekordy z pozycją, a mapy o dynamicznych kluczach — typowane tabele klucz/wartość odpowiednie dla danej struktury. Tekst akapitu pozostaje zwykłym polem tekstowym. Indeksy wynikają z rzeczywistych filtrów, relacji i sortowania; nie indeksujemy automatycznie każdego pola ani pełnego tekstu akapitów.

Importer musi zachować wartości, typy, kolejność tablic, puste kolekcje oraz różnicę między brakiem pola i `null`. Deduplikacja identycznych bloków jest dozwolona tylko z zachowaniem wszystkich wystąpień i historycznych wersji. Zmiana dzisiejszego tekstu nie może zmienić rekonstrukcji wcześniejszego wejścia modelu. Semantyczny eksport cache → JSON musi być równoważny źródłu; nie wymaga identycznych wcięć ani kolejności kluczy obiektu. Oryginalny hash bajtów artefaktu pozostaje oddzielny od hasha semantycznego i nadal służy do sprawdzania receipts.

Pełne pokrycie nie eliminuje różnych schematów P1–P5. Potrzebny jest jeden rejestr wersjonowanych mapowań dokument → tabele, wykorzystywany przez import, update, eksport kontrolny i walidację. Nowe pole lub nieobsługiwany typ nie może zostać po cichu pominięty: oznacza niepełną projekcję i wymaga rozszerzenia mapowania/migracji. CI sprawdza pokrycie każdego pola wspieranych schematów i równoważność po odtworzeniu. Nie stosujemy uniwersalnego magazynu JSON-path/wartość jako zamiennika dedykowanego modelu relacyjnego.

### Własność danych

Poniższa tabela określa **stan docelowy po migracji**. Obecnie część wymienionych decyzji istnieje wyłącznie w `state.sqlite3`.

| Dane | Właściciel | Rola nowego cache |
| --- | --- | --- |
| Źródło książki i segmentacja | `book.json` oraz oryginalne źródła | Pełna reprezentacja rozdziałów, bloków, segmentów i pozostałych pól |
| Plany, draft Review, leksykon, pamięć, konfiguracja, lifecycle, publikacje | Odpowiednie trwałe JSON-y; dla lifecycle również zachowany trwały katalog legacy | Pełne odwzorowanie, relacje i indeksy |
| Wejścia, wyniki, requests, usage i pozostałe JSON-y prób | Zachowane pliki artefaktów | Pełna treść każdej wersji oraz indeks historii |
| Checkpointy, approvals, status chunks, selected passes, fakty, historia Store | Wersjonowane snapshoty i rejestr zatwierdzonych zmian w JSON, powiązane z artefaktami | Pełny odtwarzalny model, zależności i liczniki; bez wyłącznych danych w SQL |
| Trwałe receipts poleceń, idempotencja i historia runtime | Plikowy rejestr poleceń/zdarzeń we właściwym scope aplikacji | Odtwarzalny indeks runtime i replay |
| Bieżąca własność procesu i blokady | Supervisor, rzeczywiste procesy i blokady systemowe | Informacja ulotna, uzgadniana po restarcie; zapis historii nie wznawia procesu |
| Metadane źródeł, Inspect, okładki, Work summary | Źródła oraz wersjonowane algorytmy ekstrakcji/application | Odbudowywalne dane i agregaty |

Cache nie jest miejscem pierwszego trwałego zapisu edycji. Warstwa Store może nadal obsługiwać komendy i zapytania, ale jej komendy zatwierdzają stan plikowy, a SQL przechowuje jego projekcję. Żadne pole trwałe nie może istnieć wyłącznie w bazie. Odbudowa ma zachować również to, który z kilku wyników wybrano i dlaczego segment jest stale — nie zgadywać na podstawie dat plików.

### Jedno źródło prawdy: zatwierdzone wersje plików

Trwały model obejmuje wersjonowane dokumenty JSON oraz niewielkie rekordy zatwierdzonych zmian, również w JSON. Rekord zmiany określa wersję schematu, generację workspace’u, numer rewizji, poprzednią rewizję/hash, identyfikator operacji, jej wynik i kompletną listę zmienionych dokumentów z hashami oraz jawnych usunięć. Wszystkie decyzje domenowe mają wartości w dokumentach lub typowanych danych rekordu; samo zdarzenie „coś się zmieniło” nie wystarcza do odbudowy.

Rekord zatwierdzenia jest rozstrzygający: wskazuje obowiązujące wersje i odróżnia przygotowane pliki od zakończonej operacji. Snapshot stanu przy określonej rewizji przyspiesza replay; musi być kompletny i zweryfikowany. Rekordy późniejsze od snapshotu pozwalają odtworzyć każdą zatwierdzoną zmianę. Nie jest to okresowy eksport z autorytatywnej bazy — potwierdzenie zapisu następuje dopiero po utrwaleniu plikowego stanu.

Stan rozdzielamy na małe dokumenty domenowe, np. wybory i unieważnienia danego segmentu, decyzje Review, rejestr checkpointów i wpisy historii. Zmiana wyboru jednego segmentu nie wymaga przepisywania całego `book.json` ani jednego wielkiego `state.json`. Wersjonowane snapshoty, rekordy i ich pola również podlegają pełnemu odwzorowaniu w dedykowanych tabelach SQL.

Dotychczasowe pliki o stałych nazwach mogą po migracji pozostać widokami zgodności aktualnych dokumentów. Gdy różnią się od wersji wskazanej zatwierdzonym rekordem, zgłaszamy rozbieżność; nie wybieramy automatycznie „nowszego mtime”. Edycja zewnętrzna takiego pliku wymaga walidacji i zarejestrowania jako nowej zmiany pod blokadą. Dzięki temu JSON, rejestr i SQL nie stają się trzema konkurencyjnymi właścicielami stanu.

### Podział baz: katalog ogólny i cache poszczególnych książek

Rekomendowany układ pod absolutnym `XDG_CACHE_HOME`, z fallbackiem do `~/.cache/intelitex`:

```text
catalog.sqlite3                         # katalog Books i workspace’ów, małe podsumowania
workspaces/<workspace-cache-id>.sqlite3 # pełne JSON-y i odczytowy model jednego workspace’u
sources/<source-cache-id>.sqlite3       # treść źródła Reader bez workspace’u, gdy jest indeksowana
covers/                                # odtworzone zasoby binarne
```

Jedna baza cache na workspace oznacza izolację książek, możliwość przebudowy jednej z nich i brak wspólnego writer locka dla ich szczegółów. Tytuł książki nie jest kluczem: dwa workspace’y tej samej książki muszą być niezależne. Identyfikator cache uwzględnia kanoniczny root, tożsamość workspace’u/źródła i jego generację, aby ponowne użycie ścieżki nie podpięło starej zawartości. Wydania z `books/` mają odrębne tożsamości; powiązanie z workspace’em nie zastępuje zachowania poprzednich wydań.

Baza ogólna zawiera metadane źródeł i wydań, lokalizacje, powiązania source → workspace, lifecycle, wyniki Inspect, referencje okładek, małe podsumowania Work i rewizję, z której je zbudowano. Nie zawiera tekstów wszystkich książek ani całej historii prób. Work/Books korzystają z niej bez otwierania każdej bazy książki. Dla źródła EPUB bez workspace’u treść Readera można indeksować we własnej bazie źródła; nie tworzymy fikcyjnych checkpointów tłumaczenia. W każdym przypadku brakujące ekstrakcje wykonuje ograniczona kolejka w tle.

**Docelowo jedna baza SQLite na workspace obejmuje zarówno projekcję dzisiejszego Store, jak i pełny relacyjny model dokumentów.** Nie utrzymujemy dwóch odrębnych baz książki o konkurencyjnych rolach. Obecna `state.sqlite3` jest potrzebna do migracji; po zweryfikowanym przełączeniu zostaje zachowana jako archiwalna kopia, poza mechanizmem prune cache. Nowa baza nie jest od niej zależna przy rebuild. Runtime może nadal używać osobnego indeksu `jobs.sqlite3`, lecz jego trwałe receipts i historia również muszą być odtwarzalne z plików. Ulotne statusy procesów wymagają ponownego uzgodnienia, nie bezwarunkowego replay.

Aktualizacja bazy książki i katalogu globalnego jest uzgadniana po rewizji, a nie traktowana jako jedna transakcja. SQLite w trybie WAL nie zapewnia atomowości całego zestawu baz podłączonych przez `ATTACH`. Krótkie transakcje i osobne kolejki ograniczają blokowanie; WAL nadal dopuszcza jednego piszącego naraz w danej bazie. [Dokumentacja SQLite WAL](https://www.sqlite.org/wal.html).

### Migracja obecnej `state.sqlite3`

1. Pod blokadą workspace’u, bez aktywnego writera, utworzyć spójną kopię bazy przez SQLite backup API i zinwentaryzować powiązane pliki. Nie kopiować samego pliku DB z pominięciem aktywnego WAL. Oryginał i backup zachować.
2. Wyeksportować kompletny snapshot: `kv`, `jobs`, `merged`, `terms`, `facts`, `chunks`, `history`, identyfikatory i stan alokacji ID. Zachować wszystkie klucze, approvals, wybrane fingerprinty, receipt/hash/path, zależności, unieważnienia i historię; nie rekonstruować ich heurystycznie z najnowszego wyniku.
3. Ze snapshotu i zachowanych dokumentów zbudować nową bazę bez dostępu do oryginalnej `state.sqlite3`. Porównać wszystkie eksportowane wartości i zachowanie odczytów: Review, P1–P5, stale, wybory wyników, gotowość publikacji oraz historię wydań. Nie wywoływać modeli i nie zmieniać artefaktów książki.
4. Przetestować na kopii utratę wszystkich baz cache, odbudowę i wznowienie pracy. Dopiero potem atomowym znacznikiem wersji formatu przełączyć workspace na nowy protokół zapisu. Starszy kod musi odmówić mutacji nowego formatu, zamiast dalej zapisywać starą bazę.
5. Po przełączeniu wszystkie ścieżki CLI/HTTP/worker zapisują wyłącznie przez nowy kontrakt plikowy; nie dopuszczać niezależnego dual-write starego i nowego źródła. Rollback po nowych edycjach wymaga ich eksportu/migracji, nie prostego podłożenia starej kopii DB. Analogicznie przenieść trwałe dane katalogu i runtime przed uznaniem ich indeksów SQL za usuwalne.

Do zakończenia tej migracji obecna `state.sqlite3` pozostaje źródłem niezbędnych danych i wymaga ochrony. Utrata już istniejącej bazy przed eksportem może wymagać odzyskiwania z backupu oraz ręcznego rozstrzygnięcia wyborów; nowy cache nie odtworzy informacji, których wcześniej nigdzie poza bazą nie zapisano. Po migracji warunek odbioru jest jednoznaczny: usunięcie baz SQLite na kopii projektu nie powoduje utraty zatwierdzonego stanu ani ponownego tłumaczenia. Odtwarzalność zakłada zachowane trwałe pliki; nie zastępuje ich backupu.

### Model relacyjny i indeksy

Poniżej rodziny tabel do rozwinięcia według kompletnych schematów dokumentów, nie zamknięta lista pól:

| Rodzina tabel | Zawartość i przykładowe klucze/indeksy |
| --- | --- |
| `documents`, `document_versions` | Ścieżka względna, typ/schemat, rozmiar, stat-signature, hash bajtów, hash semantyczny, generacja importu, stan i błąd; unikalność ścieżki i wersji |
| `books`, `source_files`, `sections`, `scenes` | Wszystkie metadane i struktura `book.json`; indeks kolejności sekcji i powiązań źródłowych |
| `block_versions`, `section_blocks`, `chunks`, `chunk_blocks`, `sentences`, `pieces` | Pełny tekst, struktura i zakresy; wersjonowane powiązania, indeksy `(section_id, position)` i `(chunk_id, position)` |
| Tabele konfiguracji, polityk, modeli, tokenizerów i ostrzeżeń | Wszystkie pozostałe pola manifestów; kolekcje i dynamiczne mapy w tabelach zależnych |
| Tabele planów, terminów, wariantów, decyzji, pamięci i zależności | Draft i approval jako odrębne stany; indeksy term → segment i segment → zależność |
| `passes`, `attempts`, tabele inputs/results/request/response/usage/pricing/recovery | Pełne JSON-y P1–P5 wraz z podstrukturami właściwymi dla ich schematów; indeks `(chunk_id, pass_number, version)`, powiązania receipt/hash, agregaty usage |
| Tabele historii i wydań publikacji | Wszystkie zachowane wersje, selection, fingerprinty, ścieżki i status dostarczenia; indeksy workspace/edition |
| Tabele synchronizacji | Wersja schematu/mapowania, generacja źródła, zastosowany numer zmiany, aktywna generacja cache, postęp rebuild, świeżość i błędy |

Duże przebudowy zapisują kandydacką generację partiami, a następnie krótką transakcją przełączają aktywną generację. Odczyt jednego DTO używa jednej generacji; nie miesza starego planu z nowymi segmentami. Przyrostowy update zmienia tylko dotknięte rekordy i zależne agregaty, w jednej transakcji wraz ze znacznikiem synchronizacji. Hash per dokument pozwala pominąć niezmienione pliki; porównanie rekordów pozwala ograniczyć SQL po zmianie dużego dokumentu. Historyczny wynik pozostaje oddzielną wersją.

Pełny import historii może być kosztowny i odbywa się w tle, z widocznym pokryciem. Aktywny dokument/segment ma pierwszeństwo, ale historia nie jest wyłączona z kontraktu. Dla `salvation-02` odczyt inwentarza 2026-09-27 wykazał 5234 pliki JSON o łącznym rozmiarze 210 720 952 bajtów: 10 w katalogu głównym, 35 w `analysis_inputs/` i 5189 w `artifacts/`. Nie wystarczy więc benchmark samego `book.json`.

### Czy `analysis_inputs/ch0011_a001.json` również trafia do cache?

**Tak, w całości.** Sprawdzony plik ma 175 798 bajtów i pola `SECTION_ID`, `SOURCE_BLOCKS` (181 bloków) oraz `EXISTING_MEMORY` (`matched`, `catalogue`, `catalogue_incomplete`). Zapisujemy sekcję, wszystkie bloki z ich kolejnością i pełną historyczną pamięć wejściową. Wspólne, identyczne wersje bloków mogą być współdzielone przez referencje; snapshot wejścia nie może wskazywać po prostu na „najnowszy blok”. Ta sama zasada obejmuje `inputs.json`, `result.json`, `request.semantic.json`, `request.json`, `response_meta.json`, `usage.json` i pozostałe dokumenty prób. Nazwa lub duży rozmiar pliku nie jest powodem wykluczenia.

Pliki binarne EPUB/okładek i surowe logi, które nie są JSON-em, pozostają plikami. Cache przechowuje ich tożsamość, referencje, metadane i wynik ostatniej kontroli; odczyt/ekstrakcja brakujących treści odbywa się asynchronicznie. Nie wywołujemy modelu do odbudowy cache.

### Zapis: zatwierdzone pliki → jedna baza cache książki → katalog

1. Warstwa application/Store sprawdza revision token i blokadę workspace’u. Aktualny stan wynika z ostatniej zatwierdzonej rewizji plikowej. SQL może przyspieszyć sprawdzenie tylko wtedy, gdy potwierdzono zgodność jego rewizji; przy zaległym cache trzeba odczytać/replay właściwy zakres albo zaczekać na synchronizację. Samo pole `current` w cache nie uprawnia do zapisu.
2. Przygotowuje nowe wersje zmienionych JSON-ów pod unikalnymi ścieżkami: plik tymczasowy, flush/fsync, atomowa podmiana, fsync katalogu. Poprzednie zatwierdzone wersje pozostają dostępne. Zmiana terminu zapisuje właściwe dokumenty Review/leksykonu i zależności, nie przepisuje automatycznie `book.json`. Dane operacji wieloplikowej są na tym etapie kandydatem, nie nowym aktywnym stanem.
3. Po utrwaleniu wszystkich potrzebnych dokumentów atomowo zapisuje pojedynczy rekord zatwierdzenia w plikowym rejestrze rewizji, wraz z hashami, poprzednią rewizją, identyfikatorem i wynikiem operacji. Ten zapis jest punktem zatwierdzenia. Rekord jest również trwałym zleceniem synchronizacji; nie potrzebujemy kolejki istniejącej wyłącznie w SQL. Pomocniczy wskaźnik ostatniej rewizji musi dać się odbudować z rejestru. Po fsync rekordu i katalogu można zwrócić potwierdzenie zapisu z rewizją i stanem cache.
4. Worker importuje dokumenty wskazane przez zatwierdzony rekord i aktualizuje jedną bazę cache workspace’u. Zapis danych, zależnych agregatów i zastosowanej rewizji odbywa się w jednej transakcji. Replay jest idempotentny według generacji, numeru zmiany i identyfikatora operacji. Awaria cache nie cofa zatwierdzonej edycji.
5. Po aktualizacji cache książki worker odświeża małe podsumowanie katalogu globalnego z tą samą rewizją. Niepowodzenie oznacza zaległą projekcję do ponowienia. Widoki zgodności pod dawnymi stałymi nazwami również można odtworzyć z zatwierdzonych wersji.

Nie istnieje wspólna transakcja obejmująca pliki JSON i SQLite; nie jest potrzebna do zatwierdzenia stanu domenowego, bo ten następuje w warstwie plikowej. Awaria przed rekordem zatwierdzenia pozostawia nieaktywne kandydaty. Awaria po rekordzie, ale przed odpowiedzią klientowi lub commit cache, pozostawia zatwierdzoną operację: ponowienie z tym samym identyfikatorem zwraca jej wynik, a synchronizacja nadrabia zaległość. Niekompletny/uszkodzony łańcuch rewizji blokuje potwierdzenie aktualności i wymaga diagnostyki; nie zgadujemy brakujących decyzji. To nowy protokół do implementacji i testów, a nie gwarancja istniejącego `atomic_json`.

Zachowane dokumenty nieudanych lub przerwanych prób również podlegają importowi jako historia z właściwym statusem. Zakończony zapis dokumentu nie oznacza akceptacji wyniku modelu. Niekompletny zapis wieloplikowej operacji może być widoczny w diagnostyce, ale nie może zmienić aktywnego checkpointu ani otrzymać stanu `completed`.

Normalna odpowiedź edycji nie czeka na pełną przebudowę cache. UI pokazuje zwróconą wartość i rewizję; starszy odczyt nie może jej cofnąć. Zapytanie wymagające tej rewizji dostaje zgodne dane albo jawny stan `syncing`, z ograniczonym oczekiwaniem i ponowieniem asynchronicznym. Nie potwierdzamy „zapisano” przed trwałym zatwierdzeniem plików i nie blokujemy interfejsu na odczycie wszystkich artefaktów.

### Synchronizacja i wykrywanie rozbieżności

- Każdy writer CLI/HTTP/worker korzysta ze wspólnego protokołu plikowego. SSE jest powiadomieniem, nie gwarancją synchronizacji. Po restarcie porównujemy ostatnią zatwierdzoną rewizję rejestru z zastosowanym numerem cache i wznawiamy replay; utrata cache uruchamia import snapshotu i późniejszych zmian.
- Zewnętrzne edycje wykrywa skaner inwentarza plików: dodanie/usunięcie, tożsamość, rozmiar, mtime/ctime, następnie hash zmienionych plików. Okresowa i ręczna głęboka kontrola hashuje również pliki o niezmienionych atrybutach. Zmiana zatwierdzonej wersji jest naruszeniem integralności; zmiana widoku zgodności jest kandydatem do walidacji i jawnego importu jako nowej rewizji. Skaner nie zatwierdza automatycznie dowolnego pliku z dysku.
- Trwałą rewizją jest rewizja plikowa; zmiana samej bazy cache nie jest zmianą domenową. Pełna kontrola porównuje odtworzone wartości z plikami również wtedy, gdy numer rewizji w SQL wygląda poprawnie. Przy migracji legacy trzeba odczytać spójny snapshot Store z uwzględnieniem WAL. `PRAGMA data_version` nie zastępuje trwałego numeru rewizji. [Dokumentacja PRAGMA data_version](https://www.sqlite.org/pragma.html#pragma_data_version).
- Builder pobiera spójne wejście pod blokadą/snapshotem, przygotowuje mapowanie poza transakcją zapisu cache, a przed publikacją pod tą samą blokadą protokołu writerów sprawdza rewizję i podpisy źródeł. Nowy zapis unieważnia starszego kandydata. Edycje zewnętrzne poza protokołem wykrywa ponowna kontrola i reconciliation; nie obiecujemy atomowości wobec dowolnych bezpośrednich zapisów na dysku.
- Uszkodzony lub brakujący dokument ma stan `error`/`missing`; można zachować ostatnią poprawną wersję do czytania z oznaczeniem nieaktualności. Nie traktujemy błędu parsowania jako pustej książki ani zgody na usunięcie historii. Nie naprawiamy automatycznie źródłowego JSON-a treścią cache.
- Po migracji `rebuild` odtwarza pełny model z zatwierdzonych JSON-ów, snapshotów, plikowego rejestru zmian i źródeł binarnych, bez dostępu do dawnej `state.sqlite3` i bez generacji modeli. `prune` usuwa wyłącznie osierocone dane cache; zachowane źródłowe wersje dokumentów nadal muszą mieć odwzorowanie. Żadna z tych operacji nie usuwa trwałego rejestru, JSON-ów, checkpointów lub wydań.

### Debug/Config: kontrola spójności

Planowany panel „Cache — spójność” oraz równoważne polecenia CLI mają udostępniać:

| Operacja | Wynik |
| --- | --- |
| Szybka kontrola | Schemat/mapowanie, rewizja źródła i cache, opóźnienie, pokrycie dokumentów, długość kolejki, ostatni błąd, zmienione/brakujące pliki |
| Pełna kontrola | Hash każdego JSON-a, ciągłość zatwierdzonych rewizji, rekonstrukcja semantyczna z dedykowanych tabel i porównanie wszystkich pól; kompletność decyzji, zależności i receiptów |
| Kontrola SQLite | `PRAGMA integrity_check` oraz osobno `foreign_key_check`; nie zastępują porównania z JSON-ami |
| Napraw cache | Ponowny import wskazanego dokumentu/książki lub pełny rebuild; źródła pozostają nietknięte |

Raport podaje dokument, ścieżkę pola, rodzaj różnicy, rewizję i czas weryfikacji. Nie musi ujawniać pełnej treści książki lub requestów. Kontrola działa w tle, ma postęp i możliwość anulowania; podczas zmian raport oznacza zmienioną rewizję i ponawia dotknięty zakres, zamiast zgłaszać fałszywą zgodność. `integrity_check` nie sprawdza błędów kluczy obcych — potrzebne jest osobne sprawdzenie. [Dokumentacja SQLite integrity_check](https://www.sqlite.org/pragma.html#pragma_integrity_check).

### Odczyt i przebudowa

1. Zwykły odczyt danych już odwzorowanych w aktualnym cache używa SQLite. Nie odczytuje ponownie dużych JSON-ów ani nie składa całego `book.json`, aby pobrać jeden rozdział. Brakujący import JSON-a jest zadaniem kolejki, a nie stałym wyjątkiem omijającym cache.
2. Work/Books czytają mały katalog globalny, a ekran książki jej bazę. Paginacja obejmuje sekcje, segmenty i historię. Brak danych to `unknown`/`syncing`/`error`, nie zero lub „nie rozpoczęto”.
3. Dane spoza JSON-ów, np. bieżący runtime, ekstrakcja EPUB, dostępność plików i nowe pomiary weryfikacyjne, są dołączane asynchronicznie przez właściwe adaptery. Wszystkie trwałe decyzje dawnego Store są już częścią plikowego modelu i tej samej bazy cache książki. Nie odtwarzamy definicji ukończenia tłumaczenia z samego statusu procesu.
4. Kolejka scala zadania dla dokumentu/workspace’u, ogranicza równoległe I/O i priorytetyzuje bieżący ekran oraz edycje przed pełnym importem historii. Postęp i niepełne pokrycie są widoczne. Pierwszy odczyt nie czeka na pełną odbudowę wszystkich książek.
5. Mutacje, akceptacja checkpointów i publikacja nadal sprawdzają autorytatywny stan, blokady i potrzebne artefakty. Asynchroniczne odświeżanie UI nie znosi walidacji przed wykonaniem operacji.

Implementacyjnie: mały port repozytorium projekcji oraz adapter SQLite; builder korzystający ze wspólnej logiki application. Nie wkładać SQL i skanowania filesystemu do handlerów HTTP ani nie budować drugiej definicji progressu w cache. Połączenia SQLite powinny mieć określoną własność wątkową i krótkie transakcje; nie współdzielić połączenia między workerami przez dziedziczenie procesu.

### Unieważnianie

| Zdarzenie | Projekcje do sprawdzenia |
| --- | --- |
| Discovery/Refresh lub zmiana źródła | Pełne zmienione dokumenty, metadane źródła, hash, Inspect, okładka/negative cache |
| Setup Save, archive/restore | Pełne manifesty/config/lifecycle, lista i metadane jednego workspace’u |
| Prepare/reprepare, F/T/E, zmiana planu | Pełny manifest i plan, relacje, liczniki, workflow, Inspect tylko gdy zmieniło się źródło |
| Review edit/confirm/approve | Pełny Review/leksykon/pamięć, zależności, approval, stage/progress, gotowość publikacji |
| Dowolny zapis wejścia/wyniku/próby P1–P5 | Pełne JSON-y i historia, usage oraz powiązania z checkpointami |
| Checkpoint P1/P5, zmiana selected pass, stale chunk | Projekcja Store, liczniki i zależne statusy; P2–P4 nie stają się automatycznie „current completed” |
| Zakończenie/failure/cancel joba | Uzgodnienie całego workspace’u z trwałym stanem |
| Publikacja lub zmiana selection | Projekcja publikacji i końcowego workflow |
| Reset P1 | Plan, Review/approval, liczniki oraz historia widoczna na karcie |
| CLI/zewnętrzny zapis/restart | Ponowna kontrola zależności niezależnie od SSE |

Nowego hasha cache nie używać do zmiany historycznych fingerprintów pipeline’u. Wynik domenowy rebuild musi być równoważny przebudowie przyrostowej, po pominięciu technicznych czasów i identyfikatorów przebudowy. Receipt w DB nie dowodzi sam w sobie, że powiązany plik nadal istnieje.

## Kolejność prac

1. **Aktualny punkt odniesienia:** ponownie ocenić F01–F12 po już wprowadzonych poprawkach, zinwentaryzować wszystkie rodziny JSON-ów i ich schematy oraz pola trwałego Store. Zmierzyć zapis, pełny import i odczyt na dużej książce z historią; zachować testy starych checkpointów i trwałości draftów.
2. **Pełny model i importer:** wersjonowane mapowania wszystkich JSON-ów, tabele/relacje/indeksy per workspace, semantyczna rekonstrukcja i kontrola pokrycia. Wdrożyć pełny rebuild oraz update przez ten sam importer, bez wyjątków wyłączających duże dokumenty lub historię.
3. **Jedno trwałe źródło i migracja:** protokół zatwierdzania wersji plików, kompletny eksport legacy Store, przełączenie wszystkich writerów, replay i uzgadnianie zmian zewnętrznych. Sprawdzić odbudowę na kopii po usunięciu wszystkich baz SQLite, zanim przełączymy produkcyjny workspace. Obecna baza pozostaje chroniona do końca migracji.
4. **Odczyty SQL i katalog globalny:** przepiąć Work/Books, szczegóły, Reader, Review, Translate, Publish i usage na właściwe projekcje. Zapewnić paginację, asynchroniczne uzupełnianie braków, runtime i walidację operacji przez application.
5. **Debug/Config i pomiary:** szybka/pełna kontrola, naprawa tylko cache, postęp i anulowanie; porównać wyniki z autorytatywnymi odczytami, zmierzyć rozmiar bazy, p50/p95 i opóźnienie synchronizacji. Historia może być importowana z niższym priorytetem, ale nie jest pomijana.

Optymalizacje bundla frontendu są osobnym zadaniem. Historyczny pomiar i polityka assetów z audytu 2026-09-26 nie stanowią opisu aktualnego wdrożenia; późniejsze zmiany opisuje [workflow-revisions.md](../workflow-revisions.md).

## Pomiar zapisu JSON — 2026-09-27

Wykonano odczyt rzeczywistego `salvation-02/book.json` (12 785 694 bajty, około 12,8 MB), a wszystkie zapisy wyłącznie na tymczasowej kopii pod `/home/user/DEV`, na tym samym systemie plików. Użyto `.venv/bin/python`, istniejących funkcji `atomic_json`, `atomic_text` i `plan_fingerprint`. Każdy wariant wykonano 21 razy; pierwszy pomiar odrzucono, poniżej 20 próbek i p95 metodą nearest-rank. Nie czyszczono cache systemu operacyjnego. SHA-256 źródłowego pliku przed i po eksperymencie był identyczny; tymczasowe pliki usunięto.

| Operacja | p50 | p95 | Maksimum |
| --- | ---: | ---: | ---: |
| Odczyt pliku i parsowanie JSON | 58,58 ms | 731,73 ms | 743,39 ms |
| Obliczenie `plan_fingerprint` | 56,19 ms | 603,96 ms | 618,45 ms |
| Serializacja `json.dumps(ensure_ascii=False, indent=2)` | 55,68 ms | 583,24 ms | 688,62 ms |
| Zapis gotowego tekstu + fsync pliku + replace + fsync katalogu | 17,62 ms | 142,98 ms | 164,80 ms |
| Pełne `atomic_json` z serializacją | 65,51 ms | 599,94 ms | 784,94 ms |
| Odczyt → zmiana tytułu sekcji → fingerprint → `atomic_json` | 217,35 ms | 2115,46 ms | 2205,24 ms |
| SQLite: UPDATE jednego tytułu po PK + commit, WAL, synchronous=FULL | 1,42 ms | 11,51 ms | 14,89 ms |

Pomiar SQLite używał osobnej tymczasowej tabeli `sections(id TEXT PRIMARY KEY, title TEXT NOT NULL, position INTEGER NOT NULL)` z 35 rekordami. To **koszt małej transakcji**, nie benchmark pełnego relacyjnego importera, wszystkich indeksów, zależnych agregatów lub synchronizacji. Cały cykl JSON mierzył konserwatywnie również fingerprint, choć zmiana samego tytułu nie zmienia jego wartości. p95 poszczególnych etapów nie należy sumować. Mała liczba próbek i duża zmienność środowiska nie pozwalają obiecać stałej latencji.

Wniosek z pomiaru: sam zapis tej wielkości JSON-a nie musi trwać sekund, ale pełna ścieżka edycji osiągnęła tutaj ponad dwie sekundy. Szybki UPDATE cache nie usuwa kosztu wcześniejszego zapisu źródła. Nie zapisujemy dużego manifestu przy każdej zmianie małego Review/config ani przy każdym naciśnięciu klawisza; zapisujemy właściwy dokument na zatwierdzenie edycji. Serializacja i I/O odbywają się poza event loop HTTP. Docelowe małe dokumenty stanu i rejestr zatwierdzeń wymagają osobnego benchmarku liczby zapisów i fsync; powyższy pomiar dotyczy obecnego `atomic_json`, nie nowego protokołu. Nie zastępujemy potwierdzonego zapisu samą aktualizacją usuwalnego cache.

## Kryteria gotowości i pomiary

Wymagane testy backendu nie potrzebują przeglądarki ani modeli:

- Utrata, uszkodzenie i pełna odbudowa cache nie zmieniają workspace’ów, archiwizacji, wyborów Review ani receipts.
- Na kopii zmigrowanego projektu usuwa się wszystkie bazy SQLite, także indeks runtime. Odbudowa wyłącznie z trwałych plików zachowuje approvals, selected passes, stale, zależności, receipts/idempotencję i historię; nie uruchamia modeli ani nie wznawia procesów na podstawie starego statusu. Odtworzone odczyty i dalsze mutacje są równoważne stanowi sprzed usunięcia.
- Import → rekonstrukcja zachowuje wszystkie pola każdej rodziny JSON-ów, również `analysis_inputs`, wszystkie passy i historię prób; test obejmuje tablice, puste kolekcje, `null`, brak pola, liczby i historyczne wersje bloków. Nieobsługiwane pole nie przechodzi jako kompletna projekcja.
- Wynik rebuild jest równoważny aktualnej projekcji autorytatywnej dla draftu, Prepare, częściowego P1, Review, approval, częściowego/stale P5 i publikacji.
- Awaria podczas przygotowania wersji JSON, przed/po zapisie rekordu zatwierdzenia, przed odpowiedzią klientowi, przed/po commit cache i przed odświeżeniem katalogu globalnego zostaje rozpoznana po restarcie. Replay nie duplikuje wersji, a recovery nie akceptuje częściowo zapisanej operacji.
- Edycja zwraca rewizję trwałego zapisu; starszy odczyt cache nie cofa widocznej wartości. Konkurencyjny rebuild nie miesza generacji ani nie nadpisuje nowszego update.
- Zmiana przez CLI podczas pracy serwera, reset P1 i konkurencyjna przebudowa nie pozostawiają „wiecznie aktualnego” starego wiersza. Migracja uwzględnia stan legacy zapisany tylko w WAL oraz blokuje starych writerów po przełączeniu formatu.
- Debug/Config wykrywa brak dokumentu, uszkodzenie SQL, naruszenie relacji i różnicę wartości pola nawet przy poprawnych metadanych cache. Naprawa zmienia tylko cache. Pełna kontrola rozpoznaje również zewnętrzny zapis omijający rejestr zatwierdzeń.
- Pojedynczy błędny/usunięty projekt nie blokuje listy; brak źródła nie usuwa jego workspace’ów.
- Przy niezmienionym źródle Refresh nie odczytuje jego pełnej zawartości; dodanie/zmiana/usunięcie pliku odświeża odpowiednie wpisy. Test katalogu uwzględnia pliki zagnieżdżone i atomową podmianę.
- Zmiana parsera/detektora/ekstraktora unieważnia właściwy zakres; negative cache okładki nie trwa po zmianie źródła.
- Replay SSE ani ukończenie starego buildera nie cofają nowszego stanu. Niepewna świeżość nie umożliwia ominięcia blokady mutacji.
- Stare checkpointy P3/P5 są używane zgodnie z jawnie ustaloną polityką kompatybilności.

Pomiar przed/po powinien objąć 1, 10 i 100 workspace’ów, drafty i książki opublikowane, duże manifesty oraz rosnącą historię zadań/attemptów. Osobno mierzyć pusty cache, zapisany cache po restarcie procesu i rozgrzany proces; nie mylić restartu z zimnym cache systemu operacyjnego. Zmierzyć pełny import wszystkich JSON-ów, przyrostowy update małego/dużego dokumentu, rozmiar indeksów i WAL, czas pełnej kontroli spójności oraz opóźnienie od trwałej edycji do zgodnego odczytu UI. Trafienie cache szczegółów/Readera nie może powodować odczytu całego `book.json` lub historii prób.

Rejestrować p50/p95 endpointów, liczbę/bajty odczytanych plików, SQL, rozmiar odpowiedzi, liczbę odświeżeń i czas kolejki rebuild. Proponowane wstępne cele do zatwierdzenia na reprezentatywnym lokalnym sprzęcie: zapisany indeks Work dla 100 workspace’ów p95 ≤250 ms, pierwsza strona Library p95 ≤150 ms, zero odczytów dużych manifestów/attemptów na trafieniu cache Work. To **cele**, nie uzyskane wyniki. Budżet opóźnienia uzgodnienia z CLI/SSE musi być jawny; nie wolno osiągnąć szybkiej odpowiedzi przez bezterminowe serwowanie nieaktualnego stanu.

## Wykonana weryfikacja audytu z 2026-09-26

Środowisko: Python 3.14.4, Node 22.22.1, npm 11.19.1; Ruff 0.16.9 i Vulture 2.16 uruchomione przez `uvx`. Nie zmieniano zależności projektu.

| Polecenie / metoda | Wynik |
| --- | --- |
| `uv run --group dev python -m pytest -q` | **949 passed, 1 failed**, 3 ostrzeżenia, 146,32 s; błąd F01 |
| Pojedynczy `tests/test_p1_compact.py::test_p2_p5_canonical_fingerprint_fixtures` | **1 failed**, powtarzalne |
| `node --test tests/*.cjs` | **31 passed** |
| `npm run typecheck` w `web/` | Passed |
| `npm run lint` w `web/` | Passed |
| `npm run test:unit` w `web/` | **49 passed**, 15 plików |
| `npm run build` w `web/` | Passed; ostrzeżenie o rozmiarze JS |
| `verify-mockup.py` | Passed, oba niezmienne zasoby zgodne |
| `check-api-contract.py --repo .` | Exit 3, `needs_reaudit`; 20 changed / 15 matched |
| `python -m unittest discover -s .agents/skills/intelitex-web/scripts -p 'test_*.py' -v` przez `uv run` | **31 passed** |
| Ruff: F401/F811/F821/F841 dla `bookpipe`, `experiments`, `translate.py` | 11 F401; ręcznie oddzielono re-eksporty od kandydatów do cleanupu |
| Vulture 90% oraz pomocniczo 60% | Kandydaci sprawdzeni przez referencje; bez automatycznego kasowania |
| Tymczasowe projekty + mockowanie liczników odczytu | Reprodukcje F01–F06 i F09 opisane powyżej |
| Produkcyjny ASGI przez HTTP TestClient | Reprodukcja F10; bez przeglądarki |
| Próba `npm run test:browser` przed ograniczeniem zakresu | 14 błędów startu Chromium; **brak zaliczonej walidacji przeglądarkowej**. Nie ponawiano i niczego dla niej nie instalowano. |

Ostrzeżenia Pythona dotyczyły deprecacji w Starlette/httpx/AnyIO oraz celowo zduplikowanego wpisu ZIP w teście. Nie dowodzą błędu aplikacji. Żaden opisany wynik nie pochodzi z historycznych raportów repozytorium.
