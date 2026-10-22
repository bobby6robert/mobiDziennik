# mobiDziennik for Home Assistant

Niestandardowa integracja (Custom Integration) dla Home Assistancena, umożliwiająca bezpośrednie pobieranie danych ze szkolnego dziennika **mobiDziennik (WizjaNet)** bez użycia kluczy partnerskich – logowanie odbywa się automatycznie poprzez symulację przeglądarki.

## Funkcje
Integracja pobiera i udostępnia w Home Assistancie kluczowe informacje szkolne:
* 📅 **Plan lekcji** (dla bieżącego i następnego tygodnia)
* ⭐ **Oceny** (wraz z wyliczaniem średnich cząstkowych i statusem liczenia do średniej)
* ✉️ **Wiadomości** (licznik nieprzeczytanych wiadomości oraz lista z treścią)
* 📝 **Sprawdziany i kartkówki** (zapowiedziane testy wraz z tematyką i datą)
* 🕒 **Frekwencja** (nieobecności i spóźnienia)
* 💬 **Uwagi i pochwały** (wpisy od nauczycieli)
* 🍀 **Szczęśliwy numerek**

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
   * **Imię ucznia** (opcjonalnie, dla rozróżnienia profili)

---

## Kompatybilność z Dashboardami
Integracja tworzy standardowe encje sensorów w Home Assistancie, które można łatwo integrować z własnymi kartami (np. w stylach minimalistycznych, Mushroom czy autorskich kafelkach typu Aurora).
