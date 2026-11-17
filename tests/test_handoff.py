import copy
import unittest

from scripts.publish_handoff import validate_inputs


class HandoffGateTests(unittest.TestCase):
    def samples(self):
        summary = {"as_of_date":"2026-11-16", "daily_ready":True,
                   "quality":{"daily_ready":True,"critical":[],"checks":{"LATEST_COMPLETED_SESSION":True}},
                   "breadth":{"component_count":47},"source_policy":{"stocks":"Yahoo Finance"}}
        manifest = {"as_of_date":"2026-11-16","source_mode":"LIVE","quality_gate":"PASS",
                    "component_count":47,"target_ticker_count":47,"valid_ticker_count":47,
                    "files":{str(i):{} for i in range(12)}}
        validation = {"as_of_date":"2026-11-16","errors":[],"result":"PASS_WITH_SOURCE_CAVEAT",
                      "component_count":47,"verified_manifest_files":12}
        return summary, manifest, validation

    def test_ready_with_disclosed_source_caveats_is_accepted(self):
        validate_inputs(*self.samples())

    def test_not_ready_cannot_reach_report(self):
        summary, manifest, validation = self.samples()
        summary["daily_ready"] = False
        with self.assertRaisesRegex(ValueError,"DAILY_READY"):
            validate_inputs(summary,manifest,validation)

    def test_old_or_inconsistent_trade_date_is_blocked(self):
        summary, manifest, validation = self.samples()
        manifest["as_of_date"] = "2026-11-13"
        with self.assertRaisesRegex(ValueError,"trade dates"):
            validate_inputs(summary,manifest,validation)

    def test_replay_is_never_report_ready(self):
        summary, manifest, validation = self.samples()
        manifest["source_mode"] = "HISTORICAL_REPLAY"
        with self.assertRaisesRegex(ValueError,"historical replay"):
            validate_inputs(summary,manifest,validation)

    def test_incomplete_constituents_are_blocked(self):
        summary, manifest, validation = self.samples()
        manifest["valid_ticker_count"] = 46
        with self.assertRaisesRegex(ValueError,"coverage"):
            validate_inputs(summary,manifest,validation)

    def test_failed_independent_audit_is_blocked(self):
        summary, manifest, validation = self.samples()
        validation["errors"] = ["hash mismatch"]
        with self.assertRaisesRegex(ValueError,"independent ZIP audit"):
            validate_inputs(summary,manifest,validation)

    def test_failing_latest_session_check_is_blocked(self):
        summary, manifest, validation = self.samples()
        summary["quality"]["checks"]["LATEST_COMPLETED_SESSION"] = False
        with self.assertRaisesRegex(ValueError,"quality checks"):
            validate_inputs(summary,manifest,validation)


if __name__ == "__main__":
    unittest.main()
