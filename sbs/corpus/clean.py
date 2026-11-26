import html
import logging
import re

from sbs.common.store import Store

log = logging.getLogger("sbs")

FENCED = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.S | re.M)
INDENTED = re.compile(r"(?:\A|\n[ \t]*\n)((?:(?: {4}|\t)[^\n]*(?:\n|\Z))+)")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
HTML_CODE = re.compile(r"<(pre|code|script|style)\b[^>]*>.*?</\1\s*>", re.S | re.I)
LINKED_IMAGE = re.compile(r"\[\s*!\[[^\]]*\]\([^)]*\)\s*\]\([^)]*\)")
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]")
HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
REF_DEF = re.compile(r"^[ \t]*\[[^\]]+\]:[ \t]*\S+.*$", re.M)
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
HTML_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*\b[^>]*>")
AUTOLINK = re.compile(r"<(?:https?|ftp)://[^>]*>|<[^@\s>]+@[^@\s>]+>")
URL = re.compile(r"\b(?:https?://|ftp://|www\.)\S+?(?=[.,;:!?)\]'\"]*(?:\s|$))", re.I)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1", re.S)
HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*(.*?)[ \t#]*$", re.M)
SETEXT = re.compile(r"^[ \t]*(?:=+|-+)[ \t]*$", re.M)
HRULE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.M)
TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$", re.M)
TABLE_PIPE = re.compile(r"[ \t]*\|[ \t]*")
BLOCKQUOTE = re.compile(r"^[ \t]*(?:>[ \t]?)+", re.M)
LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?", re.M)
EMPHASIS = re.compile(r"(\*{1,3}|~~)(?=\S)(.+?)(?<=\S)\1|(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\3(?!\w)")
SPACES = re.compile(r"[ \t\r\f\v]+")
BLANKS = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = FENCED.sub("\n", text)
    text = HTML_COMMENT.sub(" ", text)
    text = HTML_CODE.sub(" ", text)
    text = INDENTED.sub("\n\n", text)
    text = LINKED_IMAGE.sub(" ", text)
    text = IMAGE.sub(" ", text)
    text = HTML_IMG.sub(" ", text)
    text = REF_DEF.sub("", text)
    text = LINK.sub(r"\1", text)
    text = REF_LINK.sub(r"\1", text)
    text = AUTOLINK.sub(" ", text)
    text = HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    text = URL.sub(" ", text)
    text = EMAIL.sub(" ", text)
    text = INLINE_CODE.sub(" ", text)
    text = HRULE.sub("", text)
    text = HEADING.sub(r"\1", text)
    text = SETEXT.sub("", text)
    text = TABLE_SEP.sub("", text)
    text = TABLE_PIPE.sub(" ", text)
    text = BLOCKQUOTE.sub("", text)
    text = LIST_MARKER.sub("", text)
    for _ in range(2):
        text = EMPHASIS.sub(lambda m: m.group(2) or m.group(4), text)
    lines = [SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return BLANKS.sub("\n\n", "\n".join(lines)).strip()


def run(store: Store) -> int:
    rows = store.iter_rows("SELECT id, readme FROM repos WHERE readme_status = 'ok' AND readme IS NOT NULL")
    cleaned = ((repo_id, text) for repo_id, readme in rows if (text := clean(readme)))
    count = store.replace_all("docs_clean", ("repo_id", "text"), cleaned)
    log.info("clean: %d documents", count)
    return count
