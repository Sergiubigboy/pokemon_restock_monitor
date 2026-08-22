"""
Teste pentru modules/watchlist.py

Testele folosesc un watchlist FIX, definit aici, nu config/watchlist.json.
Fisierul real e rescris saptamanal de agent — daca testele ar depinde de el,
s-ar strica singure in fiecare luni.

Rulare:
    python -m unittest discover -s tests -t . -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import watchlist as wl

AZI = date(2026, 8, 18)

KRIT = "Pokemon TCG - Krit"
LEXSHOP = "Pokemon TCG - Lexshop"
SMYK = "Pokemon TCG - SMYK"


def _watchlist_test() -> dict:
    """Watchlist minimal dar realist, calibrat pe exemplul din brief."""
    return {
        "_meta": {"valid_until": "2026-08-24T19:00:00+03:00"},
        "defaults": {
            "platform_fee_pct": 0.1,
            "shipping_cost_ron": 20,
            "min_profit_ron": 100,
            "min_roi_pct": 0.35,
            "min_liquidity_30d": 8,
            "max_alerts_per_item_per_day": 3,
            "stale_after_days": 14,
        },
        "items": [
            {
                "id": "pkm-30th-etb",
                "enabled": True,
                "tier": "S",
                "niche": "Pokemon TCG",
                "label": "30th Celebration - Elite Trainer Box",
                "match": {
                    "include_all": ["30th"],
                    "include_any": ["elite trainer", "etb"],
                    "exclude": ["sleeve", "binder", "album"],
                },
                "buy": {"max_price_ron": 325, "sites": [KRIT, LEXSHOP], "max_qty_per_drop": 4},
                "resale": {
                    "median_ron": 520,
                    "source": "cardmarket+vinted-ro",
                    "checked_at": "2026-08-17",
                    "liquidity_30d": 22,
                    "confidence": "medium",
                },
                "thresholds": {"min_profit_ron": 120, "min_roi_pct": 0.35},
                "expires_at": "2026-11-01",
            },
            {
                "id": "pkm-core-etb",
                "enabled": True,
                "tier": "A",
                "niche": "Pokemon TCG",
                "label": "Pokemon - ETB seturi curente",
                "match": {
                    "include_any": ["elite trainer box", "etb"],
                    "exclude": ["sleeve", "gol"],
                },
                "buy": {"max_price_ron": 290, "sites": [KRIT, LEXSHOP], "max_qty_per_drop": 3},
                "resale": {
                    "median_ron": 445,
                    "source": "vinted-ro+olx",
                    "checked_at": "2026-08-17",
                    "liquidity_30d": 31,
                    "confidence": "high",
                },
                "thresholds": {"min_profit_ron": 90, "min_roi_pct": 0.3},
                "expires_at": None,
            },
            {
                "id": "item-dezactivat",
                "enabled": False,
                "tier": "S",
                "niche": "Pokemon TCG",
                "label": "Item oprit de agent",
                "match": {"include_any": ["etb"]},
                "buy": {"max_price_ron": 900, "sites": [KRIT], "max_qty_per_drop": 1},
                "resale": {"median_ron": 5000, "checked_at": "2026-08-17", "liquidity_30d": 99},
                "thresholds": {"min_profit_ron": 0, "min_roi_pct": 0},
            },
            {
                "id": "lego-gwp",
                "enabled": True,
                "tier": "B",
                "niche": "LEGO",
                "label": "LEGO GWP",
                "match": {"include_any": ["gwp", "gift with purchase"]},
                "buy": {"max_price_ron": 0, "sites": [KRIT], "max_qty_per_drop": 2},
                "resale": {"median_ron": 260, "checked_at": "2026-08-17", "liquidity_30d": 9},
                "thresholds": {"min_profit_ron": 0, "min_roi_pct": 0},
            },
            {
                "id": "lichiditate-mica",
                "enabled": True,
                "tier": "B",
                "niche": "LEGO",
                "label": "LEGO EOL Icons",
                "match": {"include_any": ["10316"]},
                "buy": {"max_price_ron": 2105, "sites": [KRIT], "max_qty_per_drop": 1},
                "resale": {"median_ron": 3100, "checked_at": "2026-08-17", "liquidity_30d": 6},
                "thresholds": {"min_profit_ron": 400, "min_roi_pct": 0.3},
                "shipping_cost_ron": 50,
            },
            {
                "id": "date-expirate",
                "enabled": True,
                "tier": "A",
                "niche": "One Piece TCG",
                "label": "One Piece Display",
                "match": {"include_all": ["one piece"], "include_any": ["display"]},
                "buy": {"max_price_ron": 490, "sites": [KRIT], "max_qty_per_drop": 3},
                "resale": {
                    "median_ron": 790,
                    "checked_at": "2026-07-01",   # 48 de zile vechime fata de AZI
                    "liquidity_30d": 18,
                },
                "thresholds": {"min_profit_ron": 150, "min_roi_pct": 0.4},
            },
            {
                "id": "item-expirat",
                "enabled": True,
                "tier": "C",
                "niche": "Hot Wheels",
                "label": "Experiment Hot Wheels",
                "match": {"include_any": ["rlc", "red line club"]},
                "buy": {"max_price_ron": 160, "sites": [KRIT], "max_qty_per_drop": 5},
                "resale": {"median_ron": 290, "checked_at": "2026-08-17", "liquidity_30d": 12},
                "thresholds": {"min_profit_ron": 80, "min_roi_pct": 0.55},
                "expires_at": "2026-08-01",       # deja trecut fata de AZI
            },
        ],
    }


class BazaWatchlist(unittest.TestCase):
    """Redirecteaza contorul de alerte intr-un fisier temporar."""

    def setUp(self):
        self.wl = _watchlist_test()
        self._dir = tempfile.mkdtemp(prefix="wl_test_")
        self._fisier_original = wl.ALERT_COUNTS_FILE
        wl.ALERT_COUNTS_FILE = os.path.join(self._dir, "alert_counts.json")

    def tearDown(self):
        wl.ALERT_COUNTS_FILE = self._fisier_original
        shutil.rmtree(self._dir, ignore_errors=True)

    def produs(self, name, price="289,00 lei"):
        return {"name": name, "url": "https://exemplu.ro/p", "image": None, "price": price}


# ─────────────────────────────────────────────────────────────────
#  match_item
# ─────────────────────────────────────────────────────────────────
class TestMatchItem(BazaWatchlist):

    def test_potrivire_simpla(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], "pkm-30th-etb")

    def test_tier_mai_inalt_castiga(self):
        # Produsul se potriveste si pe pkm-30th-etb (S) si pe pkm-core-etb (A).
        item = wl.match_item("Pokemon 30th Celebration ETB", KRIT, self.wl)
        self.assertEqual(item["id"], "pkm-30th-etb")

    def test_fara_30th_cade_pe_tier_a(self):
        item = wl.match_item("Pokemon Scarlet & Violet Elite Trainer Box", KRIT, self.wl)
        self.assertEqual(item["id"], "pkm-core-etb")

    def test_include_all_lipsa_nu_potriveste(self):
        # "one piece" lipseste din nume, desi "display" e prezent.
        item = wl.match_item("Riftbound Booster Display", KRIT, self.wl)
        self.assertIsNone(item)

    def test_exclude_blocheaza(self):
        item = wl.match_item("Pokemon 30th Celebration ETB Sleeve", KRIT, self.wl)
        self.assertIsNone(item)

    def test_site_nepermis(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", SMYK, self.wl)
        self.assertIsNone(item)

    def test_item_dezactivat_este_sarit(self):
        # item-dezactivat e tier S si s-ar potrivi pe "etb", dar e enabled=false.
        # Nu trebuie sa fure potrivirea de la pkm-core-etb (tier A, activ).
        item = wl.match_item("Pokemon Surging Sparks ETB", KRIT, self.wl)
        self.assertEqual(item["id"], "pkm-core-etb")

    def test_diacritice_si_majuscule(self):
        item = wl.match_item("POKÉMON 30th CELEBRATION Élite Trainer Box", KRIT, self.wl)
        self.assertEqual(item["id"], "pkm-30th-etb")

    def test_granita_de_cuvant(self):
        # "etb" nu trebuie sa se potriveasca in interiorul altui cuvant.
        self.assertIsNone(wl.match_item("Pokemon Sketbook Colorat", KRIT, self.wl))
        # nici codul LEGO 10316 in 103160
        self.assertIsNone(wl.match_item("Set LEGO 103160 oarecare", KRIT, self.wl))
        self.assertIsNotNone(wl.match_item("Set LEGO 10316 Rivendell", KRIT, self.wl))

    def test_watchlist_gol_sau_invalid(self):
        self.assertIsNone(wl.match_item("orice", KRIT, {}))
        self.assertIsNone(wl.match_item("orice", KRIT, {"items": []}))

    def test_nume_gol(self):
        self.assertIsNone(wl.match_item("", KRIT, self.wl))


# ─────────────────────────────────────────────────────────────────
#  evaluate — cazul pozitiv
# ─────────────────────────────────────────────────────────────────
class TestEvaluatePozitiv(BazaWatchlist):

    def test_cifrele_din_exemplul_briefului(self):
        # 520 * 0.9 - 20 - 289 = 159 RON net, ROI 55%, 4 buc -> 636 RON
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        d = wl.evaluate(self.produs("Pokemon 30th Celebration Elite Trainer Box"), item, self.wl, azi=AZI)

        self.assertTrue(d.should_alert)
        self.assertEqual(d.kind, "BUY")
        self.assertEqual(d.reason, "")
        self.assertAlmostEqual(d.net_profit_ron, 159.0, places=2)
        self.assertAlmostEqual(d.roi_pct, 159.0 / 289.0, places=4)
        self.assertAlmostEqual(d.total_profit_ron, 636.0, places=2)
        self.assertEqual(d.max_qty, 4)
        self.assertEqual(d.price_ron, 289.0)
        self.assertEqual(d.resale_age_days, 1)
        self.assertEqual(d.liquidity_30d, 22)

    def test_pret_cu_separator_de_mii(self):
        item = wl.match_item("One Piece Booster Display OP-16", KRIT, self.wl)
        self.assertIsNotNone(item)
        # Itemul are date expirate, dar pretul trebuie citit corect oricum.
        d = wl.evaluate(self.produs("One Piece Booster Display OP-16", "1.017,00 lei"), item, self.wl, azi=AZI)
        self.assertEqual(d.price_ron, 1017.0)

    def test_shipping_specific_itemului_suprascrie_defaultul(self):
        item = wl.match_item("Set LEGO 10316 Rivendell", KRIT, self.wl)
        d = wl.evaluate(self.produs("Set LEGO 10316 Rivendell", "1.500 lei"), item, self.wl, azi=AZI)
        # shipping 50 (nu 20): 3100*0.9 - 50 - 1500 = 1240
        self.assertAlmostEqual(d.net_profit_ron, 1240.0, places=2)


# ─────────────────────────────────────────────────────────────────
#  evaluate — cazuri negative
# ─────────────────────────────────────────────────────────────────
class TestEvaluateNegativ(BazaWatchlist):

    def test_pret_peste_plafon(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        d = wl.evaluate(self.produs("Pokemon 30th Celebration Elite Trainer Box", "340 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("plafon", d.reason)

    def test_profit_sub_prag(self):
        # 520*0.9 - 20 - 330 = 118 < 120 (dar 330 e si peste plafonul 325,
        # deci verificam pe itemul de tier A: 445*0.9 - 20 - 285 = 95 > 90 ... )
        item = next(i for i in self.wl["items"] if i["id"] == "pkm-core-etb")
        # 445*0.9 - 20 - 290 = 90.5 -> peste prag; la 295 ar fi 85.5 -> sub prag,
        # dar 295 depaseste plafonul 290. Cream un caz curat scazand pragul.
        item = dict(item, thresholds={"min_profit_ron": 200, "min_roi_pct": 0.3})
        d = wl.evaluate(self.produs("Pokemon Surging Sparks ETB", "250 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("profit net", d.reason)

    def test_roi_sub_prag(self):
        item = next(i for i in self.wl["items"] if i["id"] == "pkm-core-etb")
        item = dict(item, thresholds={"min_profit_ron": 0, "min_roi_pct": 0.9})
        d = wl.evaluate(self.produs("Pokemon Surging Sparks ETB", "250 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("ROI", d.reason)

    def test_lichiditate_insuficienta(self):
        item = wl.match_item("Set LEGO 10316 Rivendell", KRIT, self.wl)
        d = wl.evaluate(self.produs("Set LEGO 10316 Rivendell", "1.500 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("lichiditate", d.reason)

    def test_date_de_revanzare_expirate(self):
        item = wl.match_item("One Piece Booster Display OP-16", KRIT, self.wl)
        d = wl.evaluate(self.produs("One Piece Booster Display OP-16", "300 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("vechi de 48 zile", d.reason)

    def test_item_expirat(self):
        item = next(i for i in self.wl["items"] if i["id"] == "item-expirat")
        d = wl.evaluate(self.produs("Hot Wheels RLC Camaro", "100 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("expirat", d.reason)

    def test_item_dezactivat_respins_si_de_evaluate(self):
        item = next(i for i in self.wl["items"] if i["id"] == "item-dezactivat")
        d = wl.evaluate(self.produs("Pokemon ETB", "100 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("dezactivat", d.reason)

    def test_item_eveniment_max_price_zero(self):
        item = wl.match_item("LEGO GWP Insiders Reward", KRIT, self.wl)
        d = wl.evaluate(self.produs("LEGO GWP Insiders Reward", "0 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertEqual(d.kind, "HEADS_UP")

    def test_pret_nedetectabil_nu_declanseaza_alerta(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        for pret_brut in ("N/A", "", "Stoc epuizat"):
            with self.subTest(pret=pret_brut):
                d = wl.evaluate(self.produs("Pokemon 30th Celebration Elite Trainer Box", pret_brut),
                                item, self.wl, azi=AZI)
                self.assertFalse(d.should_alert)
                self.assertIsNone(d.price_ron)
                self.assertIn("nedetectabil", d.reason)

    def test_pret_zero_nu_imparte_la_zero(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        d = wl.evaluate(self.produs("Pokemon 30th Celebration Elite Trainer Box", "0 lei"), item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("invalid", d.reason)


# ─────────────────────────────────────────────────────────────────
#  Contorul zilnic
# ─────────────────────────────────────────────────────────────────
class TestContorZilnic(BazaWatchlist):

    def test_limita_zilnica_blocheaza_dupa_n_alerte(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        p = self.produs("Pokemon 30th Celebration Elite Trainer Box")

        for trimise in range(3):
            d = wl.evaluate(p, item, self.wl, azi=AZI)
            self.assertTrue(d.should_alert, f"alerta {trimise + 1} ar trebui sa treaca")
            wl.record_alert("pkm-30th-etb", azi=AZI)

        d = wl.evaluate(p, item, self.wl, azi=AZI)
        self.assertFalse(d.should_alert)
        self.assertIn("limita zilnica", d.reason)

    def test_evaluate_nu_incrementeaza_contorul(self):
        item = wl.match_item("Pokemon 30th Celebration Elite Trainer Box", KRIT, self.wl)
        p = self.produs("Pokemon 30th Celebration Elite Trainer Box")
        for _ in range(10):
            wl.evaluate(p, item, self.wl, azi=AZI)
        self.assertEqual(wl.alerts_today("pkm-30th-etb", azi=AZI), 0)

    def test_contorul_se_reseteaza_a_doua_zi(self):
        wl.record_alert("pkm-30th-etb", azi=AZI)
        wl.record_alert("pkm-30th-etb", azi=AZI)
        self.assertEqual(wl.alerts_today("pkm-30th-etb", azi=AZI), 2)
        self.assertEqual(wl.alerts_today("pkm-30th-etb", azi=date(2026, 8, 19)), 0)

    def test_contorul_e_separat_pe_item(self):
        wl.record_alert("pkm-30th-etb", azi=AZI)
        self.assertEqual(wl.alerts_today("pkm-core-etb", azi=AZI), 0)

    def test_fisier_corupt_nu_arunca(self):
        with open(wl.ALERT_COUNTS_FILE, "w", encoding="utf-8") as f:
            f.write("{asta nu e json")
        self.assertEqual(wl.alerts_today("pkm-30th-etb", azi=AZI), 0)


# ─────────────────────────────────────────────────────────────────
#  load_watchlist
# ─────────────────────────────────────────────────────────────────
class TestLoadWatchlist(unittest.TestCase):

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="wl_load_")

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def _scrie(self, continut: str) -> str:
        cale = os.path.join(self._dir, "watchlist.json")
        with open(cale, "w", encoding="utf-8") as f:
            f.write(continut)
        return cale

    def test_fisier_lipsa_returneaza_dict_gol(self):
        self.assertEqual(wl.load_watchlist(os.path.join(self._dir, "nu_exista.json")), {})

    def test_json_invalid_returneaza_dict_gol(self):
        self.assertEqual(wl.load_watchlist(self._scrie("{ nu e json")), {})

    def test_structura_gresita_returneaza_dict_gol(self):
        self.assertEqual(wl.load_watchlist(self._scrie('{"defaults": {}}')), {})
        self.assertEqual(wl.load_watchlist(self._scrie('[1, 2, 3]')), {})

    def test_fisier_valid(self):
        cale = self._scrie(json.dumps(_watchlist_test(), ensure_ascii=False))
        incarcat = wl.load_watchlist(cale)
        self.assertEqual(len(incarcat["items"]), 7)

    def test_detecteaza_watchlist_expirat(self):
        incarcat = _watchlist_test()
        self.assertFalse(wl.watchlist_is_stale(incarcat, azi=date(2026, 8, 20)))
        self.assertTrue(wl.watchlist_is_stale(incarcat, azi=date(2026, 9, 1)))


class TestFisierulReal(unittest.TestCase):
    """
    Verificare de integritate pe config/watchlist.json real. Nu testeaza logica —
    doar prinde din timp cazul in care agentul saptamanal a scris un fisier stricat.
    """

    def test_fisierul_real_se_incarca(self):
        radacina = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cale = os.path.join(radacina, "config", "watchlist.json")
        if not os.path.exists(cale):
            self.skipTest("config/watchlist.json nu exista")

        incarcat = wl.load_watchlist(cale)
        self.assertTrue(incarcat, "watchlist-ul real nu s-a incarcat")
        for item in incarcat["items"]:
            with self.subTest(item=item.get("id")):
                self.assertIn("id", item)
                self.assertIn("buy", item)
                self.assertIn("resale", item)
                self.assertIsNotNone(wl._to_date(item["resale"].get("checked_at")),
                                     "resale.checked_at nu e o data ISO valida")


if __name__ == "__main__":
    unittest.main(verbosity=2)
