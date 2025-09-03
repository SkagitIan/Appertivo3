"""Minimal OpenAI client stub for tests."""

class OpenAI:
    """Very small subset of the OpenAI client used in tests."""

    class chat:
        class completions:
            @staticmethod
            def create(*args, **kwargs):
                class Response:
                    choices = [type("Choice", (), {"message": type("Msg", (), {"content": ""})()})()]
                return Response()
