"""Minimal stub OpenAI client for offline tests."""


class OpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def responses_create(self, *args, **kwargs):
        class Resp:
            def output_text(self):
                return ""

        return Resp()
