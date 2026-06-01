import unittest

from app.debate_engine import DebateEngine
from app.models import Advisor, AppSettings
from app.state import DebateSession


def make_advisor() -> Advisor:
    return Advisor(
        id='anthropologist',
        name='Antropológus',
        title='Kultúra és emberi viselkedés',
        description='Szokások, csoportdinamika, jelentések, társadalmi következmények.',
        avatar='/app/static/avatars/anthropologist.svg',
        accent_color='#6ac47e',
        llm_prompt='Te antropológus vagy. Erős, karakteres hangon beszélsz, de a konkrét emberi szokásokra figyelsz.',
        voice_mode='instruct',
        voice_instruct='male, moderate pitch',
    )


def make_session() -> DebateSession:
    session = DebateSession(id='debate-1', topic='Do the French always eat snails?', advisor_ids=['anthropologist'])
    session.scores = {'anthropologist': 0}
    return session


class DebateEnginePromptTests(unittest.TestCase):
    def setUp(self):
        self.settings = AppSettings()
        self.advisor = make_advisor()
        self.engine = DebateEngine(self.settings, [self.advisor], None, None)
        self.session = make_session()
        self.language_context = {'prompt_name': 'English', 'summary_name': 'English', 'tts_code': 'en'}

    def test_finalize_turn_text_strips_constraint_meta_prefix(self):
        cleaned = self.engine.finalize_turn_text(
            'Review against constraints: all constraints met. No, snails are not an everyday staple in France.'
        )
        self.assertEqual(cleaned, 'No, snails are not an everyday staple in France.')

    def test_finalize_turn_text_keeps_spoken_sentence_after_drafting_prefix(self):
        cleaned = self.engine.finalize_turn_text(
            "Here's a version: French people do eat snails, but mostly as a special dish, not an everyday habit."
        )
        self.assertEqual(
            cleaned,
            'French people do eat snails, but mostly as a special dish, not an everyday habit.',
        )

    def test_build_turn_messages_include_topic_first_guardrails(self):
        system, user = self.engine.build_turn_messages(
            self.session,
            self.advisor,
            round_label='1. kör',
            conversation_history='No previous messages yet.',
            language_context=self.language_context,
            own_history='You have not spoken yet.',
            others_history='No one else has spoken yet.',
            previous_turn=None,
            turn_profile={'label': '1 short sentence', 'instruction': 'Make one point cleanly and stop.'},
            turn_speech_act={'label': 'sharp opening claim', 'instruction': 'Open with one sharp claim.'},
            turn_reply_mode={'label': 'opening move', 'instruction': 'Frame one sharp angle and invite a response.'},
        )
        self.assertIn('The literal meeting topic comes first.', system)
        self.assertIn('Your persona is a way of seeing the topic', system)
        self.assertIn('Do not output meta-text', user)


if __name__ == '__main__':
    unittest.main()
