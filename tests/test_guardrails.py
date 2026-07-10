from __future__ import annotations

import unittest
from uuid import UUID

from minwon_agents.guardrails import (
    InputGuard,
    InputValidationError,
    contains_pii,
    mask_pii,
    mask_pii_with_report,
    prepare_intake,
    validate_input,
)


class InputValidationTests(unittest.TestCase):
    def test_valid_input_is_trimmed_and_gets_unique_uuid(self) -> None:
        first = validate_input(
            request_id=" REQ-1 ", title=" 수당 문의 ", body="  본문\n내용  "
        )
        second = validate_input(request_id="REQ-1", title="제목", body="본문")
        self.assertEqual(first.request_id, "REQ-1")
        self.assertEqual(first.title, "수당 문의")
        self.assertEqual(first.body, "본문\n내용")
        self.assertEqual(str(UUID(first.run_id)), first.run_id)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_required_fields_and_types_are_strict(self) -> None:
        bad_values = (None, "", "   ", 123)
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(InputValidationError):
                    validate_input(request_id="REQ", title=value, body="본문")

    def test_length_and_control_character_guards(self) -> None:
        guard = InputGuard(max_request_id_chars=5, max_title_chars=5, max_body_chars=5)
        with self.assertRaisesRegex(InputValidationError, "at most 5"):
            guard.validate(request_id="REQ", title="123456", body="본문")
        with self.assertRaisesRegex(InputValidationError, "control character"):
            guard.validate(request_id="REQ", title="제목", body="본\x00문")
        with self.assertRaisesRegex(InputValidationError, "single line"):
            guard.validate(request_id="REQ\n2", title="제목", body="본문")

    def test_invalid_supplied_run_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(InputValidationError, "valid UUID"):
            validate_input(
                request_id="REQ", title="제목", body="본문", run_id="not-a-uuid"
            )


class PiiMaskingTests(unittest.TestCase):
    def test_masks_identity_email_and_phone(self) -> None:
        raw = (
            "주민번호 900101-1234567, 이메일 user.name+tag@example.co.kr, "
            "휴대폰 010-1234-5678"
        )
        result = mask_pii_with_report(raw)
        self.assertEqual(result.identity_numbers, 1)
        self.assertEqual(result.emails, 1)
        self.assertEqual(result.phones, 1)
        self.assertEqual(result.total, 3)
        self.assertIn("[ID_NUMBER]", result.text)
        self.assertIn("[EMAIL]", result.text)
        self.assertIn("[PHONE]", result.text)
        self.assertFalse(contains_pii(result.text))

    def test_masks_unseparated_identity_and_common_phone_formats(self) -> None:
        masked = mask_pii("9001011234567 / 02-123-4567 / +82 10 9876 5432")
        self.assertIn("[ID_NUMBER]", masked)
        self.assertEqual(masked.count("[PHONE]"), 2)

    def test_non_pii_text_is_unchanged(self) -> None:
        text = "시간외근무수당 지급 기준을 문의합니다."
        self.assertEqual(mask_pii(text), text)
        self.assertFalse(contains_pii(text))

    def test_masker_rejects_implicit_type_conversion(self) -> None:
        with self.assertRaises(TypeError):
            mask_pii(123)  # type: ignore[arg-type]


class IntakePreparationTests(unittest.TestCase):
    def test_prepare_intake_separates_raw_and_model_safe_text(self) -> None:
        intake = prepare_intake(
            request_id="REQ-25",
            title="user@example.com 문의",
            body="연락처는 010-1111-2222입니다.",
        )
        self.assertEqual(intake.original_title, "user@example.com 문의")
        self.assertEqual(intake.original_body, "연락처는 010-1111-2222입니다.")
        self.assertEqual(intake.masked_title, "[EMAIL] 문의")
        self.assertEqual(intake.masked_body, "연락처는 [PHONE]입니다.")
        self.assertTrue(intake.pii_masked)

    def test_prepare_intake_keeps_supplied_run_id(self) -> None:
        run_id = "12345678-1234-5678-9234-567812345678"
        intake = InputGuard().prepare(
            request_id="REQ", title="제목", body="본문", run_id=run_id
        )
        self.assertEqual(intake.run_id, run_id)
        self.assertFalse(intake.pii_masked)


if __name__ == "__main__":
    unittest.main()
