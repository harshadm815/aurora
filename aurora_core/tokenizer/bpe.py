"""
Byte-level Byte Pair Encoding (BPE), the subword method used by GPT-2.

Text is turned into UTF-8 bytes, so the 256 byte values form the base
vocabulary: any text in any language (or emoji) can be encoded, and
there is never an unknown token. Training then repeatedly finds the most
frequent pair of adjacent tokens and adds it as a new token ("merge"),
until the vocabulary reaches the requested size. Frequent words end up
as single tokens; rare words are spelled out in pieces.

Token ids:
    0-3        special tokens (<pad>, <unk>, <bos>, <eos>)
    4-259      the 256 byte values (byte b has id 4 + b)
    260-...    merges, in the order they were learned

Before counting pairs, text is split into chunks (words with their
leading space, numbers of up to 3 digits, punctuation runs, whitespace),
so merges never join the end of one word to the start of the next.
"""

import heapq
import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from aurora_core.tokenizer.base import Tokenizer
from aurora_core.tokenizer.special import NUM_SPECIAL, SPECIAL_TOKENS, UNK_ID

BYTE_OFFSET = NUM_SPECIAL
FIRST_MERGE_ID = BYTE_OFFSET + 256
MIN_VOCAB_SIZE = FIRST_MERGE_ID

# Every character falls into exactly one branch (a letter, a digit, a
# space, or anything else), so the chunks always join back into the text.
_CHUNK = re.compile(r"\s?[^\W\d_]+|\s?\d{1,3}|\s?(?:[^\s\w]|_)+|\s+(?!\S)|\s+")

_CACHE_LIMIT = 20_000


def pretokenize(text: str) -> list[str]:
    """Split text into the chunks BPE works within."""
    return _CHUNK.findall(text)


def _pairs(ids: list[int]):
    return zip(ids, ids[1:])


