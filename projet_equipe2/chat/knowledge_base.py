import csv
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings
import pymupdf


CHUNK_WORDS = 180
CHUNK_OVERLAP = 30
CONTEXT_LIMIT = 5
PDF_SOURCE_LIMIT = 3
CHUNKS_PER_SOURCE_LIMIT = 2
EXCLUDED_KNOWLEDGE_FILES = {"QuestionsReponsesSources.docx"}
STOP_WORDS = {
    "au",
    "aux",
    "avec",
    "comment",
    "dans",
    "de",
    "des",
    "du",
    "elle",
    "elles",
    "est",
    "et",
    "la",
    "le",
    "les",
    "leur",
    "leurs",
    "mais",
    "mon",
    "notre",
    "nous",
    "pour",
    "que",
    "qui",
    "quoi",
    "son",
    "sont",
    "sur",
    "une",
    "vous",
    "votre",
}
WORD_PATTERN = re.compile(r"[a-z0-9]+")
WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class KnowledgeBaseError(Exception):
    """Raised when a knowledge-base source cannot be read."""


@dataclass(frozen=True)
class KnowledgeChunk:
    source: str
    text: str


def _tokens(text):
    split_compound_words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    normalized = unicodedata.normalize("NFKD", split_compound_words.casefold())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    tokens = []
    for token in WORD_PATTERN.findall(ascii_text):
        if len(token) > 1 and token not in STOP_WORDS:
            if token.endswith("s") and len(token) > 4 and not token[-2].isdigit():
                token = token[:-1]
            tokens.append(token)
    return tokens


def _split_into_chunks(text, source):
    words = text.split()
    if not words:
        return

    step = CHUNK_WORDS - CHUNK_OVERLAP
    for start in range(0, len(words), step):
        chunk_text = " ".join(words[start : start + CHUNK_WORDS]).strip()
        if chunk_text:
            yield KnowledgeChunk(source=source, text=chunk_text)
        if start + CHUNK_WORDS >= len(words):
            break


@lru_cache(maxsize=18)
def _read_pdf(path, modified_at, size):
    try:
        chunks = []
        with pymupdf.open(path) as document:
            for page_number, page in enumerate(document, start=1):
                text = page.get_text()
                source = f"{path.name}, page {page_number}"
                chunks.extend(_split_into_chunks(text, source))
        return tuple(chunks)
    except (OSError, ValueError, pymupdf.FileDataError) as error:
        raise KnowledgeBaseError(
            f"Impossible de lire le document PDF {path.name}."
        ) from error


def _read_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            document = ElementTree.fromstring(
                archive.read("word/document.xml")
            )
    except (OSError, KeyError, ElementTree.ParseError, zipfile.BadZipFile) as error:
        raise KnowledgeBaseError(
            f"Impossible de lire le document Word {path.name}."
        ) from error

    for paragraph in document.iter(f"{WORD_NAMESPACE}p"):
        text = "".join(
            node.text or ""
            for node in paragraph.iter(f"{WORD_NAMESPACE}t")
        )
        yield from _split_into_chunks(text, path.name)


def _field_name(header):
    return header.split(" [", 1)[0].strip()


def _field_definition(header):
    if " [" not in header:
        return ""
    return header.split(" [", 1)[1].rstrip("]").strip()


def _read_jsonl(path):
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            for row_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise KnowledgeBaseError(
                        f"Enregistrement JSON invalide dans {path.name}, "
                        f"ligne {row_number}."
                    )
                text = " | ".join(
                    f"{_field_name(str(key))}: {value}"
                    for key, value in row.items()
                    if value is not None and str(value).strip()
                )
                yield KnowledgeChunk(
                    source=f"{path.name}, ligne {row_number}",
                    text=text,
                )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KnowledgeBaseError(
            f"Impossible de lire les données JSONL {path.name}."
        ) from error


def _read_csv(path):
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            headers = reader.fieldnames or []

            for header in headers:
                definition = _field_definition(header)
                if definition:
                    yield KnowledgeChunk(
                        source=f"{path.name}, définition de "
                        f"{_field_name(header)}",
                        text=f"{_field_name(header)}: {definition}",
                    )

            for row_number, row in enumerate(reader, start=2):
                text = " | ".join(
                    f"{_field_name(str(key))}: {value}"
                    for key, value in row.items()
                    if key
                    and value is not None
                    and str(value).strip()
                )
                yield KnowledgeChunk(
                    source=f"{path.name}, ligne {row_number}",
                    text=text,
                )
    except (OSError, UnicodeError, csv.Error) as error:
        raise KnowledgeBaseError(
            f"Impossible de lire les données CSV {path.name}."
        ) from error


