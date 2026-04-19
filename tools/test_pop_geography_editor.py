#!/usr/bin/env python3
"""Unit tests for pop_geography_editor.py features."""

import unittest
import sys
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, str(Path(__file__).parent))

from pop_geography_editor import (
    _safe_float,
    normalize_size,
    pop_attrs_equal,
    format_attrs_summary,
    parse_pop_attributes,
    format_pop_line,
    GeoInfo,
    PopRow,
)


class TestHelpers(unittest.TestCase):
    def test_safe_float_valid(self):
        self.assertEqual(_safe_float("123.456"), 123.456)
        self.assertEqual(_safe_float("0"), 0.0)
        self.assertEqual(_safe_float("-5.5"), -5.5)

    def test_safe_float_invalid(self):
        self.assertEqual(_safe_float(""), 0.0)
        self.assertEqual(_safe_float("abc"), 0.0)
        self.assertEqual(_safe_float(None), 0.0)

    def test_normalize_size(self):
        self.assertEqual(normalize_size("123.456789"), "123.457")
        self.assertEqual(normalize_size("100"), "100.000")
        self.assertEqual(normalize_size("abc"), "abc")

    def test_pop_attrs_equal(self):
        attrs1 = OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "english")])
        attrs2 = OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "english")])
        attrs3 = OrderedDict([("type", "nobles"), ("size", "200"), ("culture", "english")])
        self.assertTrue(pop_attrs_equal(attrs1, attrs2))
        self.assertFalse(pop_attrs_equal(attrs1, attrs3))

    def test_format_attrs_summary(self):
        attrs = OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "english")])
        result = format_attrs_summary(attrs)
        self.assertIn("type=nobles", result)
        self.assertIn("size=100", result)
        self.assertIn("culture=english", result)


class TestParsePopAttributes(unittest.TestCase):
    def test_parse_pop_attributes_basic(self):
        block = "type = nobles size = 100 culture = english religion = catholic"
        attrs = parse_pop_attributes(block)
        self.assertEqual(attrs.get("type"), "nobles")
        self.assertEqual(attrs.get("size"), "100")
        self.assertEqual(attrs.get("culture"), "english")
        self.assertEqual(attrs.get("religion"), "catholic")

    def test_parse_pop_attributes_empty(self):
        attrs = parse_pop_attributes("")
        self.assertEqual(dict(attrs), {})

    def test_format_pop_line(self):
        attrs = OrderedDict([("type", "nobles"), ("size", "100.000"), ("culture", "english"), ("religion", "catholic")])
        line = format_pop_line(attrs)
        self.assertIn("type = nobles", line)
        self.assertIn("size = 100.000", line)
        self.assertIn("culture = english", line)
        self.assertIn("religion = catholic", line)


class TestGeoInfo(unittest.TestCase):
    def test_geo_info_defaults(self):
        geo = GeoInfo()
        self.assertEqual(geo.continent, "")
        self.assertEqual(geo.province, "")

    def test_geo_info_with_values(self):
        geo = GeoInfo(continent="Europe", superregion="Western Europe", region="Iberia", area="Castile", province="castile")
        self.assertEqual(geo.continent, "Europe")
        self.assertEqual(geo.province, "castile")


class TestPopRow(unittest.TestCase):
    def setUp(self):
        attrs = OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "english"), ("religion", "catholic")])
        self.row = PopRow(row_id=1, location="london", attrs=attrs)
        self.geo = GeoInfo(continent="Europe", superregion="Western Europe", region="Britain", area="England", province="england")

    def test_get_value_location(self):
        self.assertEqual(self.row.get_value("location", self.geo), "london")

    def test_get_value_filter_field(self):
        self.assertEqual(self.row.get_value("province", self.geo), "england")
        self.assertEqual(self.row.get_value("region", self.geo), "Britain")

    def test_get_value_standard_attr(self):
        self.assertEqual(self.row.get_value("type", self.geo), "nobles")
        self.assertEqual(self.row.get_value("size", self.geo), "100")
        self.assertEqual(self.row.get_value("culture", self.geo), "english")

    def test_get_value_extra(self):
        extra = self.row.get_value("extra", self.geo)
        self.assertNotIn("type=nobles", extra)
        self.assertNotIn("size=100", extra)