def _merge(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    """Replace every non-overlapping occurrence of `pair` (left to right)."""
    out, i, n = [], 0, len(ids)
    while i < n:
        if i + 1 < n and ids[i] == pair[0] and ids[i + 1] == pair[1]:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out


def _to_bytes(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("text contains invalid Unicode (an unpaired surrogate)") from None


class ByteBPETokenizer(Tokenizer):
    kind = "byte_bpe"

    def __init__(self, merges: Iterable = (), normalization: str = "nfc"):
        super().__init__(normalization)
        self._merges: list[tuple[int, int]] = []
        self._ranks: dict[tuple[int, int], int] = {}
        self._bytes: list[bytes] = [bytes([b]) for b in range(256)]   # index = id - BYTE_OFFSET
        for merge in merges:
            if (not isinstance(merge, (list, tuple)) or len(merge) != 2
                    or not all(isinstance(x, int) and not isinstance(x, bool) for x in merge)):
                raise ValueError(f"each merge must be a pair of token ids, got {merge!r}")
            a, b = merge
            next_id = FIRST_MERGE_ID + len(self._merges)
            if not (BYTE_OFFSET <= a < next_id and BYTE_OFFSET <= b < next_id):
                raise ValueError(f"merge {len(self._merges)} uses an id that isn't defined yet: {merge!r}")
            pair = (a, b)
            if pair in self._ranks:
                raise ValueError(f"merge {pair} appears twice")
            self._ranks[pair] = len(self._merges)
            self._merges.append(pair)
            self._bytes.append(self._bytes[a - BYTE_OFFSET] + self._bytes[b - BYTE_OFFSET])
        self._cache: dict[str, tuple[int, ...]] = {}

    # --------------------------------------------------------------- training

    @classmethod
    def train(
        cls,
        texts: Iterable[str],
        vocab_size: int,
        *,
        min_frequency: int = 2,
        normalization: str = "nfc",
    ) -> "ByteBPETokenizer":
        """Learn merges from `texts` until the vocabulary has `vocab_size`
        tokens, or no pair occurs at least `min_frequency` times.

        Pair counts are updated incrementally and the most frequent pair is
        found with a heap, so each merge only touches the words containing
        it. Ties are broken by the smaller pair of ids, so training the
        same texts always gives the same tokenizer.
        """
        if not isinstance(vocab_size, int) or vocab_size < MIN_VOCAB_SIZE:
            raise ValueError(f"vocab_size must be at least {MIN_VOCAB_SIZE} (special tokens + 256 bytes)")
        if min_frequency < 1:
            raise ValueError("min_frequency must be at least 1")

        helper = cls(normalization=normalization)
        chunk_counts: Counter[str] = Counter()
        for text in texts:
            chunk_counts.update(pretokenize(helper.normalize(text)))

        words = [[BYTE_OFFSET + b for b in _to_bytes(chunk)] for chunk in chunk_counts]
        counts = list(chunk_counts.values())

        pair_counts: dict[tuple[int, int], int] = defaultdict(int)
        where: dict[tuple[int, int], set[int]] = defaultdict(set)
        for i, word in enumerate(words):
            for pair in _pairs(word):
                pair_counts[pair] += counts[i]
                where[pair].add(i)
        heap = [(-count, pair) for pair, count in pair_counts.items()]
        heapq.heapify(heap)

        merges: list[tuple[int, int]] = []
        next_id = FIRST_MERGE_ID
        while next_id < vocab_size:
            best = None
            while heap:
                neg_count, pair = heapq.heappop(heap)
                if pair_counts.get(pair, 0) == -neg_count > 0:   # skip stale entries
                    best = pair
                    break
            if best is None or pair_counts[best] < min_frequency:
                break

            touched: set[tuple[int, int]] = set()
            for i in sorted(where.pop(best)):
                word = words[i]
                if not any(p == best for p in _pairs(word)):
                    continue                                     # stale index
                for pair in _pairs(word):
                    pair_counts[pair] -= counts[i]
                    touched.add(pair)
                word = _merge(word, best, next_id)
                words[i] = word
                for pair in _pairs(word):
                    pair_counts[pair] += counts[i]
                    where[pair].add(i)
                    touched.add(pair)
            for pair in touched:
                count = pair_counts[pair]
                if count > 0:
                    heapq.heappush(heap, (-count, pair))
                else:
                    pair_counts.pop(pair, None)

            merges.append(best)
            next_id += 1

        return cls(merges, normalization)

    # ------------------------------------------------------ encode / decode

    @property
    def vocab_size(self) -> int:
        return FIRST_MERGE_ID + len(self._merges)

    @property
    def merges(self) -> list[tuple[int, int]]:
        return list(self._merges)

    def _encode_chunk(self, chunk: str) -> tuple[int, ...]:
        cached = self._cache.get(chunk)
        if cached is not None:
            return cached
        ids = [BYTE_OFFSET + b for b in _to_bytes(chunk)]
        # Apply the earliest-learned merge present, again and again; this
        # reproduces exactly the segmentation training arrived at.
        while len(ids) > 1:
            ranked = [(self._ranks[p], p) for p in _pairs(ids) if p in self._ranks]
            if not ranked:
                break
            rank, pair = min(ranked)
            ids = _merge(ids, pair, FIRST_MERGE_ID + rank)
        result = tuple(ids)
        if len(self._cache) >= _CACHE_LIMIT:
            self._cache.clear()
        self._cache[chunk] = result
        return result

    def _encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for chunk in pretokenize(text):
            ids.extend(self._encode_chunk(chunk))
        return ids

    def _decode(self, ids: list[int], skip_special: bool) -> str:
        parts: list[str] = []
        buffer = bytearray()
        for i in ids:
            if i >= BYTE_OFFSET:
                buffer += self._bytes[i - BYTE_OFFSET]
                continue
            if i == UNK_ID or not skip_special:
                parts.append(buffer.decode("utf-8", errors="replace"))
                buffer.clear()
                parts.append("�" if skip_special else SPECIAL_TOKENS[i])
        # Ids that don't form complete UTF-8 characters (possible when a
        # model generates them) show as �, rather than raising an error.
        parts.append(buffer.decode("utf-8", errors="replace"))
        return "".join(parts)

    # ------------------------------------------------------------ vocabulary

    def token_bytes(self, token_id: int) -> bytes:
        """The bytes a (non-special) token stands for."""
        token_id = self._check_ids([token_id])[0]
        if token_id < BYTE_OFFSET:
            raise ValueError(f"{SPECIAL_TOKENS[token_id]} is a special token and has no bytes")
        return self._bytes[token_id - BYTE_OFFSET]

    def id_to_token(self, token_id: int) -> str:
        """Readable form: the special token's name, the token's text, or,
        for bytes that aren't complete UTF-8, e.g. "<0xE2>"."""
        token_id = self._check_ids([token_id])[0]
        if token_id < BYTE_OFFSET:
            return SPECIAL_TOKENS[token_id]
        raw = self._bytes[token_id - BYTE_OFFSET]
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return "".join(f"<0x{b:02X}>" for b in raw)

    def token_to_id(self, token: str | bytes) -> int:
        """Id of a special token name, or of the token for these bytes/text."""
        if isinstance(token, str):
            if token in SPECIAL_TOKENS:
                return SPECIAL_TOKENS.index(token)
            token = _to_bytes(token)
        if not hasattr(self, "_byte_index"):
            self._byte_index = {raw: BYTE_OFFSET + i for i, raw in enumerate(self._bytes)}
        if token not in self._byte_index:
            raise KeyError(f"{token!r} is not a single token")
        return self._byte_index[token]

    # ----------------------------------------------------------- persistence

    def _state(self) -> dict:
        return {"merges": [list(pair) for pair in self._merges]}

    @classmethod
    def _from_state(cls, state: dict, normalization: str) -> "ByteBPETokenizer":
        merges = state.get("merges")
        if not isinstance(merges, list):
            raise ValueError("'merges' must be a list")
        return cls(merges, normalization)
