"""Text generation with AURORA models: sampling, generation, quality measures."""

from aurora_core.generation.generate import (
    GenerationConfig,
    generate_ids,
    generate_text,
    stream_ids,
)
from aurora_core.generation.quality import BigramSampler, Reference, average_stats, text_stats
from aurora_core.generation.sampling import next_token_distribution, sample_next

__all__ = [
    "BigramSampler", "GenerationConfig", "Reference", "average_stats", "generate_ids",
    "generate_text", "next_token_distribution", "sample_next", "stream_ids", "text_stats",
]
