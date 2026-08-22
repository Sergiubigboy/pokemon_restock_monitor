"""
Test de integrare pe bucla din main.py.

Verifica cea mai riscanta parte: ce notificare pleaca pentru fiecare produs.
Scraperul, Telegram-ul si scrierile pe disk sunt toate inlocuite cu duble —
testul nu deschide niciun browser, nu trimite niciun mesaj si nu atinge starea
reala a botului.
"""

import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
from tests.test_watchlist import KRIT, _watchlist_test


def produs(nume, pret, url="https://krit.ro/p", qty=None):
    return {"name": nume, "url": url, "image": None, "price": pret, "qty": qty}


class TestBuclaDeScanare(unittest.TestCase):

    def setUp(self):
        self.watchlist = _watchlist_test()
        self.known = {}
        self.vip_groups = [{"keywords": ["booster box"], "message": "SUPER DROP"}]
        self.blacklist = ["sleeve", "breloc"]

        # Inlocuim tot ce iese in exterior sau scrie pe disk.
        self.patches = [
            mock.patch.object(main, "send_telegram_notification"),
            mock.patch.object(main, "send_watchlist_alert"),
            mock.patch.object(main, "record_alert"),
            mock.patch.object(main, "record_item_alert"),
            mock.patch.object(main, "record_item_reject"),
            mock.patch.object(main, "add_product"),
            mock.patch.object(main, "remove_stale_products"),
            # Fara astea, testele ar scrie in starea reala a botului si s-ar
            # influenta intre ele prin racirea de renotificare.
            mock.patch.object(main, "poate_notifica", return_value=True),
            mock.patch.object(main, "marcheaza_notificat"),
            mock.patch.object(main.time, "sleep"),
            # Clasa asta testeaza logica de watchlist, nu clasificatorul.
            # Fara asta ar filtra produsele de test si ar scrie in cache-ul real.
            mock.patch.object(main.monitor_state, "is_classifier_enabled",
                              return_value=False),
        ]
        self.m = {}
        for p in self.patches:
            nume = p.attribute
            self.m[nume] = p.start()

        # Contorul zilnic merge intr-un fisier care nu exista — deci mereu 0.
        self._counts_original = main.__dict__.get("_")
        from modules import watchlist as wl
        self._wl = wl
        self._fisier_original = wl.ALERT_COUNTS_FILE
        wl.ALERT_COUNTS_FILE = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_counts_inexistent.json")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self._wl.ALERT_COUNTS_FILE = self._fisier_original

    def _scaneaza(self, produse, watchlist_activ=True):
        site = {"name": KRIT, "url": "https://krit.ro/categorie/pokemon", "niche": "Pokemon TCG"}
        with mock.patch.object(main, "check_search_page_stock", return_value=produse):
            main.scaneaza_site(site, self.known, self.vip_groups, self.blacklist,
                               self.watchlist, watchlist_activ)

    # ── Ramura watchlist ──────────────────────────────────────
    def test_produs_profitabil_primeste_alerta_bogata(self):
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei")])

        self.assertEqual(self.m["send_watchlist_alert"].call_count, 1)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 0,
                         "nu trebuie sa plece si notificarea clasica — ar fi mesaj dublu")

        decizie = self.m["send_watchlist_alert"].call_args[0][0]
        self.assertAlmostEqual(decizie.net_profit_ron, 159.0, places=2)
        self.assertEqual(decizie.item_id, "pkm-30th-etb")

    def test_alerta_bogata_contorizeaza_performanta(self):
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei")])
        self.m["record_alert"].assert_called_once_with("pkm-30th-etb")
        self.m["record_item_alert"].assert_called_once()

    def test_cantitatea_ajunge_in_alerta(self):
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei", qty=6)])
        self.assertEqual(self.m["send_watchlist_alert"].call_args[1]["stock_qty"], 6)

    # ── Ramura clasica — nimic nu se pierde ───────────────────
    def test_produs_respins_cade_pe_fluxul_clasic(self):
        # 340 lei depaseste plafonul de 325 -> fara alerta de cumparare,
        # dar produsul TOT trebuie notificat ca produs nou in stoc.
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "340,00 lei")])

        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)
        self.m["record_item_reject"].assert_called_once()

    def test_produs_care_nu_e_pe_watchlist_merge_pe_fluxul_clasic(self):
        self._scaneaza([produs("Jucarie plus Pikachu 20cm", "49,99 lei")])

        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)
        self.m["record_item_reject"].assert_not_called()

    def test_watchlist_oprit_pastreaza_comportamentul_vechi(self):
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei")],
                       watchlist_activ=False)

        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)

    def test_pret_nedetectabil_nu_opreste_notificarea_clasica(self):
        self._scaneaza([produs("Pokemon 30th Celebration Elite Trainer Box", "N/A")])
        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)

    # ── Filtre existente, neatinse ────────────────────────────
    def test_blacklist_inca_filtreaza(self):
        self._scaneaza([produs("Pokemon Sleeve Protector", "29,99 lei")])
        self.assertEqual(self.m["send_telegram_notification"].call_count, 0)
        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)

    def test_produs_deja_cunoscut_nu_se_renotifica(self):
        p = produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei")
        self.known[KRIT] = {p["name"].strip().lower()}
        self._scaneaza([p])
        self.assertEqual(self.m["send_watchlist_alert"].call_count, 0)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 0)

    def test_mai_multe_produse_intr_o_scanare(self):
        self._scaneaza([
            produs("Pokemon 30th Celebration Elite Trainer Box", "289,00 lei"),  # alerta bogata
            produs("Jucarie plus Pikachu 20cm", "49,99 lei"),                     # flux clasic
            produs("Pokemon Sleeve Protector", "19,99 lei"),                      # blacklist
        ])
        self.assertEqual(self.m["send_watchlist_alert"].call_count, 1)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)


