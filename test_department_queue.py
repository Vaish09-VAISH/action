import unittest
from datetime import datetime

import agent


class DepartmentQueueTests(unittest.TestCase):
    def test_message_contains_required_fields_and_utc_timestamp(self):
        message = agent.create_department_message(
            "call.txt",
            "billing",
            "Check the invoice",
        )

        self.assertEqual(message["transcript_file"], "call.txt")
        self.assertEqual(message["department"], "billing")
        self.assertEqual(message["action_item"], "Check the invoice")
        self.assertEqual(message["status"], "Sent")
        self.assertIsNotNone(datetime.fromisoformat(message["timestamp"]).tzinfo)

    def test_medical_records_is_a_supported_department(self):
        message = agent.create_department_message(
            "call.txt",
            "medical_records",
            "Send the requested record",
        )

        self.assertEqual(message["department"], "medical_records")

    def test_unknown_department_is_rejected(self):
        with self.assertRaises(ValueError):
            agent.create_department_message(
                "call.txt",
                "human_review",
                "Review this request",
            )

    def test_empty_message_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            agent.create_department_message("", "billing", "Check the invoice")


if __name__ == "__main__":
    unittest.main()
