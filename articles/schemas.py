RESEARCH_RESPONSE_SCHEMA = {
    "name": "article_research",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "citations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "snippet": {"type": "string"},
                        "source": {"type": "string"},
                    },
                    "required": ["title", "url"],
                },
            },
            "draft": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "heading": {"type": "string"},
                                "paragraphs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "body": {"type": "string"},
                            },
                            "required": ["heading"],
                        },
                    },
                    "text": {"type": "string"},
                },
            },
        },
        "required": ["summary", "citations", "draft"],
    },
}
