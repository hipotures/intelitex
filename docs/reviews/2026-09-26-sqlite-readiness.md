# Przegląd kodu przed wdrożeniem cache SQLite

Data: 2026-09-26. Przejrzany commit: `063ca1d770dde06745e56c31ecc7bb59a6818bd0`.
Podstawa wymagań: [issue #1](https://github.com/hipotures/intelitex/issues/1), wraz z trzema komentarzami, odczytane podczas przeglądu; ostatnia aktualizacja issue: 2026-09-24.

## Ocena

**Osobny, odbudowywalny cache SQLite ma uzasadnienie, ale samo dodanie tabel nie rozwiąże obecnych problemów.** Największe koszty wynikają z budowania projekcji od początku: parsowania i hashowania manifestów, sprawdzania artefaktów, ponownego składania informacji o publikacji oraz wielokrotnego odczytywania całej historii zadań. Frontend dodatkowo uruchamia osobne podsumowanie dla każdego workspace’u.

Przed wykorzystaniem nowego cache jako źródła strony Work należy rozdzielić dane trwałe od pochodnych, poprawić identyfikację źródeł i zapewnić izolację błędów pojedynczych workspace’ów. Osobno wymaga naprawy potwierdzona niezgodność starszych checkpointów P3/P5. Nie zalecam szerokiego przepisywania pipeline’u ani przenoszenia dokumentów i artefaktów do SQLite.

Przegląd nie zmienia kodu aplikacji. Zalecenia poniżej są propozycją kolejnych prac, a nie opisem już wdrożonego cache.

## Zakres i ograniczenia

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

### Własność danych

| Dane | Właściciel | Rola nowego cache |
| --- | --- | --- |
| Checkpointy, approvals, chunks, selected passes | Workspace `state.sqlite3` i powiązane artefakty | Wyłącznie pochodne liczniki/statusy |
| Źródło, języki, etykieta, modele, lifecycle | `workspace.json`, `settings.json`, `web.config.json`, lifecycle i zachowany trwały katalog legacy | Indeks do wyszukiwania i prezentacji |
| Praca aktywna, własność workera, receipts i replay | Supervisor oraz runtime `jobs.sqlite3` | Bieżący overlay; cache nie przyznaje prawa do mutacji |
| Metadane źródeł, Inspect, okładki, Work summary | Nowy indeks SQLite + pliki cache | Dane odbudowywalne |

Docelowa lokalizacja: jeden katalog aplikacyjny pod absolutnym `XDG_CACHE_HOME`, z fallbackiem do `~/.cache/intelitex`, np. `web.sqlite3` i `covers/`. Jest to propozycja układu, nie istniejąca konfiguracja. Runtime pod XDG state pozostaje oddzielny. Nie tworzyć po jednej kopii tego samego cache dla każdego endpointu.

### Minimalny model

| Projekcja | Minimalne pola / uwagi |
| --- | --- |
| `sources` | Scope import-root, source ID i lokalizacja, typ, stat-signature, hash zawartości i jego wersja, tytuł/autor/język deklarowany, status odczytu, czas weryfikacji |
| `source_inspections` | Tożsamość źródła + wersja parsera/detektora, język i źródło rozpoznania, wynik heurystyki, liczby dokumentów/próbek i ograniczone fragmenty |
| `cover_assets` | Klucz źródła, status `available/missing/not_found/error`, względna ścieżka, wersja ekstraktora, sygnatura zależności |
| `workspace_summaries` | Scope workspace-root + workspace ID, metadane, stage, liczniki/procent z podstawą, wynik ostatniej weryfikacji publikacji, sygnatura zależności, świeżość i błąd |
| Metadane schematu | Wersja DB oraz osobne wersje projekcji/parserów; możliwość przebudowy tylko dotkniętej części |

Nie kopiować całych `book.json`, planów, tekstów tłumaczeń ani attempt evidence. Brak danych to `unknown`, nie zero lub „nie rozpoczęto”. W przypadku okładki błąd I/O nie jest tym samym co potwierdzony brak okładki.

Klucze muszą uwzględniać kanoniczny root: `book.epub` i `w-123` mogą występować w wielu konfiguracjach serwera. Sama nazwa pliku, tytuł lub routing ID nie jest globalną tożsamością. Nowego hasha deduplikacyjnego nie używać do zmiany historycznych fingerprintów pipeline’u.

### Odczyt i przebudowa

1. Pierwszy odczyt Work zwraca dostępne małe projekcje; brakujące/uszkodzone wpisy mają jawny stan oczekiwania/błędu. Nie czeka na pełną odbudowę wszystkich projektów.
2. Jedna ograniczona kolejka w procesie serwera odbudowuje tylko brakujące/zmienione wpisy. Powtarzane zlecenia dla tego samego klucza są scalane; liczba równoległych odczytów jest ograniczona.
3. Odczyt autorytatywnych danych i ciężkie obliczenia odbywają się poza transakcją zapisu cache. Następuje krótki upsert z kontrolą, czy zależności nie zmieniły się podczas budowania.
4. Przestarzały builder nie nadpisuje nowszej projekcji: porównanie generacji/rewizji albo ponowna kontrola podpisu przed publikacją wyniku.
5. Aktualny supervisor dokłada status runtime i busy niezależnie od projekcji. Mutacja zawsze ponownie sprawdza stan, rewizję i blokadę w application.

Implementacyjnie: mały port repozytorium projekcji oraz adapter SQLite; builder korzystający ze wspólnej logiki application. Nie wkładać SQL i skanowania filesystemu do handlerów HTTP ani nie budować drugiej definicji progressu w cache. Połączenia SQLite powinny mieć określoną własność wątkową i krótkie transakcje; nie współdzielić połączenia między workerami przez dziedziczenie procesu.

### Unieważnianie

| Zdarzenie | Projekcje do sprawdzenia |
| --- | --- |
| Discovery/Refresh lub zmiana źródła | Metadane źródła, hash, Inspect, okładka/negative cache |
| Setup Save, archive/restore | Lista i metadane jednego workspace’u |
| Prepare/reprepare, F/T/E, zmiana planu | Metadata, liczniki, workflow, Inspect tylko gdy zmieniło się źródło |
| Review edit/confirm/approve | Approval, stage/progress, gotowość publikacji |
| Checkpoint P1/P5, zmiana selected pass, stale chunk | Liczniki i zależne statusy; P2–P4 nie stają się automatycznie „current completed” |
| Zakończenie/failure/cancel joba | Uzgodnienie całego workspace’u z trwałym stanem |
| Publikacja lub zmiana selection | Projekcja publikacji i końcowego workflow |
| Reset P1 | Plan, Review/approval, liczniki oraz historia widoczna na karcie |
| CLI/zewnętrzny zapis/restart | Ponowna kontrola zależności niezależnie od SSE |

Hooki serwera są przyspieszeniem, nie jedynym mechanizmem poprawności. SQLite pracuje w WAL: samo mtime `state.sqlite3` nie wystarcza do rozpoznawania zmian. `PRAGMA data_version` nie jest trwałą rewizją do porównywania między dowolnymi nowymi połączeniami. Należy wybrać i przetestować mechanizm: trwałą rewizję projekcji aktualizowaną przez wszystkie ścieżki Store albo kontrolowaną obserwację DB/WAL i plików, wspartą okresową weryfikacją. Zewnętrzne zmiany artefaktów też muszą zostać wykryte; receipt w DB nie dowodzi, że plik nadal istnieje.

Awaria zapisu cache po udanej mutacji nie może zmieniać jej wyniku na „nieudana” i prowokować ponowienia operacji. Wpis oznacza się do odbudowy. Uszkodzenie/niedostępność cache nie może prowadzić do kasowania źródeł, draftów, receipts ani checkpointów.

`rebuild` powinien deterministycznie odbudować wspierane projekcje z trwałego stanu; `prune` usuwać tylko osierocone wpisy i zasoby wewnątrz cache. Wynik domenowy rebuild musi być równoważny przebudowie przyrostowej, po pominięciu technicznych czasów i identyfikatorów przebudowy.

## Kolejność prac

1. **Poprawność i punkt odniesienia:** F01–F04, testy migracji fingerprintu i trwałości draftów; aktualizacja kontraktów po ponownym audycie. Poprawki F09/F10 można wykonać niezależnie, bez SQLite.
2. **Tanie uproszczenia:** jeden snapshot registry/katalogu na request, wspólny parser EPUB, ograniczenie ponownego liczenia metadanych w ramach tego samego odczytu. Mały cleanup z F12 jako osobny commit.
3. **Cache źródeł:** stat/hash, wersjonowane Inspect i negative cover cache, odbudowa/prune. Przepiąć Work oraz Reader na wspólny indeks.
4. **Cache Work:** czysty builder summary, indeksowane odczyty zbiorcze, asynchroniczne uzupełnianie braków, jawna świeżość, runtime overlay, pełna macierz invalidacji.
5. **Optymalizacja detali po pomiarze:** usage i sekcje. `usage_by_unit_report` skanuje attempt history i kilka plików na attempt; reset-status hashuje dane P1. Nie przenosić tych skanów do Work i nie rozszerzać pierwszego etapu na cały evidence store.

Drugorzędnie: rozważyć lazy loading ekranów Review/Reader/Phase. Obecny build generuje jeden JS około 600,33 kB (gzip 177,50 kB) i ostrzeżenie o chunku >500 kB. To osobna optymalizacja transferu/startu UI, nie rozwiązanie kosztów odczytu backendu. Hashowane assety otrzymują obecnie wspólne `Cache-Control: no-store`; można osobno zaprojektować politykę cache assetów, zachowując właściwe zasady dla API i HTML.

## Kryteria gotowości i pomiary

Wymagane testy backendu nie potrzebują przeglądarki ani modeli:

- Utrata, uszkodzenie i pełna odbudowa cache nie zmieniają workspace’ów, archiwizacji, wyborów Review ani receipts.
- Wynik rebuild jest równoważny aktualnej projekcji autorytatywnej dla draftu, Prepare, częściowego P1, Review, approval, częściowego/stale P5 i publikacji.
- Zmiana przez CLI podczas pracy serwera, WAL-only commit, reset P1 i konkurencyjna przebudowa nie pozostawiają „wiecznie aktualnego” starego wiersza.
- Pojedynczy błędny/usunięty projekt nie blokuje listy; brak źródła nie usuwa jego workspace’ów.
- Przy niezmienionym źródle Refresh nie odczytuje jego pełnej zawartości; dodanie/zmiana/usunięcie pliku odświeża odpowiednie wpisy. Test katalogu uwzględnia pliki zagnieżdżone i atomową podmianę.
- Zmiana parsera/detektora/ekstraktora unieważnia właściwy zakres; negative cache okładki nie trwa po zmianie źródła.
- Replay SSE ani ukończenie starego buildera nie cofają nowszego stanu. Niepewna świeżość nie umożliwia ominięcia blokady mutacji.
- Stare checkpointy P3/P5 są używane zgodnie z jawnie ustaloną polityką kompatybilności.

Pomiar przed/po powinien objąć 1, 10 i 100 workspace’ów, drafty i książki opublikowane, duże manifesty oraz rosnącą historię zadań/attemptów. Osobno mierzyć pusty cache, zapisany cache po restarcie procesu i rozgrzany proces; nie mylić restartu z zimnym cache systemu operacyjnego.

Rejestrować p50/p95 endpointów, liczbę/bajty odczytanych plików, SQL, rozmiar odpowiedzi, liczbę odświeżeń i czas kolejki rebuild. Proponowane wstępne cele do zatwierdzenia na reprezentatywnym lokalnym sprzęcie: zapisany indeks Work dla 100 workspace’ów p95 ≤250 ms, pierwsza strona Library p95 ≤150 ms, zero odczytów dużych manifestów/attemptów na trafieniu cache Work. To **cele**, nie uzyskane wyniki. Budżet opóźnienia uzgodnienia z CLI/SSE musi być jawny; nie wolno osiągnąć szybkiej odpowiedzi przez bezterminowe serwowanie nieaktualnego stanu.

## Wykonana weryfikacja

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
