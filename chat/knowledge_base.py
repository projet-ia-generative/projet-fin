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
RECOMMENDATION_QUERY_TERMS = {
    "action",
    "advice",
    "conseil",
    "measure",
    "mesure",
    "propose",
    "proposer",
    "proposition",
    "recommend",
    "recommendation",
    "strategie",
    "strategy",
    "suggest",
    "suggestion",
}
RECOMMENDATION_QUERY_EXPANSIONS = {"action", "moyen", "strategie"}
RECOMMENDATION_PAGE_BOOST = 12
RECOMMENDATION_BULLET_BOOST = 20
STOP_WORDS = {
    "au",
    "aux",
    "avec",
    "comment",
    "en",
    "dans",
    "de",
    "des",
    "du",
    "elle",
    "elles",
    "est",
    "et",
    "il",
    "ils",
    "quelle",
    "quelles",
    "quel",
    "quels",
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
    "rapport",
    "son",
    "sont",
    "selon",
    "sur",
    "une",
    "vous",
    "votre",
    "19",
}
WORD_PATTERN = re.compile(r"[a-z0-9]+")
YEAR_PATTERN = re.compile(r"^(?:19|20)\d{2}$")
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
        in_references = False
        with pymupdf.open(path) as document:
            for page_number, page in enumerate(document, start=1):
                margin = page.rect.height * 0.08
                content_area = pymupdf.Rect(
                    page.rect.x0,
                    page.rect.y0 + margin,
                    page.rect.x1,
                    page.rect.y1 - margin,
                )
                text = page.get_text(clip=content_area)
                is_table_of_contents = re.search(
                    r"\btable des mati[eè]res\b|\btable of contents\b",
                    text,
                    re.IGNORECASE,
                )
                if is_table_of_contents:
                    continue
                if re.search(
                    r"(?im)^\s*(?:r[eé]f[eé]rences|bibliographie|references|bibliography)\s*$",
                    text,
                ):
                    in_references = True
                if in_references:
                    continue
                if page_number == 1 and len(text.split()) < 40:
                    continue
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
    # Une année seule (ex. 2016) ne doit pas suffire à choisir un PDF.
    query_terms = {
        term for term in _tokens(query) if not YEAR_PATTERN.match(term)
    }
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

    # Aucun titre de PDF ne correspond à la requête : on ne sélectionne rien.
    if not scored_paths:
        return ()

    minimum_score = scored_paths[0][0] * 0.55
    return tuple(
        path
        for score, path in scored_paths
        if score >= minimum_score
    )[:PDF_SOURCE_LIMIT]


def retrieve_context(query, limit=CONTEXT_LIMIT):
    query_terms = set(_tokens(query))
    if not query_terms:
        return ""

    query_years = {term for term in query_terms if YEAR_PATTERN.match(term)}
    # « à partir d'une base de données » : on interroge les tableaux, pas les PDF.
    data_query = "base" in query_terms and "donnee" in query_terms

    recommendation_query = bool(
        query_terms.intersection(RECOMMENDATION_QUERY_TERMS)
    )
    if recommendation_query:
        query_terms.update(RECOMMENDATION_QUERY_EXPANSIONS)

    knowledge_dir = settings.BASE_DIR / "Base_de_connaissance"
    pdf_paths = tuple(sorted(knowledge_dir.rglob("*.pdf")))
    if data_query:
        selected_pdfs = ()
    else:
        selected_pdfs = _select_pdf_paths(query, pdf_paths)
    chunks, document_lengths, document_frequencies, postings = _build_index(
        selected_pdfs
    )
    source_limit = (
        limit
        if len(selected_pdfs) == 1 or data_query
        else CHUNKS_PER_SOURCE_LIMIT
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

    # Si la question mentionne une année, on écarte les lignes de données
    # (CSV/JSONL) qui ne contiennent pas cette année.
    if query_years:
        for document_id in list(scores):
            source = chunks[document_id].source
            is_data_row = ", ligne " in source
            if is_data_row and not any(
                re.search(rf"\b{year}\b", chunks[document_id].text)
                for year in query_years
            ):
                del scores[document_id]

    if recommendation_query:
        recommendation_pages = {
            chunk.source
            for chunk in chunks
            if ", page " in chunk.source
            and "moyens propos" in chunk.text.casefold()
        }
        bullet_markers = ("\uf084", "•", "▪", "■", "‣")
        for document_id, chunk in enumerate(chunks):
            if chunk.source in recommendation_pages:
                scores[document_id] += RECOMMENDATION_PAGE_BOOST
                if any(marker in chunk.text for marker in bullet_markers):
                    scores[document_id] += RECOMMENDATION_BULLET_BOOST

    if not scores:
        return ""

    ranked = sorted(scores, key=scores.get, reverse=True)
    selected = []
    source_counts = Counter()
    page_counts = Counter()
    for document_id in ranked:
        chunk = chunks[document_id]
        source = chunk.source.split(", ", 1)[0]
        page = chunk.source if ", page " in chunk.source else None
        if (
            source_counts[source] >= source_limit
            or page is not None
            and page_counts[page] >= 1
        ):
            continue
        selected.append(document_id)
        source_counts[source] += 1
        if page is not None:
            page_counts[page] += 1
        if len(selected) == limit:
            break

    return "\n\n".join(
        f"[Source : {chunks[document_id].source}]\n{chunks[document_id].text}"
        for document_id in selected
    )