class TestGrupareNise(unittest.TestCase):

    def test_grupare_pe_nisa(self):
        sites = [
            {"name": "A", "niche": "Pokemon TCG"},
            {"name": "B", "niche": "LEGO"},
            {"name": "C", "niche": "Pokemon TCG"},
        ]
        grupuri = main.grupeaza_pe_nise(sites)
        self.assertEqual(sorted(grupuri.keys()), ["LEGO", "Pokemon TCG"])
        self.assertEqual(len(grupuri["Pokemon TCG"]), 2)

    def test_site_fara_nisa_ajunge_in_grup_comun(self):
        # Configurațiile vechi, fara "niche", trebuie sa mearga neschimbat.
        grupuri = main.grupeaza_pe_nise([{"name": "A"}, {"name": "B"}])
        self.assertEqual(list(grupuri.keys()), ["General"])
        self.assertEqual(len(grupuri["General"]), 2)

    def test_o_singura_nisa_inseamna_scanare_secventiala(self):
        sites = [{"name": "A", "niche": "Pokemon TCG"}, {"name": "B", "niche": "Pokemon TCG"}]
        grupuri = main.grupeaza_pe_nise(sites)
        paralel_efectiv = max(1, min(2, len(grupuri)))
        self.assertEqual(paralel_efectiv, 1)


class TestNisaNuCedeazaLaEroare(unittest.TestCase):

    def test_un_magazin_cazut_nu_opreste_restul_nisei(self):
        apeluri = []

        def scraper_fals(site):
            apeluri.append(site["name"])
            if site["name"] == "B":
                raise RuntimeError("site cazut")
            return []

        sites = [{"name": n, "niche": "X"} for n in ("A", "B", "C")]
        with mock.patch.object(main, "check_search_page_stock", side_effect=scraper_fals), \
             mock.patch.object(main.monitor_state, "is_muted", return_value=False), \
             mock.patch.object(main, "alert_site_failure"):
            main.scaneaza_nisa("X", sites, {}, [], [], {}, False)

        self.assertEqual(apeluri, ["A", "B", "C"],
                         "C trebuia scanat chiar daca B a aruncat exceptie")


