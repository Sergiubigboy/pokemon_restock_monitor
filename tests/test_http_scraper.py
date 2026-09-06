"""
Teste pentru fast-path-ul HTTP, in special filtrul de stoc epuizat.

Magazinele pe Gomag (cardxtcg.ro) afiseaza in categorie si produsele epuizate.
Daca le-am arunca din lista, o categorie complet epuizata ar intoarce 0 produse
si ar declansa alarma de "site cazut" la fiecare ciclu. Daca le-am tine minte,
revenirea lor in stoc n-ar mai fi un "produs nou" si n-ai primi alerta —
exact momentul pentru care exista botul.

Solutia testata aici: ies din scraper marcate cu in_stoc=False.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.http_scraper import _lista_text, check_search_page_stock_http


HTML = """
<html><body>
  <div class="product-box">
    <a class="title" href="/p/booster-box-op17.html">One Piece OP-17 Booster Box</a>
    <div class="price"><span class="text-main">624,99 RON</span></div>
    <div class="stockStatus">În stoc</div>
  </div>
  <div class="product-box">
    <a class="title" href="/p/booster-box-op16.html">One Piece OP-16 Booster Box</a>
    <div class="price"><span class="text-main">719,99 RON</span></div>
    <div class="stockStatus">Stoc epuizat</div>
  </div>
  <div class="product-box">
    <a class="title" href="/p/tin-pack.html">One Piece Tin Pack Set</a>
    <div class="price"><span class="text-main">74,99 RON</span></div>
    <div class="stockStatus">Indisponibil</div>
  </div>
</body></html>
"""

CONFIG = {
    "name": "Test Gomag",
    "url": "https://exemplu.ro/one-piece",
    "engine": "http",
    "card_selector": ".product-box",
    "title_selector": "a.title",
    "price_selector": ".price .text-main",
    "link_selector": "a.title",
    "out_of_stock_text": ["stoc epuizat", "indisponibil"],
}


class _Raspuns:
    status_code = 200
    text = HTML


class TestListaText(unittest.TestCase):
    """Configul accepta si un sir, si o lista — normalizarea e in _lista_text."""

    def test_sir_devine_lista(self):
        self.assertEqual(_lista_text("Stoc Epuizat"), ["stoc epuizat"])

    def test_lista_normalizata_si_curatata(self):
        self.assertEqual(
            _lista_text(["  Stoc epuizat ", "", "INDISPONIBIL"]),
            ["stoc epuizat", "indisponibil"],
        )

    def test_gol(self):
        self.assertEqual(_lista_text(None), [])
        self.assertEqual(_lista_text([]), [])


class TestStocEpuizat(unittest.TestCase):

    def _scaneaza(self, config):
        with patch("modules.http_scraper.requests.get", return_value=_Raspuns()):
            return check_search_page_stock_http(config)

    def test_epuizatele_raman_in_lista_dar_marcate(self):
        produse = self._scaneaza(CONFIG)
        # Toate trei ies din scraper: pagina s-a citit corect.
        self.assertEqual(len(produse), 3)
        stare = {p["name"]: p["in_stoc"] for p in produse}
        self.assertTrue(stare["One Piece OP-17 Booster Box"])
        self.assertFalse(stare["One Piece OP-16 Booster Box"])
        self.assertFalse(stare["One Piece Tin Pack Set"])

    def test_fara_filtru_totul_e_in_stoc(self):
        config = dict(CONFIG)
        config.pop("out_of_stock_text")
        produse = self._scaneaza(config)
        self.assertEqual(len(produse), 3)
        self.assertTrue(all(p["in_stoc"] for p in produse))

    def test_pretul_si_linkul_raman_corecte(self):
        produse = self._scaneaza(CONFIG)
        primul = produse[0]
        self.assertEqual(primul["price"], "624,99 RON")
        self.assertEqual(primul["url"], "https://exemplu.ro/p/booster-box-op17.html")


if __name__ == "__main__":
    unittest.main(verbosity=2)
