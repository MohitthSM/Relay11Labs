from relay.script.normalize import spoken_text
from relay.sites import get_site, load_sites


def test_codes_are_spaced_out_and_plain_numbers_kept():
    assert spoken_text("Flight BA117 at Gate 23.") == "Flight B A 1 1 7 at Gate 23."
    assert spoken_text("Gate B7, route N25.") == "Gate B 7, route N 2 5."
    assert spoken_text("Route 25  now") == "Route 25 now"


def test_sites_load_and_zone_languages_drive_script_languages():
    assert set(load_sites()) == {"tube_station", "airport", "bus_station"}
    assert get_site("airport").languages_for("passenger_page") == ["en", "es", "fr"]
    assert get_site("tube_station").languages_for("line_status") == ["en"]
    assert {s.hub_type for s in load_sites().values()} == {"underground", "airport", "bus"}
