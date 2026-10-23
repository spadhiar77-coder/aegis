"""Pick the detector the API uses: the Loop 2 model or the Loop 1 rules.

Both return an `Assessment`, so callers just do `assess(txn, history)`.
The choice is read on each call (it's one environment lookup), which lets
tests switch detector without restarting the app.
"""

from app import config, rules
from app.rules import Assessment


def assess(txn, history) -> Assessment:
    if config.get_detector_name() == "rules":
        return rules.assess(txn, history)
    from app.model import load_default  # loaded once, then cached

    return load_default().assess(txn, history)
