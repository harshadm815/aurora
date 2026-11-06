"""Phase 3: tokenizers (normalisation, character tokenizer, byte-level BPE)."""

import json
import random

import numpy as np
import pytest

from aurora_core.tokenizer import (
    BOS_ID,
    EOS_ID,
    PAD_ID,
    SPECIAL_TOKENS,
    UNK_ID,
    ByteBPETokenizer,
    CharTokenizer,
    Tokenizer,
    TokenizerFileError,
    load_tokenizer,
    normalize,
    pretokenize,
)
from aurora_core.tokenizer.bpe import BYTE_OFFSET, FIRST_MERGE_ID

CORPUS = [
    "The quick brown fox jumps over the lazy dog. " * 5,
    "AURORA remembers that Harry lives in Nagpur and studies astronomy.\n" * 4,
    "Temperatures: 21.5 °C at 10:30, 23 °C at 12:00; humidity 45 %.",
    "the theory of the thermal thermometer, then the thesis",
]

TRICKY = [
    "",
    " ",
    "a",
    "  leading and trailing spaces  ",
    "tabs\tand\nnew\nlines\n\n",
    "Ünïcödé — “quotes” … ½ ² ﬁ",
    "emoji 🚀👩‍🚀 with a zero-width joiner",
    "नमस्ते दुनिया",
    "日本語のテキスト",
    "مرحبا",
    "control \x00\x01\x7f characters",
    "snake_case and __dunder__ and CamelCase123456",
    "<eos> <bos> <pad> <unk> look like special tokens",
    "1234567890 3.14159 -42",
    "é",                      # decomposed é: NFC composes it
]


def random_texts(count, seed=0):
    rng = random.Random(seed)
    alphabet = (
        "abcdefghijklmnopqrstuvwxyz ABCXYZ 0123456789 .,;:!?'\"()[]{}_-+*/\\\n\t"
        "éüßçñ€£¥°±µ¶ αβγδ абв 中文字 あいう 한국 🙂🚀✨ ‍́"
    )
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60))) for _ in range(count)]


@pytest.fixture(scope="module")
def bpe():
    return ByteBPETokenizer.train(CORPUS, vocab_size=400)


# ----------------------------------------------------------- normalisation


def test_normalize_line_endings_and_nfc():
    assert normalize("a\r\nb\rc") == "a\nb\nc"
    assert normalize("é") == "é"
    assert normalize("é\r\n", "none") == "é\r\n"


def test_normalize_rejects_bad_input():
    with pytest.raises(TypeError):
        normalize(b"bytes")
    with pytest.raises(ValueError):
        normalize("x", "nfkc")
    with pytest.raises(ValueError):
        ByteBPETokenizer(normalization="lowercase")


# ----------------------------------------------------------- special tokens


@pytest.mark.parametrize("tok", [CharTokenizer("abcd<>eosnp"), ByteBPETokenizer()])
def test_special_token_ids_are_fixed(tok):
    assert SPECIAL_TOKENS == ("<pad>", "<unk>", "<bos>", "<eos>")
    assert (PAD_ID, UNK_ID, BOS_ID, EOS_ID) == (0, 1, 2, 3)
    assert [tok.id_to_token(i) for i in range(4)] == list(SPECIAL_TOKENS)
    assert [tok.token_to_id(t) for t in SPECIAL_TOKENS] == [0, 1, 2, 3]


@pytest.mark.parametrize("tok", [CharTokenizer("abcd<>eosnp"), ByteBPETokenizer()])
def test_text_can_never_produce_special_tokens(tok):
    ids = tok.encode("<eos><bos><pad>")
    assert not set(ids) & {PAD_ID, BOS_ID, EOS_ID}
    assert tok.decode(ids) == "<eos><bos><pad>"


