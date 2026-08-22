"""
Teste pentru modules/classifier.py si modules/feedback.py

Apelul catre Gemini e inlocuit cu un dublu — testele nu ating reteaua si nu
consuma niciun token.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import classifier, feedback

REGULI_TEST = {
    "Pokemon TCG": {
        "relevante": ["etb", "booster_box", "booster_pack"],
        "ignorate": ["blister", "tin", "single_card"],
        "nota": "test",
    },
    "LEGO": {
        "relevante": ["set_lego", "gwp"],
        "ignorate": ["accesoriu"],
    },
}


class BazaClasificator(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="clf_test_")
        self._cache_original = classifier.CLASIFICARI_FILE
        self._feedback_original = feedback.FEEDBACK_FILE
        classifier.CLASIFICARI_FILE = os.path.join(self._dir, "clasificari.json")
        feedback.FEEDBACK_FILE = os.path.join(self._dir, "feedback.json")
        classifier.reseteaza_cache()
        classifier.reseteaza_reguli()
        feedback.reseteaza()

        cale_reguli = os.path.join(self._dir, "reguli.json")
        with open(cale_reguli, "w", encoding="utf-8") as f:
            json.dump(REGULI_TEST, f)
        classifier.NICHE_RULES_FILE = cale_reguli
        classifier.incarca_reguli(cale_reguli)

    def tearDown(self):
        classifier.CLASIFICARI_FILE = self._cache_original
        feedback.FEEDBACK_FILE = self._feedback_original
        classifier.reseteaza_cache()
        classifier.reseteaza_reguli()
        feedback.reseteaza()
        shutil.rmtree(self._dir, ignore_errors=True)


def _raspuns_fals(verdicte):
    """Construieste un dublu pentru _apeleaza_gemini."""
    def fals(nume_lista, nisa, cheie):
        iesire = []
        for i, nume in enumerate(nume_lista):
            v = verdicte.get(nume, {"linie": "pokemon", "set": "", "tip": "altul"})
            iesire.append({"index": i, "relevant": True, **v})
        return iesire
    return fals


class TestClasificare(BazaClasificator):

    def test_verdict_relevant(self):
        fals = _raspuns_fals({
            "Pokemon TCG Pitch Black Elite Trainer Box":
                {"linie": "pokemon", "set": "pitch black", "tip": "etb"},
        })
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            rez = classifier.clasifica(["Pokemon TCG Pitch Black Elite Trainer Box"],
                                       "Pokemon TCG", cheie_api="fals")
        v = rez["Pokemon TCG Pitch Black Elite Trainer Box"]
        self.assertTrue(v["relevant"])
        self.assertEqual(v["tip"], "etb")
        self.assertEqual(v["id_canonic"], "pokemon|pitch-black|etb")

    def test_tip_ignorat_devine_irelevant(self):
        fals = _raspuns_fals({
            "Pokemon Blister Pack": {"linie": "pokemon", "set": "", "tip": "blister"},
        })
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            rez = classifier.clasifica(["Pokemon Blister Pack"], "Pokemon TCG", cheie_api="fals")
        self.assertFalse(rez["Pokemon Blister Pack"]["relevant"])

    def test_relevanta_e_recalculata_nu_luata_de_la_model(self):
        """
        Modelul spune relevant=True pentru un tip ignorat. Noi recalculam din
        regulile nisei, ca sa nu poata inventa relevanta.
        """
        def fals(nume_lista, nisa, cheie):
            return [{"index": 0, "linie": "pokemon", "set": "x",
                     "tip": "single_card", "relevant": True}]
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            rez = classifier.clasifica(["Carte single Charizard"], "Pokemon TCG", cheie_api="fals")
        self.assertFalse(rez["Carte single Charizard"]["relevant"])

    def test_id_canonic_identic_pentru_nume_diferite(self):
        """Miezul designului: acelasi produs, doua magazine, acelasi id."""
        nume_noriel = "Set carti de joc, Pokemon TCG, Mega Evolution, Pitch Black, Elite Trainer Box"
        nume_krit = "Pokemon TCG: ME05 - Pitch Black - Elite Trainer Box"
        fals = _raspuns_fals({
            nume_noriel: {"linie": "pokemon", "set": "pitch black", "tip": "etb"},
            nume_krit:   {"linie": "pokemon", "set": "pitch black", "tip": "etb"},
        })
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            rez = classifier.clasifica([nume_noriel, nume_krit], "Pokemon TCG", cheie_api="fals")
        self.assertEqual(rez[nume_noriel]["id_canonic"], rez[nume_krit]["id_canonic"])

    def test_fara_cheie_api_da_necunoscut(self):
        rez = classifier.clasifica(["Orice produs"], "Pokemon TCG", cheie_api="")
        v = rez["Orice produs"]
        self.assertIsNone(v["relevant"], "necunoscut trebuie sa fie None, nu False")
        self.assertEqual(v["tip"], "necunoscut")

    def test_eroare_de_retea_da_necunoscut(self):
        with mock.patch.object(classifier, "_apeleaza_gemini",
                               side_effect=RuntimeError("retea cazuta")):
            rez = classifier.clasifica(["Produs"], "Pokemon TCG", cheie_api="fals")
        self.assertIsNone(rez["Produs"]["relevant"])

    def test_lista_goala(self):
        self.assertEqual(classifier.clasifica([], "Pokemon TCG"), {})


class TestCache(BazaClasificator):

    def test_al_doilea_apel_nu_mai_intreaba_modelul(self):
        fals = _raspuns_fals({"Produs X": {"linie": "pokemon", "set": "y", "tip": "etb"}})
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals) as dublu:
            classifier.clasifica(["Produs X"], "Pokemon TCG", cheie_api="fals")
            classifier.clasifica(["Produs X"], "Pokemon TCG", cheie_api="fals")
            classifier.clasifica(["Produs X"], "Pokemon TCG", cheie_api="fals")
        self.assertEqual(dublu.call_count, 1, "cache-ul trebuia sa evite apelurile 2 si 3")

    def test_cache_ul_persista_pe_disk(self):
        fals = _raspuns_fals({"Produs Y": {"linie": "pokemon", "set": "z", "tip": "etb"}})
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            classifier.clasifica(["Produs Y"], "Pokemon TCG", cheie_api="fals")

        classifier.reseteaza_cache()   # simulam repornirea botului
        self.assertIsNotNone(classifier.din_cache("Produs Y", "Pokemon TCG"))

    def test_necunoscutul_nu_se_pune_in_cache(self):
        """Altfel o cadere de retea ar otravi cache-ul permanent."""
        with mock.patch.object(classifier, "_apeleaza_gemini",
                               side_effect=RuntimeError("cazut")):
            classifier.clasifica(["Produs Z"], "Pokemon TCG", cheie_api="fals")
        self.assertIsNone(classifier.din_cache("Produs Z", "Pokemon TCG"))

    def test_nise_diferite_au_intrari_separate(self):
        fals = _raspuns_fals({"Produs": {"linie": "lego", "set": "", "tip": "set_lego"}})
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals):
            classifier.clasifica(["Produs"], "LEGO", cheie_api="fals")
        self.assertIsNotNone(classifier.din_cache("Produs", "LEGO"))
        self.assertIsNone(classifier.din_cache("Produs", "Pokemon TCG"))

    def test_numele_se_normalizeaza_in_cheie(self):
        fals = _raspuns_fals({"Produs Test": {"linie": "pokemon", "set": "", "tip": "etb"}})
        with mock.patch.object(classifier, "_apeleaza_gemini", side_effect=fals) as dublu:
            classifier.clasifica(["Produs Test"], "Pokemon TCG", cheie_api="fals")
            classifier.clasifica(["  PRODUS   TEST  "], "Pokemon TCG", cheie_api="fals")
        self.assertEqual(dublu.call_count, 1, "diferentele de spatii/majuscule nu sunt produse noi")


class TestFeedback(BazaClasificator):

    def test_bad_blocheaza(self):
        feedback.inregistreaza("pokemon|pitch-black|etb", "bad")
        self.assertTrue(feedback.este_respins("pokemon|pitch-black|etb"))

    def test_good_nu_blocheaza(self):
        feedback.inregistreaza("pokemon|chaos-rising|etb", "good")
        self.assertFalse(feedback.este_respins("pokemon|chaos-rising|etb"))

    def test_produs_necunoscut_nu_e_blocat(self):
        self.assertFalse(feedback.este_respins("ceva|ce|nu-exista"))
        self.assertFalse(feedback.este_respins(""))

    def test_te_poti_razgandi(self):
        feedback.inregistreaza("pokemon|x|etb", "bad")
        self.assertTrue(feedback.este_respins("pokemon|x|etb"))
        feedback.inregistreaza("pokemon|x|etb", "good")
        self.assertFalse(feedback.este_respins("pokemon|x|etb"))

    def test_token_incape_in_callback_data(self):
        # Telegram limiteaza callback_data la 64 de octeti; noi trimitem "v:b:" + token.
        id_lung = "pokemon|" + "un-set-cu-nume-foarte-foarte-lung" * 3 + "|booster-box"
        token = feedback.token_pentru(id_lung)
        callback_data = "v:b:" + token
        self.assertLessEqual(len(callback_data.encode("utf-8")), 64)

    def test_tokenul_reface_id_ul(self):
        id_canonic = "pokemon|pitch-black|etb"
        token = feedback.token_pentru(id_canonic)
        self.assertEqual(feedback.id_dupa_token(token), id_canonic)

    def test_tokenul_e_stabil(self):
        a = feedback.token_pentru("pokemon|x|etb")
        b = feedback.token_pentru("pokemon|x|etb")
        self.assertEqual(a, b)

    def test_token_inexistent(self):
        self.assertIsNone(feedback.id_dupa_token("nuexista"))

    def test_deblocare(self):
        feedback.inregistreaza("pokemon|pitch-black|etb", "bad")
        feedback.inregistreaza("pokemon|pitch-black|booster-pack", "bad")
        feedback.inregistreaza("pokemon|chaos-rising|etb", "bad")

        deblocate = feedback.deblocheaza("pitch-black")
        self.assertEqual(len(deblocate), 2)
        self.assertFalse(feedback.este_respins("pokemon|pitch-black|etb"))
        self.assertTrue(feedback.este_respins("pokemon|chaos-rising|etb"))

    def test_deblocare_fara_potrivire(self):
        self.assertEqual(feedback.deblocheaza("nimic"), [])
        self.assertEqual(feedback.deblocheaza(""), [])

    def test_verdictele_persista(self):
        feedback.inregistreaza("pokemon|x|etb", "bad")
        feedback.reseteaza()   # simulam repornirea
        self.assertTrue(feedback.este_respins("pokemon|x|etb"))

    def test_fisier_corupt_nu_arunca(self):
        with open(feedback.FEEDBACK_FILE, "w", encoding="utf-8") as f:
            f.write("{stricat")
        feedback.reseteaza()
        self.assertFalse(feedback.este_respins("orice"))


class TestIdNuPoateFiCategorie(BazaClasificator):
    """
    Regresie 19 august 2026 — bug care a costat produse.

    Cand setul nu era recunoscut, id-ul canonic iesea "pokemon|booster-box":
    o CATEGORIE, nu un produs. Un Bad apasat pe o cutie chinezeasca a blocat
    toate booster box-urile Pokemon cu set nerecunoscut. Pierdere tacuta.
    """

    def test_id_are_mereu_trei_bucati(self):
        for nume in ("Pokemon Gem Pack Vol 6 Booster Box (S-CHN)",
                     "Ceva complet necunoscut Booster Box",
                     "Produs fara set Elite Trainer Box",
                     "xyz"):
            with self.subTest(nume=nume):
                v = classifier.clasifica_local(nume, "Pokemon TCG")
                bucati = v["id_canonic"].split("|")
                self.assertGreaterEqual(len(bucati), 3,
                    f"id '{v['id_canonic']}' e o categorie, nu un produs")

    def test_produse_diferite_fara_set_au_id_diferit(self):
        a = classifier.clasifica_local("Pokemon Gem Pack Vol 6 Booster Box", "Pokemon TCG")
        b = classifier.clasifica_local("Pokemon Blade Awakening Booster Box", "Pokemon TCG")
        self.assertNotEqual(a["id_canonic"], b["id_canonic"],
            "doua produse diferite nu au voie sa imparta acelasi id")

    def test_feedback_refuza_id_de_categorie(self):
        self.assertIsNone(feedback.inregistreaza("pokemon|booster-box", "bad"))
        self.assertFalse(feedback.este_respins("pokemon|booster-box"))

    def test_feedback_accepta_id_de_produs(self):
        self.assertIsNotNone(feedback.inregistreaza("pokemon|pitch-black|etb", "bad"))
        self.assertTrue(feedback.este_respins("pokemon|pitch-black|etb"))

    def test_blocarea_unui_produs_nu_afecteaza_alt_set(self):
        chinezesc = classifier.clasifica_local(
            "Pokemon Gem Pack Vol 6 Booster Box (S-CHN) - Editie Chineza", "Pokemon TCG")
        bun = classifier.clasifica_local(
            "Pokemon TCG 30th Celebration Booster Box", "Pokemon TCG")
        feedback.inregistreaza(chinezesc["id_canonic"], "bad")
        self.assertFalse(feedback.este_respins(bun["id_canonic"]),
            "blocarea unei cutii chinezesti nu are voie sa taie 30th Celebration")


class TestEditii(BazaClasificator):

    def test_editiile_asiatice_sunt_detectate(self):
        cazuri = [
            ("Pokemon Gem Pack Vol 6 Booster Box (S-CHN) - Editie Chineza", "chineza"),
            ("Pokemon Blade Awakening CSV7 Slim Booster Box - Editie Chinezeasca", "chineza"),
            ("Pokemon JP - Abyss Eye - Booster Box", "japoneza"),
        ]
        for nume, asteptat in cazuri:
            with self.subTest(nume=nume):
                self.assertEqual(classifier.detecteaza_editie_local(nume), asteptat)

    def test_produsele_normale_raman_standard(self):
        for nume in ("Pokemon TCG 30th Celebration Booster Box",
                     "Elite Trainer Box Pokemon TCG Destined Rivals"):
            with self.subTest(nume=nume):
                self.assertEqual(classifier.detecteaza_editie_local(nume), "standard")


class TestCollectorVsPlay(BazaClasificator):
    """MTG: marja e in Collector Boosters, nu in Play Boosters."""

    def test_tipuri_distincte(self):
        self.assertEqual(
            classifier.detecteaza_tip_local("MTG The Hobbit Collector Booster Box"),
            "collector_booster_box")
        self.assertEqual(
            classifier.detecteaza_tip_local("MTG The Hobbit Play Booster Box"),
            "play_booster_box")


if __name__ == "__main__":
    unittest.main(verbosity=2)