def _load_chunks(pdf_paths):
    knowledge_dir = settings.BASE_DIR / "Base_de_connaissance"
    if not knowledge_dir.is_dir():
        raise KnowledgeBaseError(
            "Le dossier Base_de_connaissance est introuvable."
        )

    files = sorted(path for path in knowledge_dir.rglob("*") if path.is_file())
    jsonl_stems = {
        path.relative_to(knowledge_dir).with_suffix("")
        for path in files
        if path.suffix.casefold() == ".jsonl"
    }
    selected_pdfs = set(pdf_paths)

    for path in files:
        relative_path = path.relative_to(knowledge_dir)
        suffix = path.suffix.casefold()

        if suffix == ".pdf":
            if path in selected_pdfs:
                try:
                    stat = path.stat()
                except OSError as error:
                    raise KnowledgeBaseError(
                        f"Impossible d'accéder au document PDF {path.name}."
                    ) from error
                yield from _read_pdf(
                    path,
                    stat.st_mtime_ns,
                    stat.st_size,
                )
        elif suffix == ".docx" and path.name not in EXCLUDED_KNOWLEDGE_FILES:
            yield from _read_docx(path)
        elif suffix == ".jsonl":
            yield from _read_jsonl(path)
        elif suffix == ".csv":
            if relative_path.with_suffix("") not in jsonl_stems:
                yield from _read_csv(path)


@lru_cache(maxsize=4)
def _build_index(pdf_paths):
    chunks = []
    document_lengths = []
    document_frequencies = Counter()
    postings = defaultdict(list)

    for chunk in _load_chunks(pdf_paths):
        term_frequencies = Counter(_tokens(chunk.text))
        if not term_frequencies:
            continue

        document_id = len(chunks)
        chunks.append(chunk)
        document_length = sum(term_frequencies.values())
        document_lengths.append(document_length)

        for token, frequency in term_frequencies.items():
            postings[token].append((document_id, frequency))
            document_frequencies[token] += 1

    if not chunks:
        raise KnowledgeBaseError(
            "Aucun texte exploitable n'a été trouvé dans la base de connaissances."
        )

    return (
        tuple(chunks),
        tuple(document_lengths),
        document_frequencies,
        dict(postings),
    )


def _select_pdf_paths(query, pdf_paths):
    query_terms = set(_tokens(query))
    if not query_terms:
        return ()

    title_terms = {
        path: set(_tokens(path.stem.replace("_", " ")))
        for path in pdf_paths
    }
    document_frequencies = Counter(
        term
        for terms in title_terms.values()
        for term in terms
    )
    document_count = len(title_terms)
    scored_paths = []

    for path, terms in title_terms.items():
        score = sum(
            math.log(
                1
                + (document_count - document_frequencies[term] + 0.5)
                / (document_frequencies[term] + 0.5)
            )
            for term in query_terms.intersection(terms)
        )
        if score:
            scored_paths.append((score, path))

    scored_paths.sort(key=lambda item: (-item[0], item[1].name))
    return tuple(
        path for _, path in scored_paths[:PDF_SOURCE_LIMIT]
    )


def retrieve_context(query, limit=CONTEXT_LIMIT):
    query_terms = set(_tokens(query))
    if not query_terms:
        return ""

    knowledge_dir = settings.BASE_DIR / "Base_de_connaissance"
    pdf_paths = tuple(sorted(knowledge_dir.rglob("*.pdf")))
    selected_pdfs = _select_pdf_paths(query, pdf_paths)
    chunks, document_lengths, document_frequencies, postings = _build_index(
        selected_pdfs
    )
    document_count = len(chunks)
    average_length = sum(document_lengths) / document_count
    scores = defaultdict(float)
    k1 = 1.5
    b = 0.75

    for term in query_terms:
        term_postings = postings.get(term, ())
        if not term_postings:
            continue

        document_frequency = document_frequencies[term]
        inverse_frequency = math.log(
            1
            + (document_count - document_frequency + 0.5)
            / (document_frequency + 0.5)
        )

        for document_id, term_frequency in term_postings:
            length = document_lengths[document_id]
            denominator = term_frequency + k1 * (
                1 - b + b * length / average_length
            )
            scores[document_id] += (
                inverse_frequency
                * term_frequency
                * (k1 + 1)
                / denominator
            )

    if not scores:
        return ""

    ranked = sorted(scores, key=scores.get, reverse=True)
    selected = []
    source_counts = Counter()
    for document_id in ranked:
        source = chunks[document_id].source.split(", ", 1)[0]
        if source_counts[source] >= CHUNKS_PER_SOURCE_LIMIT:
            continue
        selected.append(document_id)
        source_counts[source] += 1
        if len(selected) == limit:
            break

    return "\n\n".join(
        f"[Source : {chunks[document_id].source}]\n"
        f"{chunks[document_id].text}"
        for document_id in selected
    )
