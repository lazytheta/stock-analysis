from unittest.mock import patch

import gather_data as gd

LIVE = {"Auto & Truck": 1.27, "Healthcare Support Services": 0.77,
        "Hospitals/Healthcare Facilities": 0.70, "Software (System & Application)": 1.2}


def _resolve(sic, desc):
    with patch.object(gd, "fetch_sector_betas", return_value=LIVE):
        return gd.resolve_sector_betas(sic, desc)


def test_ampersand_alone_is_not_a_match():
    # "HOSPITAL & MEDICAL SERVICE PLANS" shared only "&" with "Auto & Truck".
    assert _resolve(9999, "BLANKBOOKS & LOOSELEAF BINDERS")[0][0] == "Market"


def test_managed_care_sic_maps_to_healthcare_support():
    assert _resolve(6324, "HOSPITAL & MEDICAL SERVICE PLANS") == [
        ("Healthcare Support Services", 0.77, 1.0)]


def test_word_overlap_still_matches_real_words():
    assert _resolve(9998, "HOSPITALS/HEALTHCARE FACILITIES")[0][0] == "Hospitals/Healthcare Facilities"
