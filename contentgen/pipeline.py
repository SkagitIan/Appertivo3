"""Simple content generation pipeline steps."""
from dataclasses import dataclass
from typing import List, Tuple, Dict


@dataclass
class ContentPipeline:
    """Collection of helper methods for content generation."""

    def brainstorm_ideas(self) -> List[str]:
        """Return a list of brainstormed ideas."""
        return ["Idea 1", "Idea 2", "Idea 3"]

    def score_and_pick(self, ideas: List[str]) -> List[Tuple[str, float]]:
        """Return ideas ranked by a score."""
        return [(idea, 1.0 - idx * 0.1) for idx, idea in enumerate(ideas)]

    def make_brief(self, idea: str) -> str:
        """Create a short brief for the chosen idea."""
        return f"Brief for {idea}"

    def draft_article(self, research: Dict[str, List[str]]) -> str:
        """Generate a draft article from research."""
        sources = ", ".join(research.get("sources", []))
        return f"Draft using sources: {sources}"

    def edit_article(self, draft: str) -> str:
        """Return an edited version of the draft."""
        return draft + "\n\nEdited for clarity."

    def make_seo(self, article: str) -> Dict[str, str]:
        """Return SEO metadata for the article."""
        return {"meta_title": article[:50], "meta_description": article[:150]}

    def format_article(self, article: str) -> str:
        """Return formatted article content."""
        return f"<p>{article}</p>"