@pytest.mark.parametrize("tok", [CharTokenizer("hi"), ByteBPETokenizer()])
def test_bos_eos_options_and_decoding_specials(tok):
    ids = tok.encode("hi", add_bos=True, add_eos=True)
    assert ids[0] == BOS_ID and ids[-1] == EOS_ID
    assert tok.decode(ids) == "hi"
    assert tok.decode(ids, skip_special=False) == "<bos>hi<eos>"
    assert tok.decode([PAD_ID, PAD_ID] + ids) == "hi"


@pytest.mark.parametrize("tok", [CharTokenizer("ab"), ByteBPETokenizer()])
def test_decode_validates_ids(tok):
    with pytest.raises(ValueError):
        tok.decode([tok.vocab_size])
    with pytest.raises(ValueError):
        tok.decode([-1])
    with pytest.raises(ValueError):
        tok.decode([1.5])
    with pytest.raises(ValueError):
        tok.decode([True])
    with pytest.raises(TypeError):
        tok.decode("ab")
    assert tok.decode(np.array(tok.encode("ab"))) == "ab"
    assert tok.decode(np.array(tok.encode("ab"), dtype=np.int32)) == "ab"


# ------------------------------------------------------- character tokenizer


def test_char_vocabulary_is_ordered_by_frequency():
    tok = CharTokenizer.train(["aaabbc"])
    assert [tok.id_to_token(i) for i in range(4, tok.vocab_size)] == ["a", "b", "c"]
    assert tok.encode("cab") == [6, 4, 5]


def test_char_round_trip_of_known_characters():
    tok = CharTokenizer.train(CORPUS)
    for text in CORPUS:
        assert tok.decode(tok.encode(text)) == text


def test_char_unknown_characters_become_unk():
    tok = CharTokenizer.train(["abc"])
    ids = tok.encode("abz")
    assert ids[-1] == UNK_ID
    assert tok.decode(ids) == "ab�"
    assert tok.decode(ids, skip_special=False) == "ab<unk>"


def test_char_max_vocab_and_min_frequency():
    tok = CharTokenizer.train(["aaaabbbcc d"], max_vocab=6)
    assert tok.vocab_size == 6
    assert [tok.id_to_token(i) for i in (4, 5)] == ["a", "b"]
    tok = CharTokenizer.train(["aaaabbbcc d"], min_frequency=3)
    assert [tok.id_to_token(i) for i in range(4, tok.vocab_size)] == ["a", "b"]
    with pytest.raises(ValueError):
        CharTokenizer.train(["abc"], max_vocab=4)


def test_char_constructor_validation():
    with pytest.raises(ValueError):
        CharTokenizer(["a", "a"])
    with pytest.raises(ValueError):
        CharTokenizer(["ab"])
    with pytest.raises(KeyError):
        CharTokenizer("ab").token_to_id("z")


# ---------------------------------------------------------- pre-tokenization


@pytest.mark.parametrize("text", TRICKY + random_texts(300))
def test_pretokenize_never_loses_characters(text):
    assert "".join(pretokenize(text)) == text


def test_pretokenize_keeps_words_and_their_leading_space():
    assert pretokenize("Hello world, it's 2026!") == [
        "Hello", " world", ",", " it", "'", "s", " 202", "6", "!",
    ]


# ---------------------------------------------------------------------- BPE


def test_untrained_bpe_is_plain_bytes():
    tok = ByteBPETokenizer()
    assert tok.vocab_size == 260
    assert tok.encode("A€") == [BYTE_OFFSET + b for b in "A€".encode()]


@pytest.mark.parametrize("text", TRICKY + random_texts(300, seed=1))
def test_bpe_round_trip_is_lossless(bpe, text):
    """Phase 3's completion condition: nothing is lost."""
    assert bpe.decode(bpe.encode(text)) == normalize(text)


def test_bpe_round_trip_without_normalization():
    tok = ByteBPETokenizer.train(CORPUS, vocab_size=300, normalization="none")
    for text in ["é", "a\r\nb", *TRICKY]:
        assert tok.decode(tok.encode(text)) == text


def test_bpe_never_produces_unknown_tokens(bpe):
    for text in random_texts(100, seed=2):
        assert UNK_ID not in bpe.encode(text)