class TestRedistributionLogic(unittest.TestCase):
    def setUp(self):
        self.location_geo = {
            "loc_a": GeoInfo(continent="Europe", superregion="Western Europe", region="Test", area="Test Area", province="test_prov"),
            "loc_b": GeoInfo(continent="Europe", superregion="Western Europe", region="Test", area="Test Area", province="test_prov"),
            "loc_c": GeoInfo(continent="Europe", superregion="Western Europe", region="Test", area="Test Area", province="test_prov"),
        }
        self.rows = [
            PopRow(1, "loc_a", OrderedDict([("type", "nobles"), ("size", "300"), ("culture", "eng"), ("religion", "cath")])),
            PopRow(2, "loc_b", OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "eng"), ("religion", "cath")])),
        ]

    def test_redistribute_mode_target(self):
        source_location = "loc_a"
        source_province = self.location_geo[source_location].province
        other_locations = [
            loc for loc, geo in self.location_geo.items()
            if geo.province == source_province and loc != source_location
        ]
        mode = "target"
        target_each = 50.0

        source_rows = [r for r in self.rows if r.location == source_location]
        source_size = sum(_safe_float(r.attrs.get("size", "0")) for r in source_rows)
        num_targets = len(other_locations)

        total_needed = target_each * num_targets
        excess = total_needed - source_size

        self.assertEqual(num_targets, 2)
        self.assertEqual(total_needed, 100.0)
        self.assertEqual(excess, -200.0)

    def test_redistribute_mode_amount(self):
        source_location = "loc_a"
        other_locations = [
            loc for loc, geo in self.location_geo.items()
            if geo.province == self.location_geo[source_location].province and loc != source_location
        ]
        mode = "amount"
        amount = 150.0

        source_rows = [r for r in self.rows if r.location == source_location]
        source_size = sum(_safe_float(r.attrs.get("size", "0")) for r in source_rows)
        per_loc = amount / len(other_locations)

        self.assertEqual(per_loc, 75.0)
        self.assertEqual(source_size, 300.0)

    def test_redistribute_mode_percent(self):
        source_location = "loc_a"
        other_locations = [
            loc for loc, geo in self.location_geo.items()
            if geo.province == self.location_geo[source_location].province and loc != source_location
        ]
        mode = "percent"
        pct = 50.0

        source_rows = [r for r in self.rows if r.location == source_location]
        row = source_rows[0]
        old_size = _safe_float(row.attrs.get("size", "0"))

        take = old_size * pct / 100
        remaining = old_size - take
        per_loc = take

        self.assertEqual(take, 150.0)
        self.assertEqual(remaining, 150.0)
        self.assertEqual(per_loc, 150.0)

    def test_redistribute_filtered_scope(self):
        location_geo = {
            "area_a_loc1": GeoInfo(continent="E", superregion="W", region="R", area="area_a", province="p1"),
            "area_a_loc2": GeoInfo(continent="E", superregion="W", region="R", area="area_a", province="p1"),
            "area_b_loc1": GeoInfo(continent="E", superregion="W", region="R", area="area_b", province="p2"),
            "area_b_loc2": GeoInfo(continent="E", superregion="W", region="R", area="area_b", province="p2"),
        }
        all_rows = [
            PopRow(1, "area_a_loc1", OrderedDict([("type", "nobles"), ("size", "100"), ("culture", "eng"), ("religion", "cath")])),
            PopRow(2, "area_a_loc2", OrderedDict([("type", "nobles"), ("size", "200"), ("culture", "eng"), ("religion", "cath")])),
            PopRow(3, "area_b_loc1", OrderedDict([("type", "nobles"), ("size", "300"), ("culture", "eng"), ("religion", "cath")])),
            PopRow(4, "area_b_loc2", OrderedDict([("type", "nobles"), ("size", "400"), ("culture", "eng"), ("religion", "cath")])),
        ]

        source_location = "area_a_loc1"
        filter_area = "area_a"

        filtered_rows = [r for r in all_rows if r.location in ["area_a_loc1", "area_a_loc2"]]
        target_locations = list(dict.fromkeys(r.location for r in filtered_rows if r.location != source_location))

        self.assertIn("area_a_loc2", target_locations)
        self.assertNotIn("area_b_loc1", target_locations)
        self.assertEqual(len(target_locations), 1)

    def test_redistribute_value_exceeds_source(self):
        source_location = "loc_a"
        source_rows = [r for r in self.rows if r.location == source_location]
        row = source_rows[0]
        old_size = _safe_float(row.attrs.get("size", "0"))
        direct_value = 500.0

        self.assertGreater(direct_value, old_size)

    def test_collect_mode_target(self):
        source_location = "loc_a"
        other_locations = ["loc_b", "loc_c"]
        mode = "target"
        target_each = 50.0

        num_sources = len(other_locations)
        total_needed = target_each * num_sources
        current_other = sum(
            _safe_float(r.attrs.get("size", "0"))
            for loc in other_locations
            for r in self.rows if r.location == loc
        )

        self.assertEqual(num_sources, 2)
        self.assertEqual(total_needed, 100.0)
        self.assertEqual(current_other, 100.0)

    def test_collect_mode_amount(self):
        source_location = "loc_a"
        other_locations = ["loc_b"]
        mode = "amount"
        amount = 25.0

        loc_rows = [r for r in self.rows if r.location == other_locations[0]]
        row = loc_rows[0]
        old_size = _safe_float(row.attrs.get("size", "0"))

        take = min(old_size, amount)
        self.assertEqual(take, 25.0)
        self.assertEqual(old_size, 100.0)

    def test_collect_mode_percent(self):
        source_location = "loc_a"
        other_locations = ["loc_b"]
        mode = "percent"
        pct = 50.0

        loc_rows = [r for r in self.rows if r.location == other_locations[0]]
        row = loc_rows[0]
        old_size = _safe_float(row.attrs.get("size", "0"))

        take = old_size * pct / 100
        self.assertEqual(take, 50.0)


