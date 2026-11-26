from sbs.corpus import clean
from tests.corpus.conftest import CLEAN_CFG, add_repo


def test_removes_code_blocks():
    text = "Intro\n\n```python\nimport os\n```\n\n~~~\nraw\n~~~\n\n    indented code\n\nOutro"
    out = clean.clean(text, 3)
    assert "import" not in out and "raw" not in out and "indented" not in out
    assert out.startswith("Intro") and out.endswith("Outro")


def test_inline_code_kept_when_short_and_html_removed():
    text = "Run `make all` or `npm run build -- --watch` now.<br><b>Bold</b> <!-- hidden --> <pre>x = 1</pre>"
    assert clean.clean(text, 3) == "Run make all or now. Bold"
    assert clean.clean(text, 0) == "Run or now. Bold"


def test_unpaired_backticks_do_not_swallow_text():
    text = "With `gitx -Shaha', gitx will\nonly show `this` commit"
    assert clean.clean(text, 3) == "With gitx -Shaha', gitx will\nonly show this commit"


def test_alerts_tables_quotes_and_reference_badges():
    text = ("> [!IMPORTANT]\n> **Moved.**\n\n| # | Name |\n|---|---|\n| 1 | foo |\n\n"
            "> ### Long-Polling\n\n[![Badge]][BadgeLink] text")
    assert clean.clean(text, 3) == "Moved.\n\nName\n\n1 foo\n\nLong-Polling\n\ntext"


def test_license_paragraphs_and_copyright_removed():
    text = ("Real intro.\n\n## License\n\nCopyright (c) 2020 Someone\n\n"
            "Licensed under the Apache License, Version 2.0 (the \"License\");\nyou may not use this file.\n\n"
            "Permission is hereby granted, free of charge, to any person.\n\nThanks.")
    assert clean.clean(text, 3) == "Real intro.\n\nLicense\n\nThanks."


def test_removes_badges_and_images_keeps_link_text():
    text = ("[![Build](https://ci/badge.svg)](https://ci) ![logo](logo.png) <img src='a.png'>\n"
            "See the [docs](https://example.com/docs) and [guide][g].\n\n[g]: https://example.com/g")
    assert clean.clean(text, 3) == "See the docs and guide."


def test_removes_urls_and_emails():
    out = clean.clean("Visit https://example.com or www.example.org, mail me@example.com <https://x.y>", 3)
    assert out == "Visit or , mail"
    assert "example" not in out


def test_strips_markdown_markup():
    text = "# Title #\n\n> quoted *text*\n\n- item one\n2. **item** two\n\n---\n\n| a | b |\n|---|:-:|\n| c | d |"
    assert clean.clean(text, 3) == "Title\n\nquoted text\n\nitem one\nitem two\n\na b\n\nc d"


def test_keeps_snake_case_and_trailing_punctuation():
    assert clean.clean("Use snake_case_name (see https://a.b/c). __Done__", 3) == "Use snake_case_name (see ). Done"


def test_unescapes_entities():
    assert clean.clean("Tom &amp; Jerry &lt;3", 3) == "Tom & Jerry <3"


def test_run_skips_missing_readmes(store):
    add_repo(store, 1, "# Hello")
    add_repo(store, 2, None)
    add_repo(store, 3, "![only](img.png)")
    assert clean.run(store, CLEAN_CFG) == 1
    assert store.db.execute("SELECT repo_id, text FROM docs_clean").fetchall() == [(1, "Hello")]
