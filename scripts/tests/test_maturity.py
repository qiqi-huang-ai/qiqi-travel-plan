import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import migrate_trip
import privacy_export
import recheck_trip
import render_outputs as ro
import evidence_graph
import route_matrix
import state_io
import tikhub_client
import validate_trip as vt
import verify_acceptance


SAMPLE = Path(__file__).resolve().parents[2] / "assets" / "examples" / "sample_trip.json"


def sample():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class MaturityTests(unittest.TestCase):
    def test_migration_adds_new_contract_without_changing_identity(self):
        old = sample()
        old["schema_version"] = "1.0"
        old.pop("provider_results", None)
        old.pop("recheck_queue", None)
        old.pop("privacy", None)
        old["itinerary"].pop("plan_variants", None)
        migrated = migrate_trip.migrate(old)
        self.assertEqual(migrated["schema_version"], "1.1")
        self.assertEqual(migrated["trip_id"], old["trip_id"])
        self.assertIn("provider_results", migrated)
        self.assertIn("recheck_queue", migrated)
        self.assertIn("plan_variants", migrated["itinerary"])

    def test_place_change_invalidates_dependent_fact_leg_and_item(self):
        original = sample()
        edited = copy.deepcopy(original)
        place = next(p for p in edited["places"] if p["place_id"] == "place-museum")
        place["entrance"] = "新入口"
        affected = state_io.invalidate_changed_dependencies(original, edited)
        fact = next(f for f in edited["facts"] if f["subject_id"] == "place-museum")
        leg = next(l for l in edited["legs"] if l["to_id"] == "place-museum")
        item = next(i for d in edited["itinerary"]["days"] for i in d["items"] if i.get("place_id") == "place-museum")
        self.assertIn("place-museum", affected)
        self.assertEqual(fact["status"], "unknown")
        self.assertEqual(leg["evidence_level"], "unknown")
        self.assertIsNone(leg["time_min"])
        self.assertEqual(item["verification_status"], "unknown")
        self.assertTrue(edited["recheck_queue"])

    def test_recheck_queue_includes_stale_conflict_locked_and_must(self):
        trip = migrate_trip.migrate(sample())
        trip["facts"][0]["stale_after"] = "2026-01-01T00:00:00+00:00"
        trip["facts"][1]["status"] = "conflict"
        queue = recheck_trip.build_queue(trip, datetime(2026, 9, 22, tzinfo=timezone.utc))
        reasons = {reason for q in queue for reason in q["reasons"]}
        self.assertIn("stale", reasons)
        self.assertIn("conflict", reasons)
        self.assertIn("must_go", reasons)
        self.assertIn("locked", reasons)

    def test_recheck_can_create_candidate_trip_without_mutating_source(self):
        trip = migrate_trip.migrate(sample())
        original = copy.deepcopy(trip)
        candidate = recheck_trip.with_queue(trip, datetime(2026, 9, 22, tzinfo=timezone.utc))
        self.assertEqual(trip, original)
        self.assertTrue(candidate["recheck_queue"])
        self.assertEqual(candidate["trip_id"], trip["trip_id"])

    def test_public_projection_removes_private_details_but_keeps_plan(self):
        trip = migrate_trip.migrate(sample())
        trip["request"]["user_materials"].append({"id":"m-private","type":"booking","note":"手机号 13800138000","readable":True})
        trip["itinerary"]["lodging"]["booked"] = {"name":"真实酒店","region":"中心区","address":"隐私路 1 号","place_id":"place-hotel-area"}
        public = privacy_export.project(trip, "public")
        text = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("13800138000", text)
        self.assertNotIn("隐私路 1 号", text)
        self.assertNotIn("真实酒店", text)
        self.assertEqual(public["trip_id"], trip["trip_id"])
        self.assertTrue(public["itinerary"]["days"])

    def test_public_projection_redacts_structured_identity_and_booking_codes(self):
        trip = migrate_trip.migrate(sample())
        trip["decisions"].append({
            "decision_id":"d-private", "topic":"联系人", "options":["旅客张三"],
            "user_choice":"预订人：张三，订单号：ABC12345678",
            "time":"2026-09-22T10:00:00+08:00", "affected_ids":[],
            "note":"身份证：110101199001011234",
        })
        public = privacy_export.project(trip, "public")
        text = json.dumps(public, ensure_ascii=False)
        for private_value in ("张三", "110101199001011234", "ABC12345678"):
            self.assertNotIn(private_value, text)
        self.assertEqual(public["decisions"], [])

    def test_public_projection_supports_exact_user_supplied_redactions(self):
        trip = migrate_trip.migrate(sample())
        trip["itinerary"]["days"][0]["items"][0]["description"] += "；联系人张三"
        public = privacy_export.project(trip, "public", redact_terms=["张三"])
        self.assertNotIn("张三", json.dumps(public, ensure_ascii=False))
        self.assertIn("exact_redactions", public["privacy"]["redacted_fields"])

    def test_schema_and_validator_accept_route_options_and_plan_variants(self):
        trip = migrate_trip.migrate(sample())
        trip["legs"][0]["route_group_id"] = "route-station-hotel"
        trip["legs"][0]["provider"] = "amap"
        trip["itinerary"]["plan_variants"] = [{
            "variant_id":"variant-relaxed", "title":"轻松版", "objective":"减少步行与换乘",
            "selected":True, "status":"conditional", "metrics":{"travel_minutes":120,"cost_max":2000,"lodging_changes":0,"unknown_count":1},
            "tradeoffs":["少去一个可选点"]
        }]
        errors = vt.MiniSchema(json.loads(Path(vt.SCHEMA_PATH).read_text(encoding="utf-8"))).errors
        schema = json.loads(Path(vt.SCHEMA_PATH).read_text(encoding="utf-8"))
        checker = vt.MiniSchema(schema)
        self.assertTrue(checker.check(trip, schema, "$"), checker.errors)
        audit = vt.Audit(trip)
        vt.semantic_checks(trip, audit)
        self.assertFalse(any("方案变体" in x["evidence"] for x in audit.issues), audit.issues)

    def test_stale_verified_fact_is_not_treated_as_current(self):
        trip = migrate_trip.migrate(sample())
        trip["facts"][0]["stale_after"] = "2020-01-01T00:00:00+00:00"
        audit = vt.Audit(trip)
        vt.semantic_checks(trip, audit)
        self.assertTrue(any(x["severity"] == "conditional" and "已过期" in x["evidence"] for x in audit.issues), audit.issues)

    def test_multiple_selected_plan_variants_are_blocking(self):
        trip = migrate_trip.migrate(sample())
        variant = {"variant_id":"v1","title":"轻松版","objective":"少走路","selected":True,"status":"conditional","metrics":{"travel_minutes":100,"cost_max":1800,"lodging_changes":0,"unknown_count":1},"tradeoffs":[]}
        trip["itinerary"]["plan_variants"] = [variant, {**variant, "variant_id":"v2", "title":"预算版"}]
        audit = vt.Audit(trip)
        vt.semantic_checks(trip, audit)
        self.assertTrue(any(x["severity"] == "blocking" and "只能选中一个" in x["evidence"] for x in audit.issues), audit.issues)

    def test_plan_variants_are_visible_in_both_outputs(self):
        trip = migrate_trip.migrate(sample())
        trip["itinerary"]["plan_variants"] = [{"variant_id":"v1","title":"轻松版","objective":"减少步行与换乘","selected":True,"status":"conditional","metrics":{"travel_minutes":120,"cost_max":2000,"lodging_changes":0,"unknown_count":1},"tradeoffs":["少去一个可选点"]}]
        ctx = ro.Ctx(trip)
        md = ro.render_md(ctx)
        html = ro.html_to_text(ro.render_html(ctx))
        for value in ("轻松版", "减少步行与换乘", "少去一个可选点"):
            self.assertIn(value, md)
            self.assertIn(value, html)

    def test_acceptance_report_has_all_twelve_contract_checks(self):
        trip = migrate_trip.migrate(sample())
        report = verify_acceptance.evaluate(trip)
        self.assertEqual([x["id"] for x in report], [f"A{i:02d}" for i in range(1, 13)])
        self.assertTrue(all(x["status"] in ("pass", "warn", "fail", "manual") for x in report))
        self.assertEqual(next(x for x in report if x["id"] == "A05")["status"], "manual")
        self.assertEqual(next(x for x in report if x["id"] == "A06")["status"], "manual")
        self.assertEqual(next(x for x in report if x["id"] == "A07")["status"], "manual")

    def test_acceptance_behavior_results_are_not_inferred_from_field_presence(self):
        trip = migrate_trip.migrate(sample())
        trip["recheck_queue"] = []
        report = verify_acceptance.evaluate(trip, behavior_results={"A06": True, "A07": False})
        self.assertEqual(next(x for x in report if x["id"] == "A06")["status"], "pass")
        self.assertEqual(next(x for x in report if x["id"] == "A07")["status"], "fail")

    def test_acceptance_a04_uses_real_semantic_blockers(self):
        trip = migrate_trip.migrate(sample())
        trip["itinerary"]["budget"]["hard_limit"] = 100
        report = verify_acceptance.evaluate(trip)
        self.assertEqual(next(x for x in report if x["id"] == "A04")["status"], "fail")

    def test_skill_routes_advanced_lifecycle_commands(self):
        root = Path(__file__).resolve().parents[2]
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        description = skill.split("description:", 1)[1].splitlines()[0]
        self.assertIn("Use when", description)
        for command in ("migrate_trip.py", "recheck_trip.py", "privacy_export.py"):
            self.assertIn(command, skill)

    def test_cross_platform_ci_matrix_is_present(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github" / "workflows" / "check.yml").read_text(encoding="utf-8")
        for runner in ("ubuntu-latest", "windows-latest", "macos-latest"):
            self.assertIn(runner, workflow)
        self.assertIn("python -m compileall", workflow)

    def test_offline_h5_has_real_departure_mode_and_structured_deadlines(self):
        trip = migrate_trip.migrate(sample())
        trip["itinerary"]["checklist"][0]["deadline_at"] = "2026-10-03T20:00:00+08:00"
        html = ro.render_html(ro.Ctx(trip))
        self.assertIn('id="departure-mode"', html)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn('data-deadline="2026-10-03T20:00:00+08:00"', html)
        self.assertIn('class="countdown"', html)
        self.assertIn("body.departure .departure-hide", html)
        self.assertIn("departure-day", html)

    def test_tikhub_cli_requires_explicit_live_or_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = str(Path(td) / "usage.json")
            with self.assertRaises(SystemExit):
                tikhub_client.main(["search", "测试", "--usage-file", ledger])
            self.assertFalse(Path(ledger).exists())

    def test_evidence_graph_connects_fields_sources_and_itinerary(self):
        trip = migrate_trip.migrate(sample())
        graph = evidence_graph.build_graph(trip, datetime(2026, 9, 22, tzinfo=timezone.utc))
        museum = next(x for x in graph["facts"] if x["fact_id"] == "fact-museum-opening")
        self.assertEqual(museum["subject_id"], "place-museum")
        self.assertTrue(museum["source_ids"])
        self.assertIn("item-d1-museum", museum["used_by_item_ids"])
        self.assertIn("summary", graph)

    def test_route_matrix_groups_and_selects_candidates(self):
        trip = migrate_trip.migrate(sample())
        base = copy.deepcopy(trip["legs"][0])
        base.update({"leg_id":"leg-route-bus","route_group_id":"rg-station-hotel","provider":"amap","mode":"公交","time_min":35,"time_max":50,"walking_min":8,"transfers":1,"evidence_level":"tool_route"})
        fast = copy.deepcopy(base)
        fast.update({"leg_id":"leg-route-taxi","mode":"打车","time_min":18,"time_max":28,"walking_min":2,"transfers":0})
        matrix = route_matrix.build_matrix([base, fast])
        self.assertEqual(len(matrix), 1)
        self.assertEqual(matrix[0]["recommended"]["fast"], "leg-route-taxi")
        self.assertEqual(matrix[0]["recommended"]["low_walk"], "leg-route-taxi")

    def test_recheck_queue_is_visible_in_both_outputs(self):
        trip = migrate_trip.migrate(sample())
        trip["recheck_queue"] = [{"target_id":"fact-museum-opening","target_type":"fact","reasons":["stale"],"action":"重新核查博物馆开放时间"}]
        ctx = ro.Ctx(trip)
        md = ro.render_md(ctx)
        html = ro.html_to_text(ro.render_html(ctx))
        for value in ("重新核查博物馆开放时间",):
            self.assertIn(value, md)
            self.assertIn(value, html)


if __name__ == "__main__":
    unittest.main()
