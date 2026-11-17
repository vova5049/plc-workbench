# Sterowanie oświetleniem - wymagania projektu

**Status:** etap 1 zrealizowany jako odtwarzalny przykład. Komunikacja między PLC jest planowana, niezaimplementowana.

## 1. Cel i zakres

Zbudować oprogramowanie sterowania oświetleniem dla trzech sterowników Mitsubishi FX3U. Każdy sterownik realizuje lokalne sterowanie niezależnie od komputera i pozostałych PLC. Architektura powinna pozwalać na rozszerzenie do czterech stacji i późniejsze dodanie komunikacji między nimi, bez przebudowy lokalnej logiki przycisków i stref.

Zestaw narzędzi oraz instrukcje dla agentów mają pozostać modułowe i wielokrotnego użytku.

## 2. Sprzęt i sygnały

- Wejścia `X`: chwilowe przyciski monostabilne.
- Wyjścia `Y`: przekaźniki przypisane do stref oświetlenia.
- Bity `M`: warstwa pośrednia między fizycznymi wejściami a logiką programu.
- Parametryzowane przyporządkowanie przycisków do stref, bez wiązania logiki gestów z adresami fizycznymi.
- Urządzenia `X` i `Y` adresowane ósemkowo, zgodnie z konwencją FX3U.

Obecna konfiguracja przykładu: `PLC1`, `PLC2`, `PLC3`; po 24 przyciski i 24 strefy na sterownik. Ta konfiguracja nie stanowi zweryfikowanej mapy okablowania dowolnej instalacji.

## 3. Logika lokalna

Każdy przycisk ma być rozpoznawany jako cztery typy zdarzeń:

| Zdarzenie | Wymaganie |
|---|---|
| Pojedyncze naciśnięcie | Przełączenie skonfigurowanej listy stref; domyślnie strefy odpowiadającej przyciskowi |
| Podwójne naciśnięcie | Oddzielna komenda z opcjonalną listą stref |
| Potrójne i kolejne naciśnięcia | Osobna komenda, bez przypisanej akcji wyjściowej w obecnej konfiguracji |
| Długie naciśnięcie | Oddzielna komenda, opcjonalnie wyłączenie skonfigurowanych stref przez `RST` |

Parametry obecnej implementacji: okno bez kolejnego impulsu **400 ms**, próg długiego naciśnięcia **2 s**. Rzeczywista dokładność czasów zależy od podstawy timerów i cyklu PLC; nie jest potwierdzona dla każdego urządzenia.

Dodatkowe zasady:
- Przyporządkowanie `X -> M` oraz `przycisk/zdarzenie -> Y` musi być definiowane w konfiguracji.
- Obsługa przycisku ma generować impulsy komend wykorzystywane przez lokalną logikę stref.
- Opcjonalne listy podwójnego i długiego naciśnięcia są obecnie puste; automatyczne wyłączanie jest wyłączone.
- `D100` zlicza komendy; `D101` przechowuje ostatnią komendę jako `indeks_przycisku * 10 + kod_komendy`, gdzie kody 1/2/3/4 oznaczają odpowiednio pojedyncze/podwójne/potrójne lub kolejne/długie naciśnięcie. To diagnostyka ostatniego zdarzenia, **nie kolejka zdarzeń**.

## 4. Etap 1 - programy niezależnych sterowników

Wygenerować i zweryfikować trzy programy PLC z jednej konfiguracji, bez wymagań dotyczących komunikacji sieciowej. Każdy program ma sterować wyłącznie lokalnymi strefami i działać bez PC.

Kryteria weryfikacji publicznego przykładu:
1. Poprawne wygenerowanie kodu i CSV dla wszystkich trzech stacji.
2. Zgodność kompilacji z wersjonowanym `program.il`.
3. Dekompilacja i ponowna kompilacja dają identyczne bajty.
4. Obecna konfiguracja generuje **1585 instrukcji**, **5954 bajty** i SHA-256 `499f966aef619067b1ce6aaed78ee74eb392bc519d92442119c67d7f5429a38c` dla każdej stacji.
5. Rzeczywisty odczyt dotyczy wyłącznie `legacy-reference.il` (SHA-256 `ca59c2fa…28b930b2`) na **PLC2** w opisanym teście; nie kwalifikuje poprawionego aktywnego `program.il` ani nie oznacza pełnych testów gestów i wyjść dla wszystkich trzech stacji.

Implementacja i procedura odtwarzania: [README.md](README.md).

## 5. Etap 2 - komunikacja między PLC

Wymagania docelowe:
- Sterowanie wybranymi strefami lub komendami innego PLC z poziomu lokalnego programu oraz z aplikacji Python na komputerze.
- Jawne adresowanie stacji, kontrola potwierdzeń, czasów odpowiedzi i zachowania po utracie komunikacji.
- Zachowanie autonomicznego sterowania lokalnego podczas awarii połączenia.
- Rozdzielenie warstwy transmisji, protokołu, modelu komend i aplikacji.

**Warunek rozpoczęcia:** udokumentować dokładne modele CPU, złącza, wymagane moduły i rzeczywiście obsługiwany protokół. Wbudowany okrągły port programowania FX3U z użytym tutaj protokołem Programming Communication **nie jest automatycznie szyną 1:N Computer Link**. Limit urządzeń, multidrop RS-422 i wirtualny port COM pozostają kwestiami do potwierdzenia w dokumentacji wybranego interfejsu i testach sprzętowych. Nie konfigurować `D8120` na podstawie założeń o porcie programowania.

Nie deklarować gotowej komunikacji PLC-PLC na podstawie działającego programu lokalnego.

## 6. Moduły narzędzi i obowiązki

- [fx-plc-dsl](../../skills/fx-plc-dsl/SKILL.md): źródła logiki, deklaracje urządzeń, mapowanie I/O oraz kompozycja wielokrotnego użytku.
- [fx3u-toolchain](../../skills/fx3u-toolchain/SKILL.md): kompilacja, dekompilacja, symulacja, kontrola obrazu i dowody poprawności kodowania.
- [fx-plc-programming-comm](../../skills/fx-plc-programming-comm/SKILL.md): komunikacja przez zweryfikowany interfejs programowania, odczyty i ściśle autoryzowane operacje zapisu.

Podział ten dotyczy istniejących umiejętności narzędziowych. Nowa funkcjonalność sieciowa wymaga osobnej specyfikacji i kwalifikacji sprzętowej; nie należy dopisywać do niej niezweryfikowanych gwarancji.

## 7. Bezpieczeństwo i zakres potwierdzenia

Program przykładowy steruje fizycznymi wyjściami `Y`. Przed uruchomieniem na instalacji trzeba potwierdzić mapowanie wejść/wyjść, adresy zajętych urządzeń, obciążenia wyjść i działanie po STOP/RUN. Zapis programu do CPU wymaga wyraźnej zgody, aktualnej kopii programu oraz planu odtworzenia stanów instalacji.

Etap 2 pozostaje niezaimplementowany. Poziomy dowodów i ograniczenia wynikają z [case study](README.md), [evidence](../../docs/evidence.md) i [SAFETY](../../SAFETY.md).

Poprawiony aktywny przykład ma tylko testy hosta/symulatora; historyczny dowód PLC dotyczy `legacy-reference.il`, nie zmienionego programu.
