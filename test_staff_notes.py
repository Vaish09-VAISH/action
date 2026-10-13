import unittest
from unittest.mock import patch

import pipeline


def make_card(**overrides):
    card = {
        "caller": "patient",
        "reason": "Asked about a follow-up",
        "request": "Schedule a follow-up appointment",
        "call_type": "appointment",
        "sentiment": "calm",
        "urgency": "routine",
        "emergency_flag": False,
        "summary": "Staff notes describe a request to schedule a follow-up.",
        "evidence": {
            field: {"quote": "unsupported model quote", "timestamp": "00:20"}
            for field in ("summary", "sentiment", "urgency", "emergency")
        },
        "action_items": [
            {
                "text": "Schedule a follow-up",
                "action_required": "Contact the patient to schedule",
                "owner_team": "scheduling",
                "quote": "invented supporting quote",
                "timestamp": "00:20",
                "timestamp_sec": 20,
                "confidence": 0.99,
                "status": "verified",
                "due_hint": "",
            }
        ],
    }
    card.update(overrides)
    return card


class StaffNotesProcessingTests(unittest.TestCase):
    def process_with(self, notes, model_output):
        with patch.object(pipeline, "call_model", return_value=model_output):
            return pipeline.process_staff_notes(notes)

    def test_empty_notes_do_not_call_model(self):
        with patch.object(pipeline, "call_model") as model:
            result = pipeline.process_staff_notes(" \n ")
        self.assertEqual(result["status"], "error")
        model.assert_not_called()

    def test_valid_notes_have_unavailable_confidence_and_no_quotes(self):
        result = self.process_with(
            "Staff notes: patient requests help scheduling a follow-up appointment.",
            make_card(),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "Staff-entered notes")
        self.assertEqual(result["routed"], [])
        item = result["card"]["action_items"][0]
        self.assertIsNone(item["confidence"])
        self.assertFalse(item["confidence_available"])
        self.assertEqual(item["status"], "needs_review")
        self.assertFalse(item["verified"])
        self.assertEqual(item["quote"], "none")
        for evidence in result["card"]["evidence"].values():
            self.assertEqual(evidence["quote"], "none")
            self.assertEqual(evidence["timestamp"], "")

    def test_malformed_model_output_returns_error(self):
        with patch.object(
            pipeline, "call_model", side_effect=ValueError("malformed JSON")
        ):
            result = pipeline.process_staff_notes("Patient asks for a follow-up.")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("card", result)

    def test_model_api_failure_returns_error_without_a_card(self):
        with patch.object(
            pipeline, "call_model", side_effect=RuntimeError("provider unavailable")
        ):
            result = pipeline.process_staff_notes("Patient asks for a follow-up.")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("card", result)

    def test_missing_required_model_fields_returns_error(self):
        card = make_card()
        del card["request"]
        result = self.process_with("Patient asks for a follow-up.", card)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("card", result)

    def test_ambiguous_request_and_owner_are_reviewed(self):
        card = make_card(
            caller="unknown",
            request="unclear",
            action_items=[
                {
                    "text": "Follow-up",
                    "action_required": "unknown",
                    "owner_team": "human_review",
                    "quote": "model text",
                    "timestamp": "",
                    "timestamp_sec": 0,
                    "confidence": 0.9,
                    "status": "verified",
                    "due_hint": "",
                }
            ],
        )
        result = self.process_with("Caller and next steps unclear.", card)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["routed"], [])
        self.assertTrue(result["needs_human_review"])
        self.assertIn("caller", result["unclear_fields"])
        self.assertIn("request", result["unclear_fields"])
        self.assertIn("action owner", result["unclear_fields"])
        self.assertIn("next step", result["unclear_fields"])

    def test_potentially_urgent_notes_require_immediate_human_review(self):
        card = make_card(urgency="routine", emergency_flag=False)
        result = self.process_with(
            "Staff notes: caller reports sudden weakness and slurred speech.",
            card,
        )
        self.assertTrue(result["potentially_urgent"])
        self.assertEqual(result["routed"], [])
        self.assertIn("immediate human review", result["review_reason"])

    def test_invalid_model_enum_returns_error(self):
        result = self.process_with(
            "Patient asks for a follow-up.",
            make_card(urgency="maybe"),
        )
        self.assertEqual(result["status"], "error")
        self.assertNotIn("card", result)


if __name__ == "__main__":
    unittest.main()