def test_bpe_rejects_unpaired_surrogates(bpe):
    with pytest.raises(ValueError):
        bpe.encode("bad \ud800 text")


def test_bpe_first_merge_is_the_most_frequent_pair():
    tok = ByteBPETokenizer.train(["aaab"], vocab_size=261, min_frequency=1)
    a, b = BYTE_OFFSET + ord("a"), BYTE_OFFSET + ord("b")
    assert tok.merges == [(a, a)]
    assert tok.encode("aaab") == [FIRST_MERGE_ID, a, b]


def test_bpe_training_respects_vocab_size():
    big = " ".join(CORPUS) * 3 + " ".join(random_texts(200, seed=9))
    tok = ByteBPETokenizer.train([big], vocab_size=300, min_frequency=1)
    assert tok.vocab_size == 300


def test_bpe_training_stops_early_on_a_small_corpus(bpe):
    # The test corpus runs out of pairs seen at least twice before 400.
    assert 300 < bpe.vocab_size <= 400


def test_bpe_compresses_repetitive_text(bpe):
    plain = ByteBPETokenizer()
    for text in CORPUS[:2]:
        assert len(bpe.encode(text)) < len(plain.encode(text)) / 4
    for text in CORPUS:
        assert len(bpe.encode(text)) < len(plain.encode(text))


def test_bpe_frequent_words_become_single_tokens(bpe):
    assert bpe.tokens(" quick") == [" quick"]
    assert bpe.tokens("AURORA") == ["AURORA"]


def test_bpe_merges_stay_inside_words(bpe):
    for i in range(FIRST_MERGE_ID, bpe.vocab_size):
        assert len(pretokenize(bpe.token_bytes(i).decode("utf-8", errors="replace"))) == 1


def test_bpe_training_is_deterministic():
    a = ByteBPETokenizer.train(CORPUS, vocab_size=350)
    b = ByteBPETokenizer.train(list(CORPUS), vocab_size=350)
    assert a.merges == b.merges


def test_bpe_stops_when_no_pair_is_frequent_enough():
    tok = ByteBPETokenizer.train(["abcdef"], vocab_size=1000, min_frequency=2)
    assert tok.vocab_size == 260
    tok = ByteBPETokenizer.train(["ab ab ab"], vocab_size=1000, min_frequency=2)
    assert 260 < tok.vocab_size < 1000


def test_bpe_rejects_small_vocab():
    with pytest.raises(ValueError):
        ByteBPETokenizer.train(CORPUS, vocab_size=259)


def reference_bpe_encode(merges, text):
    """Independent check: apply each merge, in learned order, to every chunk."""
    ids = []
    for chunk in pretokenize(normalize(text)):
        word = [BYTE_OFFSET + b for b in chunk.encode()]
        for rank, pair in enumerate(merges):
            out, i = [], 0
            while i < len(word):
                if i + 1 < len(word) and (word[i], word[i + 1]) == pair:
                    out.append(FIRST_MERGE_ID + rank)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = out
        ids.extend(word)
    return ids


@pytest.mark.parametrize("text", CORPUS + TRICKY + random_texts(50, seed=3))
def test_bpe_encoding_matches_reference(bpe, text):
    assert bpe.encode(text) == reference_bpe_encode(bpe.merges, text)


def test_bpe_partial_utf8_decodes_to_replacement_character():
    tok = ByteBPETokenizer()
    first_byte_of_emoji = BYTE_OFFSET + "🚀".encode()[0]
    assert tok.decode([first_byte_of_emoji]) == "�"
    assert tok.decode([BYTE_OFFSET + ord("a"), first_byte_of_emoji, BYTE_OFFSET + ord("b")]) == "a�b"
    assert tok.id_to_token(first_byte_of_emoji) == "<0xF0>"


def test_bpe_token_lookup_round_trip(bpe):
    for i in range(BYTE_OFFSET, bpe.vocab_size):
        assert bpe.token_to_id(bpe.token_bytes(i)) == i
    assert bpe.token_to_id(" quick") == bpe.encode(" quick")[0]
    with pytest.raises(KeyError):
        bpe.token_to_id("definitely not one token")
    with pytest.raises(ValueError):
        bpe.token_bytes(EOS_ID)


