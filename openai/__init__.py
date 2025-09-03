"""Minimal stub for the OpenAI client used in tests."""


class DummyResponses:
    """Provide a stubbed interface for `client.responses`."""

    def create(self, **kwargs):
        """Return an empty response object."""
        class R:
            usage = None
            output = []
            output_text = ""

        return R()


class OpenAI:
    """Tiny stand-in for the real OpenAI client."""

    def __init__(self, *args, **kwargs):
        self.responses = DummyResponses()
