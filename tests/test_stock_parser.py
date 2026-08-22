"""
Teste pentru modules/stock_parser.py

Cantitatea decide cate bucati cumperi, deci o cifra inventata e mai
periculoasa decat lipsa ei. Testele insista pe cazurile care trebuie sa dea
None.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.stock_parser import parse_stock_qty


class TestCantitatiValide(unittest.TestCase):

    def test_formate_uzuale(self):
        cazuri = [
            ("6 buc in stoc", 6),
            ("6 bucati", 6),
            ("12 produse", 12),
            ("3 articole", 3),
            ("Stoc: 6", 6),
            ("stoc disponibil: 12", 12),
            ("In stoc 4", 4),
            ("Ultimele 3 produse", 3),
            ("ultimul 1 produs", 1),
            ("Disponibil: 8", 8),
            ("Cantitate: 15", 15),
        ]
        for text, asteptat in cazuri:
            with self.subTest(text=text):
                self.assertEqual(parse_stock_qty(text), asteptat)

    def test_diacritice(self):
        self.assertEqual(parse_stock_qty("6 bucăți în stoc"), 6)
        self.assertEqual(parse_stock_qty("Ultimele 2 bucăți"), 2)

    def test_majuscule(self):
        self.assertEqual(parse_stock_qty("STOC: 7"), 7)
        self.assertEqual(parse_stock_qty("6 BUC"), 6)

    def test_numar_intreg_direct(self):
        self.assertEqual(parse_stock_qty(6), 6)


class TestFaraCantitate(unittest.TestCase):
    """Toate astea trebuie sa dea None — mai bine lipsa decat o cifra gresita."""

    def test_stoc_fara_numar(self):
        for text in ("In stoc", "Disponibil", "Adauga in cos", "Pe stoc"):
            with self.subTest(text=text):
                self.assertIsNone(parse_stock_qty(text))

    def test_gol_si_none(self):
        self.assertIsNone(parse_stock_qty(None))
        self.assertIsNone(parse_stock_qty(""))
        self.assertIsNone(parse_stock_qty("   "))

    def test_tipuri_gresite(self):
        self.assertIsNone(parse_stock_qty(["6"]))
        self.assertIsNone(parse_stock_qty({"qty": 6}))

    def test_zero_nu_e_cantitate_utila(self):
        # "0 bucati" inseamna indisponibil, nu o cantitate de cumparat.
        self.assertIsNone(parse_stock_qty("0 bucati"))

    def test_numere_absurde_ignorate(self):
        # Coduri de produs si ani nu sunt cantitati.
        self.assertIsNone(parse_stock_qty("Cod produs 123456"))
        self.assertIsNone(parse_stock_qty("Editia 2026"))

    def test_pretul_nu_e_confundat_cu_stocul(self):
        # Textul unui card contine si pretul. Fara un cuvant-cheie de stoc,
        # nu extragem nimic.
        self.assertIsNone(parse_stock_qty("289,00 lei"))
        self.assertIsNone(parse_stock_qty("Pokemon ETB 1.017,00 lei"))


class TestTextDeCard(unittest.TestCase):
    """Textul complet al unui card de produs, asa cum vine din scraper."""

    def test_card_cu_stoc_explicit(self):
        text = "Pokemon TCG 30th Celebration ETB 289,00 lei 6 buc in stoc Adauga in cos"
        self.assertEqual(parse_stock_qty(text), 6)

    def test_card_fara_stoc_explicit(self):
        text = "Pokemon TCG 30th Celebration ETB 289,00 lei In stoc Adauga in cos"
        self.assertIsNone(parse_stock_qty(text))


if __name__ == "__main__":
    unittest.main(verbosity=2)
