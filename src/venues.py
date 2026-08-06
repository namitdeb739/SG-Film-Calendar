"""Normalise messy venue strings to a canonical cinema name.

The sources label the same cinema many ways — "GV Cineleisure Hall 6 (SFS
Somerset)", "Golden Village Cineleisure, Hall 6"; "Shaw Lido", "Shaw Lido 2",
"Shaw Theatres Lido" — which fragments the by-cinema grouping and misdirects
the website/maps links. This collapses each to one canonical name, keyed on the
brand plus (for the multi-location chains) its branch, dropping hall numbers and
parenthetical notes.
"""

import re

# Branch keyword -> canonical branch label, for the chains that have many.
_GV_BRANCHES = [
    ("cineleisure", "Cineleisure"),
    ("funan", "Funan"),
    ("suntec", "Suntec City"),
    ("bugis", "Bugis+"),
    ("vivo", "VivoCity"),
    ("paya lebar", "Paya Lebar"),
    ("tiong bahru", "Tiong Bahru"),
    ("katong", "i12 Katong"),
    ("yishun", "Yishun"),
    ("jurong", "Jurong"),
    ("plaza", "Plaza"),
    ("grand", "Grand"),
]
_SHAW_BRANCHES = [
    ("lido", "Lido"),
    ("plq", "PLQ"),
    ("waterway", "Waterway Point"),
    ("nex", "Nex"),
    ("seletar", "Seletar"),
    ("balestier", "Balestier"),
    ("jcube", "JCube"),
]
_PROJECTOR_BRANCHES = [
    ("cineleisure", "Cineleisure"),
    ("riverside", "Riverside"),
    ("golden mile", "Golden Mile"),
    ("picturehouse", "Picturehouse"),
]


def _branch(prefix: str, low: str, branches: list) -> str:
    """Prefix + the first matching branch label, else the brand alone."""
    for keyword, label in branches:
        if keyword in low:
            return prefix + label
    return prefix.rstrip(", ")


def normalize_venue(raw: str) -> str:
    """Return a canonical cinema name for a raw venue string."""
    venue = re.sub(r"\s+", " ", (raw or "").strip())
    if not venue:
        return venue
    low = venue.lower()

    # The Projector shares the Cineleisure building with GV, so match it first.
    if "projector" in low or re.search(r"\btp\b", low):
        return _branch("The Projector, ", low, _PROJECTOR_BRANCHES)
    if "golden village" in low or re.search(r"\bgv\b", low):
        return _branch("Golden Village ", low, _GV_BRANCHES)
    if "shaw" in low:
        return _branch("Shaw Theatres ", low, _SHAW_BRANCHES)
    if "filmhouse" in low:
        return "Filmhouse Cinemas"
    if "oldham" in low:
        return "Oldham Theatre"
    if "national gallery" in low:
        return "National Gallery Singapore"
    if "national library" in low or re.search(r"\bnlb\b", low):
        return "National Library"
    if "lasalle" in low:
        return "LASALLE College of the Arts"
    if "alliance fran" in low:
        return "Alliance Française"
    if "marina bay sands" in low:
        return "Marina Bay Sands"
    if "72-13" in low:
        return "72-13 (TheatreWorks)"
    if "various" in low:
        return "Various venues"
    return venue
