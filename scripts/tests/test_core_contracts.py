"""Behavior-level regression tests for the travel-plan-pro contract.

These tests intentionally exercise public module behavior with synthetic trips;
they do not mirror another Skill's test layout or fixtures.
"""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import privacy_export
import provider_gate
import recheck_trip
import render_outputs
import state_io
import tikhub_client
import trip_support
import validate_trip


SAMPLE_PATH = ROOT / "assets" / "examples" / "sample_trip.json"


def trip():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def audit(data):
    schema = json.loads(Path(validate_trip.SCHEMA_PATH).read_text(encoding="utf-8"))
    checker = validate_trip.MiniSchema(schema)
    checker.check(data, schema, "$")
    report = validate_trip.Audit(data)
    validate_trip.semantic_checks(data, report)
    return checker.errors, report


class CoreContractTests(unittest.TestCase):
    def test_example_data_meets_schema_and_has_no_blocker(self):
        errors, report = audit(trip())
        self.assertEqual(errors, [])
        self.assertEqual(report.summary()["blocking"], 0, report.issues)

    def test_replacing_a_place_invalidates_its_downstream_evidence(self):
        original = trip()
        changed = copy.deepcopy(original)
        place = next(p for p in changed["places"] if p["place_id"] == "place-museum")
        place["entrance"] = "西门"
        affected = state_io.invalidate_changed_dependencies(original, changed)

        linked_fact = next(f for f in changed["facts"] if f["subject_id"] == "place-museum")
        linked_item = next(i for d in changed["itinerary"]["days"] for i in d["items"] if i["place_id"] == "place-museum")
        self.assertIn("place-museum", affected)
        self.assertEqual(linked_fact["status"], "unknown")
        self.assertEqual(linked_item["verification_status"], "unknown")
        self.assertTrue(changed["recheck_queue"])

    def test_user_confirmed_plan_survives_evidence_invalidation(self):
        original = trip()
        edited = copy.deepcopy(original)
        item = next(i for d in edited["itinerary"]["days"] for i in d["items"] if i["place_id"] == "place-museum")
        item["verification_status"] = "planned"
        next(p for p in edited["places"] if p["place_id"] == "place-museum")["entrance"] = "西门"

        state_io.invalidate_changed_dependencies(original, edited)

        self.assertEqual(item["verification_status"], "planned")

    def test_user_confirmed_route_survives_place_change(self):
        original = trip()
        edited = copy.deepcopy(original)
        leg = next(leg for leg in edited["legs"] if leg["to_id"] == "place-museum")
        leg.update({"evidence_level": "planned", "time_min": 20, "time_max": 20})
        next(p for p in edited["places"] if p["place_id"] == "place-museum")["entrance"] = "西门"

        state_io.invalidate_changed_dependencies(original, edited)

        self.assertEqual(leg["evidence_level"], "planned")
        self.assertEqual(leg["time_max"], 20)

    def test_visit_can_reuse_a_same_day_admission_booking(self):
        data = trip()
        day = data["itinerary"]["days"][0]
        parent = next(item for item in day["items"] if item["item_id"] == "item-d1-museum")
        parent["planned_end"] = "2026-10-10T12:00:00+08:00"
        child = copy.deepcopy(parent)
        child.update({
            "item_id": "item-d1-museum-walk",
            "planned_start": "2026-10-10T12:00:00+08:00",
            "planned_end": "2026-10-10T13:00:00+08:00",
            "booking_status": "not_required",
            "admission_item_id": parent["item_id"],
        })
        day["items"].insert(day["items"].index(parent) + 1, child)

        _, report = audit(data)

        self.assertFalse(any("地点需要预约" in issue["evidence"] and child["item_id"] in issue["affected_ids"] for issue in report.issues), report.issues)

    def test_stale_and_conflicting_information_enters_recheck_queue(self):
        data = trip()
        data["facts"][0]["stale_after"] = "2020-01-01T00:00:00+00:00"
        data["facts"][1]["status"] = "conflict"
        queue = recheck_trip.build_queue(data, datetime(2026, 9, 22, tzinfo=timezone.utc))
        reasons = {reason for entry in queue for reason in entry["reasons"]}
        self.assertIn("stale", reasons)
        self.assertIn("conflict", reasons)

    def test_public_projection_removes_booking_identity_details(self):
        data = trip()
        data["request"]["user_materials"].append({
            "id": "booking", "type": "order", "note": "联系人李四，手机号 13800138000", "readable": True,
        })
        public = privacy_export.project(data, "public", redact_terms=["李四"])
        text = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("13800138000", text)
        self.assertNotIn("李四", text)
        self.assertEqual(public["trip_id"], data["trip_id"])

    def test_renderer_keeps_business_content_and_has_no_network_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trip.json"
            path.write_text(json.dumps(trip(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(render_outputs.main([str(path)]), 0)
            html = next((path.parent / "outputs").glob("*.html")).read_text(encoding="utf-8")
            self.assertIn("示例市两日人文轻松游", html)
            self.assertIn("A SLOW ISLAND DAY", html)
            self.assertIn('class="sidebar"', html)
            self.assertIn('id="section-nav"', html)
            self.assertIn('id="day-switcher"', html)
            self.assertIn('class="journey-rail"', html)
            self.assertIn("localStorage", html)
            self.assertNotIn("<script src=", html)
            self.assertNotIn("<link rel=\"stylesheet\"", html)

    def test_traveler_html_does_not_dump_history_or_internal_provider_state(self):
        data = trip()
        data["changelog"].append({"version": 99, "summary": "旧版内部记录，不能出现在旅行者页面", "at": "2026-10-01T10:00:00+08:00"})
        data["decisions"].append({"decision_id": "internal", "topic": "provider", "options": [], "user_choice": "secret_store", "time": "2026-10-01T10:00:00+08:00", "affected_ids": [], "note": "available/granted"})
        page = render_outputs.render_html(render_outputs.Ctx(data))

        self.assertIn("现在要完成", page)
        self.assertIn("查看核查依据与待确认事项", page)
        self.assertNotIn("旧版内部记录，不能出现在旅行者页面", page)
        self.assertNotIn("available/granted", page)
        self.assertNotIn("补充记录", page)

    def test_unknown_route_does_not_gain_fake_duration(self):
        data = trip()
        leg = data["legs"][0]
        leg.update(evidence_level="unknown", time_min=None, time_max=None)
        errors, report = audit(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("没有时间依据" in issue["evidence"] for issue in report.issues))

    def test_tikhub_dry_run_never_reads_a_token_or_makes_network_request(self):
        output = io.StringIO()
        with patch.dict("os.environ", {"TIKHUB_API_KEY": "not-used"}, clear=True), patch("sys.stdout", output):
            code = tikhub_client.main(["search", "厦门 亲子", "--dry-run"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["source_meta"]["response_mode"], "dry_run")
        self.assertNotIn("not-used", output.getvalue())

    def test_tikhub_live_requires_explicit_budget(self):
        with self.assertRaises(SystemExit):
            tikhub_client.main(["search", "厦门", "--live", "--usage-file", "/tmp/tikhub-test-usage.json"])

    def test_provider_gate_records_explicit_choice_without_a_secret(self):
        data = provider_gate.choose(trip(), "configure", 12)
        decision = next(item for item in data["decisions"] if item["topic"] == "provider_onboarding:tikhub")
        capability = next(item for item in data["capabilities"] if item["capability"] == "tikhub")
        self.assertEqual(decision["user_choice"], "接入，但需要配置指导")
        self.assertIn("12", decision["note"])
        self.assertEqual(capability["availability"], "unavailable")
        self.assertNotIn("Authorization", json.dumps(data, ensure_ascii=False))

    def test_provider_gate_rejects_missing_budget_for_opt_in(self):
        with self.assertRaisesRegex(ValueError, "请求次数上限"):
            provider_gate.choose(trip(), "connect", None)

    def test_tikhub_auth_error_stops_further_requests(self):
        calls = []

        def transport(url, headers):
            calls.append((url, headers))
            return 401, json.dumps({"detail": "Unauthorized"})

        client = tikhub_client.Client("example-token-value", tikhub_client.Budget(3), transport=transport)
        with self.assertRaises(tikhub_client.AuthError):
            client.search("厦门")
        with self.assertRaises(tikhub_client.TikHubError):
            client.comments("note-1")
        self.assertEqual(len(calls), 1)

    def test_checkpoint_rejects_changed_data_with_the_same_version(self):
        with tempfile.TemporaryDirectory() as directory:
            data = trip()
            trip_support.checkpoint(directory, data)
            changed = copy.deepcopy(data)
            changed["itinerary"]["days"][0]["items"][0]["title"] = "同版本的不同内容"
            with self.assertRaisesRegex(ValueError, "不能覆盖历史快照"):
                trip_support.checkpoint(directory, changed)

    def test_restore_creates_a_new_version_and_invalidates_review_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = trip()
            trip_support.checkpoint(directory, first)
            current = copy.deepcopy(first)
            current["plan_version"] = 2
            current["itinerary"]["days"][0]["items"][0]["title"] = "当前版本"
            trip_support.checkpoint(directory, current)
            trip_support.atomic_json(root / "trip.json", current)

            self.assertEqual(state_io.cmd_restore(SimpleNamespace(trip_dir=directory, v=1)), 0)

            restored = json.loads((root / "trip.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["plan_version"], 3)
            self.assertEqual(
                restored["itinerary"]["days"][0]["items"][0]["title"],
                first["itinerary"]["days"][0]["items"][0]["title"],
            )
            self.assertFalse(any(fact["status"] == "verified" for fact in restored["facts"]))
            self.assertEqual(restored["status"], "draft")
            self.assertFalse(restored["checked_at"])
            self.assertTrue((root / "versions" / "trip.v2.json").exists())


if __name__ == "__main__":
    unittest.main()
