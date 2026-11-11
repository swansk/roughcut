"""The Index panel for a finer look (wave 27, GRAIN): nothing selected says "all N clips",
the new stops are in the select, and the price says how long and how many frames."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_index_panel_ui import (live, page, _open_from_the_menu, IV, WHAT, PRICE, RUN)  # noqa: E402,F401


def test_a_fine_look_of_everything_is_one_step_and_says_how_long(page, live):
    pg = page
    _open_from_the_menu(pg)
    assert pg.locator('[data-testid="index-scope"]').inner_text() == "all 3 clips"
    pg.locator(WHAT).select_option("look")
    stops = pg.locator(f"{IV} option").evaluate_all("o => o.map(x => x.value)")
    assert [s for s in stops if s] == ["4", "3", "2", "1", "0.5", "0.25", "0.1"]
    pg.locator(IV).select_option("0.25")
    pg.wait_for_function("document.querySelector('[data-testid=index-price]').dataset.local === ''")
    price = pg.locator(PRICE).inner_text()
    assert "frames in" in price and "sheets" in price and "about" in price, price
    assert pg.locator(RUN).is_enabled()
    # what a clip got, said in the detail
    pg.locator('[data-testid=index-row][data-clip="CLIP_A.MP4"]').click()
    got = pg.locator('[data-testid="index-got"]').inner_text()
    assert got.startswith("got: every 4 s"), got
