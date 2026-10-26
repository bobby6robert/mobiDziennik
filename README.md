# mobiDziennik for Home Assistant

Niestandardowa integracja (Custom Integration) dla Home Assistanta, umożliwiająca bezpośrednie pobieranie danych ze szkolnego dziennika **mobiDziennik (WizjaNet)** bez użycia kluczy partnerskich – logowanie odbywa się automatycznie poprzez symulację przeglądarki.

## Funkcje
Integracja pobiera i udostępnia w Home Assistancie kluczowe informacje szkolne:
* 📅 **Plan lekcji** (dla bieżącego i następnego tygodnia)
* ⭐ **Oceny** (wraz z wyliczaniem średnich cząstkowych i statusem liczenia do średniej)
* ✉️ **Wiadomości** (licznik nieprzeczytanych wiadomości oraz lista z treścią)
* 📝 **Sprawdziany i kartkówki** (zapowiedziane testy wraz z tematyką i datą)
* 🕒 **Frekwencja** (nieobecności i spóźnienia)
* 💬 **Uwagi i pochwały** (wpisy od nauczycieli)
* 🍀 **Szczęśliwy numerek**
* 📆 **Najbliższy sprawdzian** – data i godzina, przedmiot, rodzaj i tematyka, liczba dni do sprawdzianu (od v1.0.6)
* 📈 **Średnia ocen** – średnia ze wszystkich przedmiotów, średnie przedmiotów, najlepszy i najsłabszy przedmiot (od v1.0.6)
* 🚫 **Nieusprawiedliwione nieobecności** – licznik do automatyzacji (od v1.0.6)

### Zmiany w v1.1.2
* **Naprawione „Błąd konfiguracji” zamiast karty** – karta rejestruje się teraz jako zasób pulpitu (tak jak karty z HACS), więc ładuje się zawsze. Po aktualizacji zrestartuj HA i odśwież stronę (Ctrl+F5).
* Poprawiony odczyt sprawdzianów w szkołach, gdzie w tabeli jest klasa/grupa w formie „1 G2” (przedmiot i rodzaj były przesunięte).
* Kolory pasków średnich na karcie.

### Zmiany w v1.1.1 (niewydana)
* Poprawiony odczyt sprawdzianów w szkołach, gdzie w tabeli jest klasa/grupa w formie „1 G2” (przedmiot i rodzaj były przesunięte).
* Kolory pasków średnich na karcie.

### Nowość w v1.1.0 – gotowa karta i kreator pulpitu
* 🧩 **Karta „mobiDziennik”** – ładuje się sama razem z integracją (nie trzeba dodawać zasobów ani instalować nic z HACS).
  Na pulpicie: **Edytuj → Dodaj kartę → mobiDziennik**, wybierz ucznia z listy i zaznacz, co pokazać.
  Karta pokazuje plan na dziś lub najbliższy dzień nauki (strzałki ‹ › przełączają dni, bieżąca lekcja jest podświetlona,
  zastępstwa przekreślone), sprawdziany z odliczaniem dni, wiadomości (kliknięcie otwiera mobiDziennik),
  ostatnie oceny ze średnimi, frekwencję oraz uwagi. Wygląd dopasowuje się do motywu (jasny / ciemny).
* 🪄 **Kreator pulpitu** – przy dodawaniu integracji zostaw zaznaczone **„Utwórz pulpit mobiDziennik”**.
  Po chwili w menu bocznym pojawi się pulpit **mobiDziennik** z osobną zakładką dla każdego ucznia
  (pulpit tworzy się w przeglądarce administratora – jeśli go nie widać, odśwież stronę).
  Dla integracji dodanych wcześniej: **Ustawienia → Urządzenia i usługi → mobiDziennik → Konfiguruj → „Utwórz pulpit ponownie”**.
* Czujnik **Plan** przelicza „lekcje dziś” zaraz po północy i ma nowe atrybuty `najblizszy_dzien` i `najblizszy_dzien_lekcji`.
* Każdy czujnik ma atrybut `mobidziennik` (rodzaj czujnika) – po nim karta odnajduje encje ucznia.

> **Dlaczego kafelek „Plan” pokazuje 0 lekcji?** Stan czujnika to *liczba lekcji dziś* – w weekend i w dni wolne jest 0.
> Cały plan jest w atrybucie `lekcje`. Najprościej użyć karty **mobiDziennik**, która pokazuje najbliższy dzień nauki.

### Zmiany w v1.0.6
* Pełne nazwy rodzajów sprawdzianów („Kartkówka”, „Sprawdzian” zamiast „kar...”, „spr...”).
* Sprawdziany mają pole `data_iso` i są posortowane od najbliższego.
* Frekwencja ma podsumowanie: `nieusprawiedliwione`, `usprawiedliwione`, `spoznienia`.
* Poprawiony numer wersji, link do dokumentacji i autor w `manifest.json`.

---

## Instalacja przez HACS (Zalecana)

1. Upewnij się, że masz zainstalowany komponent **HACS** w Home Assistancie.
2. Otwórz HACS w menu Home Assistanta.
3. Kliknij w trzy kropki w prawym górnym rogu i wybierz **Repozytoria niestandardowe** (Custom repositories).
4. Wklej adres URL repozytorium: `https://github.com/bobby6robert/mobiDziennik`
5. Wybierz kategorię: **Integracja** (Integration) i kliknij **Dodaj**.
6. Wyszukaj integrację `mobiDziennik` w HACS, pobierz ją i **zrestartuj Home Assistanta**.

---

## Konfiguracja

1. Przejdź do **Ustawienia** -> **Urządzenia i usługi** -> **Dodaj integrację**.
2. Wyszukaj **mobiDziennik**.
3. Wprowadź dane logowania:
   * **Nazwa szkoły** (np. jeśli adres to `szkola123.mobidziennik.pl`, wpisz `szkola123`)
   * **Login** (adres e-mail lub login do dziennika)
   * **Hasło**
   * **Imię ucznia** (dla rozróżnienia profili – pojawi się w nazwie urządzenia i na pulpicie)
   * **Utwórz pulpit mobiDziennik** (zalecane – patrz wyżej)

---

## Karta mobiDziennik (YAML – dla zaawansowanych)

```yaml
type: custom:mobidziennik-card
device: 0123456789abcdef            # wybierany z listy w edytorze karty
sekcje: [plan, sprawdziany, wiadomosci, oceny, frekwencja]   # dowolny wybór i kolejność
naglowek: true                      # imię, szczęśliwy numerek, średnia, nowe wiadomości
```

## Kompatybilność z Dashboardami
Integracja tworzy standardowe encje sensorów w Home Assistancie, które można łatwo integrować z własnymi kartami (np. w stylach minimalistycznych, Mushroom czy autorskich kafelkach typu Aurora).
