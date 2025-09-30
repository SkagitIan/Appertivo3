RESEARCH_RESPONSE_SCHEMA = {
    "name": "article_research",
    "type": "json_schema",
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
                    "required": ["title", "url", "snippet", "source"]
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
                            "required": ["heading", "paragraphs", "body"]
                        },
                    },
                    "text": {"type": "string"},
                },
                "required": ["title", "sections", "text"]
            },
        },
        "required": ["summary", "citations", "draft"],
    },
}

