# Embedding Models
class EmbeddingModels:
    BGE_LARGE = "qllama/bge-large-en-v1.5"
    NOMIC_EMBED = "nomic-embed-text"
    BGE_M3 = "bge-m3"


# Tokenizer Names
class TokenizerNames:
    BGE_LARGE = "BAAI/bge-large-en-v1.5"
    NOMIC_EMBED = "nomic-ai/nomic-embed-text-v1"
    BGE_M3 = "BAAI/bge-m3"


# Token Limits
class TokenLimits:
    BGE_LARGE = 512
    NOMIC_EMBED = 8192
    BGE_M3 = 8192


# Embedding Model Config (active selection)
ACTIVE_EMBEDDING_MODEL = EmbeddingModels.NOMIC_EMBED
ACTIVE_TOKENIZER = TokenizerNames.NOMIC_EMBED
ACTIVE_MAX_TOKENS = TokenLimits.NOMIC_EMBED

# Batch size
DEFAULT_EMBEDDING_BATCH_SIZE = 16