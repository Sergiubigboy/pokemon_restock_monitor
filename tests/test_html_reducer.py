"""
Teste pentru modules/html_reducer.py

Regresia importanta: pe pagina reala de la Krit, detectorul alegea sectiunea
de FAQ in locul grilei de produse, pentru ca si FAQ-ul are multe elemente
identice cu text lung. Gemini primea fragmentul gresit si raspundea corect
ca "nu sunt produse aici". Testele de mai jos blocheaza intoarcerea bug-ului.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.html_reducer import curata_html, extrage_fragment, gaseste_grila


def _pagina(cu_faq=True):
    """Pagina cu o grila de 3 produse si un FAQ cu text mai lung."""
    produse = "".join(
        f'<div class="product">'
        f'<a href="/produs/{i}"><img src="/img/{i}.jpg"/>'
        f'<div class="product-title">Produs Pokemon numarul {i}</div>'
        f'<div class="price">{i}99,00 lei</div></a></div>'
        for i in range(1, 4)
    )
    faq = "".join(
        f'<div class="faq-item"><h3>Intrebarea {i} despre livrare</h3>'
        f'<p>{"Text foarte lung de raspuns la intrebare. " * 8}</p></div>'
        for i in range(1, 6)
    ) if cu_faq else ""

    return f"""<html><head><title>x</title></head><body>
        <nav><a href="/a">Acasa</a><a href="/b">Cont</a><a href="/c">Cos</a></nav>
        <div class="product-grid">{produse}</div>
        <div class="faq">{faq}</div>
        <script>var tracking = 1;</script>
        <style>.x {{ color: red; }}</style>
    </body></html>"""


class TestCuratare(unittest.TestCase):

    def test_scoate_script_si_style(self):
        supa = curata_html(_pagina())
        self.assertIsNone(supa.find("script"))
        self.assertIsNone(supa.find("style"))

    def test_pastreaza_clasele_si_linkurile(self):
        supa = curata_html(_pagina())
        card = supa.select_one(".product")
        self.assertIsNotNone(card)
        self.assertIsNotNone(card.select_one("a[href]"))

    def test_scoate_atributele_inutile(self):
        supa = curata_html('<div class="p" onclick="hack()" style="color:red">x</div>')
        element = supa.select_one(".p")
        self.assertNotIn("onclick", element.attrs)
        self.assertNotIn("style", element.attrs)
        self.assertIn("class", element.attrs)


class TestDetectareGrila(unittest.TestCase):

    def test_alege_produsele_nu_faq_ul(self):
        # Miezul regresiei: FAQ-ul are 5 elemente cu text lung, grila are doar
        # 3 produse cu text scurt. Fara semnalele de pret/imagine/link, FAQ-ul
        # ar castiga.
        supa = curata_html(_pagina(cu_faq=True))
        parinte, _, repetitii = gaseste_grila(supa)
        self.assertIn("product-grid", parinte.get("class"))
        self.assertEqual(repetitii, 3)

    def test_ignora_navigatia(self):
        # Meniul are 3 linkuri repetate, dar text prea putin.
        supa = curata_html(_pagina(cu_faq=False))
        parinte, _, _ = gaseste_grila(supa)
        self.assertNotEqual(parinte.name, "nav")

    def test_pagina_fara_grila(self):
        supa = curata_html("<html><body><p>Nimic aici</p></body></html>")
        self.assertIsNone(gaseste_grila(supa))


class TestExtragereFragment(unittest.TestCase):

    def test_reduce_dimensiunea(self):
        html = _pagina()
        fragment, carduri = extrage_fragment(html)
        self.assertEqual(carduri, 3)
        self.assertLess(len(fragment), len(html))

    def test_fragmentul_contine_datele_necesare(self):
        fragment, _ = extrage_fragment(_pagina())
        for asteptat in ("product-title", "price", "lei", "href", "img"):
            with self.subTest(asteptat=asteptat):
                self.assertIn(asteptat, fragment)

    def test_fara_grila_cade_pe_html_taiat(self):
        fragment, carduri = extrage_fragment("<html><body><p>Nimic</p></body></html>")
        self.assertEqual(carduri, 0)
        self.assertIn("Nimic", fragment)

    def test_respecta_limita_de_caractere(self):
        fragment, _ = extrage_fragment(_pagina(), max_caractere=200)
        self.assertLessEqual(len(fragment), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