class TestClasificatorInBucla(unittest.TestCase):
    """Fluxul complet: clasificare -> filtrare -> lista de blocate."""

    def setUp(self):
        import tempfile, shutil
        from modules import classifier, feedback, policy
        self.classifier, self.feedback, self.policy = classifier, feedback, policy

        self._dir = tempfile.mkdtemp(prefix="int_clf_")
        self._shutil = shutil
        self._orig = (classifier.CLASIFICARI_FILE, feedback.FEEDBACK_FILE,
                      classifier.NICHE_RULES_FILE,
                      policy.NICHE_POLICY_FILE, policy.SET_INTELLIGENCE_FILE)
        classifier.CLASIFICARI_FILE = os.path.join(self._dir, "clf.json")
        feedback.FEEDBACK_FILE = os.path.join(self._dir, "fb.json")

        import json as _json
        cale_reguli = os.path.join(self._dir, "reguli.json")
        with open(cale_reguli, "w", encoding="utf-8") as f:
            _json.dump({"Pokemon TCG": {"relevante": ["etb"], "ignorate": ["blister"]}}, f)
        classifier.NICHE_RULES_FILE = cale_reguli

        # Politica si registrul de seturi trebuie izolate la fel — altfel
        # testul depinde de tier-urile reale, care se schimba saptamanal.
        cale_pol = os.path.join(self._dir, "politica.json")
        with open(cale_pol, "w", encoding="utf-8") as f:
            _json.dump({"Pokemon TCG": {"urmareste": ["etb"], "ignora": ["blister"]}}, f)
        cale_set = os.path.join(self._dir, "seturi.json")
        with open(cale_set, "w", encoding="utf-8") as f:
            _json.dump({"Pokemon TCG": {}}, f)
        policy.NICHE_POLICY_FILE = cale_pol
        policy.SET_INTELLIGENCE_FILE = cale_set
        policy.reseteaza()

        classifier.reseteaza_cache()
        classifier.reseteaza_reguli()
        classifier.incarca_reguli(cale_reguli)
        feedback.reseteaza()

        self.patches = [
            mock.patch.object(main, "send_telegram_notification"),
            mock.patch.object(main, "send_watchlist_alert"),
            mock.patch.object(main, "record_alert"),
            mock.patch.object(main, "record_item_alert"),
            mock.patch.object(main, "record_item_reject"),
            mock.patch.object(main, "add_product"),
            mock.patch.object(main, "remove_stale_products"),
            mock.patch.object(main, "poate_notifica", return_value=True),
            mock.patch.object(main, "marcheaza_notificat"),
            mock.patch.object(main.time, "sleep"),
            mock.patch.object(main.monitor_state, "is_classifier_enabled", return_value=True),
        ]
        self.m = {}
        for p in self.patches:
            self.m[p.attribute] = p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        (self.classifier.CLASIFICARI_FILE, self.feedback.FEEDBACK_FILE,
         self.classifier.NICHE_RULES_FILE,
         self.policy.NICHE_POLICY_FILE, self.policy.SET_INTELLIGENCE_FILE) = self._orig
        self.policy.reseteaza()
        self.classifier.reseteaza_cache()
        self.classifier.reseteaza_reguli()
        self.feedback.reseteaza()
        self._shutil.rmtree(self._dir, ignore_errors=True)

    def _scaneaza(self, produse, verdicte):
        def gemini_fals(nume_lista, nisa, cheie):
            iesire = []
            for i, nume in enumerate(nume_lista):
                v = verdicte.get(nume, {"linie": "pokemon", "set": "", "tip": "altul"})
                iesire.append({"index": i, "relevant": True, **v})
            return iesire

        site = {"name": KRIT, "url": "https://krit.ro/x", "niche": "Pokemon TCG"}
        with mock.patch.object(main, "check_search_page_stock", return_value=produse),              mock.patch.object(self.classifier, "_apeleaza_gemini", side_effect=gemini_fals),              mock.patch.dict(os.environ, {"GEMINI_API_KEY": "fals"}):
            main.scaneaza_site(site, {}, [], [], {}, False)

    def test_produsul_irelevant_e_filtrat(self):
        nume = "Pokemon Blister Pack Oarecare"
        self._scaneaza([produs(nume, "29,99 lei")],
                       {nume: {"linie": "pokemon", "set": "x", "tip": "blister"}})
        self.assertEqual(self.m["send_telegram_notification"].call_count, 0,
                         "blisterul nu trebuia sa ajunga la tine")

    def test_produsul_relevant_trece(self):
        nume = "Pokemon Pitch Black Elite Trainer Box"
        self._scaneaza([produs(nume, "389,99 lei")],
                       {nume: {"linie": "pokemon", "set": "pitch black", "tip": "etb"}})
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1)
        # id-ul canonic ajunge la notifier, ca sa poata pune butoanele
        self.assertEqual(
            self.m["send_telegram_notification"].call_args[1]["id_canonic"],
            "pokemon|pitch-black|etb")

    def test_bad_apasat_blocheaza_in_toate_magazinele(self):
        self.feedback.inregistreaza("pokemon|pitch-black|etb", "bad")
        # Alt nume, alt magazin, dar acelasi produs canonic.
        nume = "Set carti de joc, Pokemon TCG, Pitch Black, Elite Trainer Box"
        self._scaneaza([produs(nume, "389,99 lei")],
                       {nume: {"linie": "pokemon", "set": "pitch black", "tip": "etb"}})
        self.assertEqual(self.m["send_telegram_notification"].call_count, 0,
                         "Bad pe id canonic trebuie sa blocheze si numele diferite")

    def test_fara_cheie_api_nu_se_pierde_nimic(self):
        nume = "Produs Oarecare"
        site = {"name": KRIT, "url": "https://krit.ro/x", "niche": "Pokemon TCG"}
        with mock.patch.object(main, "check_search_page_stock",
                               return_value=[produs(nume, "99 lei")]),              mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            main.scaneaza_site(site, {}, [], [], {}, False)
        self.assertEqual(self.m["send_telegram_notification"].call_count, 1,
                         "fara clasificare, produsul trebuie sa treaca pe fluxul vechi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
