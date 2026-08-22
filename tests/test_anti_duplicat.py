"""
Teste pentru protectia anti-duplicat.

Motivatia e masurata pe feed-ul real: din 33 de notificari primite, doar 17
erau produse distincte. 48% era zgomot pur, cauzat de magazine care intorc
uneori doar o parte din produse.

Reproducem exact tiparul asta.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import state_manager as sm

SITE = "Pokemon TCG - PokeMANIA"


class BazaStare(unittest.TestCase):
    """Redirecteaza toate fisierele de stare in directoare temporare."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="stare_test_")
        self._originale = (sm.KNOWN_PRODUCTS_FILE, sm.HISTORICAL_PRODUCTS_FILE,
                           sm.ABSENCE_FILE, sm.LAST_NOTIFIED_FILE)
        sm.KNOWN_PRODUCTS_FILE      = os.path.join(self._dir, "known.json")
        sm.HISTORICAL_PRODUCTS_FILE = os.path.join(self._dir, "hist.json")
        sm.ABSENCE_FILE             = os.path.join(self._dir, "absente.json")
        sm.LAST_NOTIFIED_FILE       = os.path.join(self._dir, "notificat.json")

    def tearDown(self):
        (sm.KNOWN_PRODUCTS_FILE, sm.HISTORICAL_PRODUCTS_FILE,
         sm.ABSENCE_FILE, sm.LAST_NOTIFIED_FILE) = self._originale
        shutil.rmtree(self._dir, ignore_errors=True)


class TestContorAbsente(BazaStare):

    def test_o_absenta_nu_sterge_nimic(self):
        known = {SITE: {"produs a", "produs b", "produs c"}}
        sm.remove_stale_products(known, SITE, {"produs a"})
        # b si c lipsesc, dar doar o data — raman in memorie.
        self.assertEqual(known[SITE], {"produs a", "produs b", "produs c"})

    def test_trei_absente_consecutive_sterg(self):
        known = {SITE: {"produs a", "produs b"}}
        for _ in range(3):
            sm.remove_stale_products(known, SITE, {"produs a"})
        self.assertEqual(known[SITE], {"produs a"})

    def test_reaparitia_reseteaza_contorul(self):
        known = {SITE: {"produs a", "produs b"}}
        sm.remove_stale_products(known, SITE, {"produs a"})           # b lipsa 1
        sm.remove_stale_products(known, SITE, {"produs a"})           # b lipsa 2
        sm.remove_stale_products(known, SITE, {"produs a", "produs b"})  # b revine
        sm.remove_stale_products(known, SITE, {"produs a"})           # b lipsa 1 din nou
        sm.remove_stale_products(known, SITE, {"produs a"})           # b lipsa 2
        self.assertIn("produs b", known[SITE], "contorul trebuia resetat la reaparitie")

    def test_scenariul_real_de_oscilatie(self):
        """
        Exact tiparul din feed: magazinul intoarce 20 de produse, apoi 3, apoi
        iar 20. Fara contor, cele 17 ar fi purjate si renotificate.
        """
        toate = {f"produs {i}" for i in range(20)}
        putine = {f"produs {i}" for i in range(3)}
        known = {SITE: set(toate)}

        sm.remove_stale_products(known, SITE, putine)   # scanare partiala
        self.assertEqual(len(known[SITE]), 20, "nu trebuie sa piarda nimic dupa o oscilatie")

        sm.remove_stale_products(known, SITE, toate)    # revine la normal
        self.assertEqual(len(known[SITE]), 20)

    def test_disparitie_reala_ajunge_sa_stearga(self):
        """Un produs chiar epuizat trebuie sa dispara pana la urma."""
        known = {SITE: {"produs a", "epuizat"}}
        for _ in range(3):
            sm.remove_stale_products(known, SITE, {"produs a"})
        self.assertNotIn("epuizat", known[SITE])

    def test_prag_configurabil(self):
        known = {SITE: {"a", "b"}}
        sm.remove_stale_products(known, SITE, {"a"}, min_absente=1)
        self.assertEqual(known[SITE], {"a"})

    def test_site_necunoscut_nu_arunca(self):
        self.assertEqual(sm.remove_stale_products({}, "Site Inexistent", {"x"}), set())


class TestRacireRenotificare(BazaStare):

    def test_prima_notificare_e_permisa(self):
        self.assertTrue(sm.poate_notifica(SITE, "produs nou"))

    def test_a_doua_notificare_imediata_e_blocata(self):
        sm.marcheaza_notificat(SITE, "elite trainer box destined rivals")
        self.assertFalse(sm.poate_notifica(SITE, "elite trainer box destined rivals"))

    def test_racirea_expira(self):
        sm.marcheaza_notificat(SITE, "produs")
        # Cu racire 0 secunde, notificarea e din nou permisa.
        self.assertTrue(sm.poate_notifica(SITE, "produs", racire=0))

    def test_magazine_diferite_sunt_independente(self):
        sm.marcheaza_notificat(SITE, "acelasi produs")
        self.assertTrue(sm.poate_notifica("Pokemon TCG - Krit", "acelasi produs"))

    def test_produse_diferite_sunt_independente(self):
        sm.marcheaza_notificat(SITE, "produs a")
        self.assertTrue(sm.poate_notifica(SITE, "produs b"))

    def test_intrarile_vechi_se_curata(self):
        sm.marcheaza_notificat(SITE, "vechi")
        # Imbatranim artificial intrarea peste limita de pastrare.
        import json
        cale = sm.LAST_NOTIFIED_FILE
        date = json.load(open(cale, encoding="utf-8"))
        for k in date:
            date[k] = time.time() - sm.VECHIME_MAXIMA_NOTIFICARI - 10
        json.dump(date, open(cale, "w", encoding="utf-8"))

        sm.marcheaza_notificat(SITE, "nou")
        date = json.load(open(cale, encoding="utf-8"))
        self.assertEqual(len(date), 1, "intrarea veche trebuia aruncata")
        self.assertIn(f"{SITE}||nou", date)

    def test_fisier_corupt_nu_blocheaza_notificarile(self):
        with open(sm.LAST_NOTIFIED_FILE, "w", encoding="utf-8") as f:
            f.write("{asta nu e json")
        self.assertTrue(sm.poate_notifica(SITE, "produs"))


class TestScriereAtomica(BazaStare):

    def test_nu_ramane_fisier_temporar(self):
        sm.save_known_products({SITE: {"a", "b"}})
        ramase = [f for f in os.listdir(self._dir) if f.endswith(".tmp")]
        self.assertEqual(ramase, [], "fisierele .tmp trebuie mutate, nu lasate in urma")

    def test_datele_se_citesc_inapoi(self):
        sm.save_known_products({SITE: {"produs a", "produs b"}})
        self.assertEqual(sm.load_known_products()[SITE], {"produs a", "produs b"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
