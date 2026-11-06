"""Special tokens. Every AURORA tokenizer gives them the same ids, 0-3."""

PAD = "<pad>"   # fills unused positions in a batch
UNK = "<unk>"   # a character the vocabulary doesn't contain (character tokenizer only)
BOS = "<bos>"   # beginning of a sequence
EOS = "<eos>"   # end of a sequence

SPECIAL_TOKENS: tuple[str, ...] = (PAD, UNK, BOS, EOS)
PAD_ID, UNK_ID, BOS_ID, EOS_ID = range(len(SPECIAL_TOKENS))
NUM_SPECIAL = len(SPECIAL_TOKENS)

# Special tokens are never produced from text: encoding the string
# "<eos>" gives the ids of its characters, not EOS_ID. Only the add_bos /
# add_eos options add them. So text typed by a user (or found in a
# document) can't pretend to end a sequence or inject control tokens.
