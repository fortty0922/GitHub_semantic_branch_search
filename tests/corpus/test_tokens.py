from sbs.corpus import tokens


def test_keeps_technical_tokens():
    sentences = tokens.tokenize("Built with C++, C# and Node.js on .NET using real-time snake_case APIs")
    assert sentences == [["built", "with", "c++", "c#", "and", "node.js", "on", ".net", "using",
                          "real-time", "snake_case", "apis"]]


def test_splits_sentences_and_strips_punctuation():
    assert tokens.tokenize("A fast pipeline. It uses Python 3.12!\nNext: done") == [
        ["a", "fast", "pipeline"], ["it", "uses", "python", "3.12"], ["next"], ["done"]]


def test_stop_words_kept():
    assert tokens.tokenize("the art of the kernel") == [["the", "art", "of", "the", "kernel"]]


def test_serialize_round_trip():
    s = [["a", "b"], ["c"]]
    assert tokens.deserialize(tokens.serialize(s)) == s