class TestProvinceDistribution(unittest.TestCase):
    def test_distribution_across_multiple_locations(self):
        location_geo = {
            "prov_loc_1": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
            "prov_loc_2": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
            "prov_loc_3": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
            "prov_loc_4": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
        }

        source_location = "prov_loc_1"
        other_locations = [
            loc for loc, geo in location_geo.items()
            if geo.province == location_geo[source_location].province and loc != source_location
        ]

        self.assertEqual(len(other_locations), 3)

        value_to_distribute = 150.0

        per_location = value_to_distribute / len(other_locations)

        self.assertEqual(per_location, 50.0)

        total_distributed = per_location * len(other_locations)
        self.assertEqual(total_distributed, value_to_distribute)

    def test_two_location_province(self):
        location_geo = {
            "loc_1": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
            "loc_2": GeoInfo(continent="E", superregion="W", region="R", area="A", province="P"),
        }

        source_location = "loc_1"
        other_locations = [
            loc for loc, geo in location_geo.items()
            if geo.province == location_geo[source_location].province and loc != source_location
        ]

        self.assertEqual(len(other_locations), 1)

        value_to_distribute = 100.0
        per_location = value_to_distribute / len(other_locations)
        self.assertEqual(per_location, 100.0)


class TestNormalizeSize(unittest.TestCase):
    def test_normalize_size_various(self):
        self.assertEqual(normalize_size("1"), "1.000")
        self.assertEqual(normalize_size("1.5"), "1.500")
        self.assertEqual(normalize_size("1.5678"), "1.568")
        self.assertEqual(normalize_size("0.001"), "0.001")
        self.assertEqual(normalize_size("0"), "0.000")

    def test_normalize_size_non_numeric(self):
        self.assertEqual(normalize_size("english"), "english")
        self.assertEqual(normalize_size(""), "")


class TestPopRowCreation(unittest.TestCase):
    def test_create_pop_row(self):
        attrs = OrderedDict()
        attrs["type"] = "nobles"
        attrs["size"] = "500"
        attrs["culture"] = "persian"
        attrs["religion"] = "sunni"

        row = PopRow(row_id=100, location="persepolis", attrs=attrs)

        self.assertEqual(row.row_id, 100)
        self.assertEqual(row.location, "persepolis")
        self.assertEqual(row.attrs["type"], "nobles")
        self.assertEqual(row.attrs["size"], "500")
        self.assertEqual(row.attrs["culture"], "persian")
        self.assertEqual(row.attrs["religion"], "sunni")

    def test_pop_row_with_extra_attrs(self):
        attrs = OrderedDict()
        attrs["type"] = "citizens"
        attrs["size"] = "200"
        attrs["culture"] = "roman"
        attrs["religion"] = "orthodox"
        attrs["some_custom"] = "value"
        attrs["another"] = "123"

        row = PopRow(row_id=1, location="rome", attrs=attrs)

        extra = row.get_value("extra", GeoInfo())
        self.assertIn("some_custom=value", extra)
        self.assertIn("another=123", extra)


class TestEdgeCases(unittest.TestCase):
    def test_zero_ratio(self):
        ratio = 0
        old_size = 100.0

        size_per_loc = (old_size * ratio / 100)
        remaining_size = old_size * (1 - ratio / 100)

        self.assertEqual(size_per_loc, 0.0)
        self.assertEqual(remaining_size, old_size)

    def test_full_ratio(self):
        ratio = 100
        old_size = 100.0
        other_count = 2

        size_per_loc = (old_size * ratio / 100) / other_count
        remaining_size = old_size * (1 - ratio / 100)

        self.assertEqual(size_per_loc, 50.0)
        self.assertEqual(remaining_size, 0.0)

    def test_single_other_location(self):
        value = 100.0
        other_count = 1

        per_loc = value / other_count
        self.assertEqual(per_loc, 100.0)


if __name__ == "__main__":
    unittest.main()