def test_bpe_constructor_validates_merges():
    a = BYTE_OFFSET + ord("a")
    with pytest.raises(ValueError):
        ByteBPETokenizer([(a, FIRST_MERGE_ID)])         # refers to itself / the future
    with pytest.raises(ValueError):
        ByteBPETokenizer([(EOS_ID, a)])                 # special tokens can't merge
    with pytest.raises(ValueError):
        ByteBPETokenizer([(a, a), (a, a)])              # duplicate
    with pytest.raises(ValueError):
        ByteBPETokenizer([(a,)])
    with pytest.raises(ValueError):
        ByteBPETokenizer([(a, True)])


# -------------------------------------------------------------- persistence


@pytest.mark.parametrize("make", [
    lambda: CharTokenizer.train(CORPUS),
    lambda: ByteBPETokenizer.train(CORPUS, vocab_size=330),
    lambda: ByteBPETokenizer.train(CORPUS, vocab_size=300, normalization="none"),
])
def test_save_and_load_round_trip(tmp_path, make):
    tok = make()
    path = tmp_path / "sub" / "tokenizer.json"
    tok.save(path)
    loaded = load_tokenizer(path)
    assert type(loaded) is type(tok)
    assert loaded.vocab_size == tok.vocab_size
    assert loaded.normalization == tok.normalization
    for text in CORPUS + TRICKY:
        assert loaded.encode(text) == tok.encode(text)
    assert not list(tmp_path.rglob("*.tmp"))
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "aurora-tokenizer"


def write(tmp_path, data):
    path = tmp_path / "tok.json"
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def valid_file_data():
    return {"format": "aurora-tokenizer", "version": 1, "type": "byte_bpe",
            "normalization": "nfc", "special_tokens": list(SPECIAL_TOKENS), "merges": []}


@pytest.mark.parametrize("change", [
    {"format": "something-else"},
    {"version": 2},
    {"type": "sentencepiece"},
    {"normalization": "nfkd"},
    {"special_tokens": ["<pad>", "<unk>"]},
    {"merges": "not a list"},
    {"merges": [[4, 999]]},
    {"merges": [[4, 4], [4, 4]]},
])
def test_load_rejects_bad_files(tmp_path, change):
    with pytest.raises(TokenizerFileError):
        load_tokenizer(write(tmp_path, {**valid_file_data(), **change}))


def test_load_rejects_non_json_and_missing_files(tmp_path):
    with pytest.raises(TokenizerFileError):
        load_tokenizer(write(tmp_path, "not json {"))
    with pytest.raises(TokenizerFileError):
        load_tokenizer(write(tmp_path, "[1, 2, 3]"))
    with pytest.raises(TokenizerFileError):
        load_tokenizer(tmp_path / "missing.json")
    assert isinstance(load_tokenizer(write(tmp_path, valid_file_data())), Tokenizer)


def test_bad_char_file_is_rejected(tmp_path):
    data = {**valid_file_data(), "type": "char", "characters": ["a", "bb"]}
    del data["merges"]
    with pytest.raises(TokenizerFileError):
        load_tokenizer(write(tmp_path, data))


# ---------------------------------------------------------- with AURORA-T0


def test_tokenizer_feeds_aurora_t0(bpe):
    from aurora_core.transformer import AuroraT0, T0Config

    model = AuroraT0(T0Config(vocab_size=bpe.vocab_size), seed=0)
    ids = bpe.encode("AURORA remembers that Harry lives in Nagpur.", add_bos=True)
    assert len(ids) <= model.config.context_length
    logits = model(np.array(ids))
    assert logits.shape == (len(ids), bpe.vocab_size)
    # The model's output ids can be turned back into text (meaningless while untrained).
    assert isinstance(bpe.decode(logits.argmax(axis=-1)), str)
