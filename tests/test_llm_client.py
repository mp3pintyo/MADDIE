import unittest

from app.llm_client import _extract_answer_from_reasoning


class LlmClientReasoningTests(unittest.TestCase):
    def test_extract_answer_from_reasoning_rejects_constraint_checklist(self):
        reasoning = '1. Check constraints\n2. Constraints met\n3. Final output generation\n'
        self.assertEqual(_extract_answer_from_reasoning(reasoning), '')


if __name__ == '__main__':
    unittest.main()
