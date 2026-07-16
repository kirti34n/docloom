"""Guard against __all__ drifting away from what's actually importable.

Regression for: __init__.py's __all__ still listed "Divider", but the .ir
import list lost it when the four Diagram names were inserted, so
`from docloom import Divider` raised ImportError while every other test in
the suite (importing from docloom.ir directly) stayed green and missed it.
This imports every name docloom advertises in __all__ from the top-level
package itself, so any future name that gets added to __all__ without a
matching import (or vice versa) fails loudly here instead of shipping.
"""

import importlib


def test_every_dunder_all_name_is_importable_from_top_level():
    docloom = importlib.import_module("docloom")
    missing = [name for name in docloom.__all__ if not hasattr(docloom, name)]
    assert missing == [], (
        "names listed in docloom.__all__ but not importable via "
        f"`from docloom import <name>`: {missing}"
    )


def test_from_docloom_import_star_matches_dunder_all():
    # `from docloom import *` only exposes __all__; confirm every one of
    # those names is a real, live attribute (not e.g. shadowed to None) by
    # actually executing the import statement form, one name at a time.
    docloom = importlib.import_module("docloom")
    for name in docloom.__all__:
        namespace: dict = {}
        exec(f"from docloom import {name}", namespace)
        assert name in namespace, name
