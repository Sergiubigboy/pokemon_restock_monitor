"""
Teste pentru modules/price_parser.py

Rulare (fara dependente noi, doar biblioteca standard):
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.price_parser import parse_price_ron, format_ron


class TestCazuriObligatorii(unittest.TestCase):
    """Cazurile cerute explicit in brief."""

    CAZURI = [
        ("1.017,00 lei", 1017.0),
        ("599,99 RON", 599.99),
        ("Pret: 289 lei", 289.0),
        ("289", 289.0),
        ("1 017,00 lei", 1017.0),
        ("29,99", 29.99),
        ("N/A", None),
        ("", None),
        ("de la 199 lei", 199.0),
    ]

    def test_cazuri_din_brief(self):
        for text, asteptat in self.CAZURI:
            with self.subTest(text=text):
                self.assertEqual(parse_price_ron(text), asteptat)

    def test_pret_cu_diacritice(self):
        # Textul real de pe site are diacritice, parserul nu trebuie sa se incurce.
        self.assertEqual(parse_price_ron("Preț: 289 lei"), 289.0)
        self.assertEqual(parse_price_ron("Preț redus 1.017,00 lei"), 1017.0)


class TestRegulaSeparatorMii(unittest.TestCase):
    """
    Miezul ambiguitatii: punctul din "1.017" e separator de mii, nu zecimal.
    Regula: exact 3 cifre dupa ultimul separator, fara alt separator => mii.
    """

    def test_punct_cu_trei_cifre_este_mii(self):
        self.assertEqual(parse_price_ron("1.017"), 1017.0)
        self.assertEqual(parse_price_ron("12.345 lei"), 12345.0)

    def test_punct_cu_doua_cifre_este_zecimal(self):
        self.assertEqual(parse_price_ron("2.50 lei"), 2.5)
        self.assertEqual(parse_price_ron("289.90"), 289.90)

    def test_punct_cu_o_cifra_este_zecimal(self):
        self.assertEqual(parse_price_ron("99.5 lei"), 99.5)

    def test_virgula_cu_trei_cifre_este_tot_mii(self):
        # Niciun pret real din RO nu are 3 zecimale. Citirea "1,017 lei" ca
        # 1 leu ar declansa alerte false de profit urias — deci alegem 1017.
        self.assertEqual(parse_price_ron("1,017 lei"), 1017.0)

    def test_mii_multiple_acelasi_separator(self):
        self.assertEqual(parse_price_ron("1.017.500 lei"), 1017500.0)

    def test_mii_si_zecimale_impreuna(self):
        self.assertEqual(parse_price_ron("1.017.500,50 lei"), 1017500.50)

    def test_format_englezesc_ramane_corect(self):
        # Unele teme Shopify scot pretul in format EN. Ultimul separator castiga.
        self.assertEqual(parse_price_ron("1,017.00 lei"), 1017.0)

    def test_spatiu_ca_separator_de_mii(self):
        self.assertEqual(parse_price_ron("1 017 500,50 lei"), 1017500.50)

    def test_non_breaking_space_din_html(self):
        # Cel mai frecvent caracter invizibil din preturile scoase din HTML.
        self.assertEqual(parse_price_ron("1\xa0017,00\xa0lei"), 1017.0)
        self.assertEqual(parse_price_ron("599,99\xa0RON"), 599.99)


class TestValoriFaraPret(unittest.TestCase):
    """Tot ce trebuie sa dea None — adica 'pret nedetectabil'."""

    def test_none_si_gol(self):
        self.assertIsNone(parse_price_ron(None))
        self.assertIsNone(parse_price_ron(""))
        self.assertIsNone(parse_price_ron("   "))

    def test_texte_fara_cifre(self):
        for text in ("N/A", "-", "Stoc epuizat", "Indisponibil", "Pret la cerere", "lei"):
            with self.subTest(text=text):
                self.assertIsNone(parse_price_ron(text))

    def test_tipuri_gresite(self):
        self.assertIsNone(parse_price_ron(["289"]))
        self.assertIsNone(parse_price_ron({"pret": 289}))

    def test_zero_este_valoare_valida_nu_none(self):
        # 0 e diferit de None: inseamna "am citit pretul si e zero", nu
        # "selectorul s-a stricat". Motorul de decizie le trateaza diferit.
        self.assertEqual(parse_price_ron("0 lei"), 0.0)
        self.assertEqual(parse_price_ron("0,00 lei"), 0.0)


class TestTextMurdar(unittest.TestCase):
    """Cazuri reale de text prost extras din carduri de produs."""

    def test_pret_lipit_de_moneda(self):
        self.assertEqual(parse_price_ron("289lei"), 289.0)
        self.assertEqual(parse_price_ron("599,99RON"), 599.99)

    def test_prefixe_diverse(self):
        self.assertEqual(parse_price_ron("Pretul nostru: 1.017,00 lei"), 1017.0)
        self.assertEqual(parse_price_ron("incepand de la 199,00 lei"), 199.0)

    def test_doua_preturi_ia_primul(self):
        # Card cu pret taiat + pret redus. Luam primul (cel vechi, mai mare):
        # profitul estimat iese mai mic, deci nu generam alerte false.
        self.assertEqual(parse_price_ron("1.299,00 lei 999,00 lei"), 1299.0)

    def test_text_dupa_pret_nu_e_inghitit(self):
        self.assertEqual(parse_price_ron("289 lei / bucata"), 289.0)
        self.assertEqual(parse_price_ron("199 lei, 3 in stoc"), 199.0)

    def test_separator_final_ignorat(self):
        self.assertEqual(parse_price_ron("289. lei"), 289.0)
        self.assertEqual(parse_price_ron("289, lei"), 289.0)

    def test_separator_cu_spatii_in_jur(self):
        # Cazuri reale de pe Krit: pretul e spart in noduri HTML separate,
        # iar textul iese cu spatii in jurul punctului zecimal.
        self.assertEqual(parse_price_ron("19 . 99 Lei"), 19.99)
        self.assertEqual(parse_price_ron("1039 . 99 Lei"), 1039.99)
        self.assertEqual(parse_price_ron("599 . 99 Lei"), 599.99)
        self.assertEqual(parse_price_ron("1.017 , 00 lei"), 1017.0)

    def test_spatiul_de_mii_ramane_neatins(self):
        # Reparatia de mai sus nu trebuie sa strice separatorul de mii pe spatiu.
        self.assertEqual(parse_price_ron("1 017,00 lei"), 1017.0)
        self.assertEqual(parse_price_ron("1 017 500,50 lei"), 1017500.50)

    def test_pret_shopify_cu_original_si_current(self):
        # Tema Redgoblin scoate ambele preturi in acelasi nod. Luam primul
        # (pretul intreg), deci profitul estimat iese conservator.
        text = ("Original price 149,00 lei Original price 149,00 lei - "
                "Current price 134,10 lei")
        self.assertEqual(parse_price_ron(text), 149.0)

    def test_numere_deja_parsate(self):
        self.assertEqual(parse_price_ron(289), 289.0)
        self.assertEqual(parse_price_ron(289.5), 289.5)


class TestFormatRon(unittest.TestCase):
    """Helper de afisare folosit in mesajele Telegram."""

    def test_intreg_fara_zecimale(self):
        self.assertEqual(format_ron(1017.0), "1017")
        self.assertEqual(format_ron(289), "289")

    def test_zecimale_cu_virgula_romaneasca(self):
        self.assertEqual(format_ron(599.99), "599,99")

    def test_none(self):
        self.assertEqual(format_ron(None), "?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
