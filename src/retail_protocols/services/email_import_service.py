from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from ..models import Store
from .quantity_service import normalize_store_name

_FORWARD_MARKERS = (
    "---------- forwarded message ---------",
    "---------- mensagem encaminhada ---------",
    "-----original message-----",
    "-----mensagem original-----",
)


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").strip().split())


def _digits_to_int(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value)
    if not digits:
        return None
    number = int(digits)
    return number if number > 0 else None


@dataclass(frozen=True)
class ImportedEmailRow:
    raw_name: str
    raw_address: str
    quantity: int
    store_id: str | None
    official_name: str | None
    match_method: str | None

    @property
    def is_matched(self) -> bool:
        return self.store_id is not None


@dataclass(frozen=True)
class EmailQuantityTable:
    source_position: int
    rows: tuple[ImportedEmailRow, ...]
    duplicate_store_ids: tuple[str, ...] = ()
    label: str = ""
    os_numbers: tuple[str, ...] = ()
    profile_name: str = ""
    description: str = ""
    category: str = ""
    issue_date: date | None = None

    @property
    def quantities(self) -> dict[str, int]:
        result: dict[str, int] = {}
        duplicate_ids = set(self.duplicate_store_ids)
        for row in self.rows:
            if row.store_id and row.store_id not in duplicate_ids:
                result[row.store_id] = row.quantity
        return result

    @property
    def total(self) -> int:
        return sum(row.quantity for row in self.rows)

    @property
    def matched_count(self) -> int:
        return sum(1 for row in self.rows if row.is_matched)

    @property
    def unmatched_rows(self) -> tuple[ImportedEmailRow, ...]:
        return tuple(row for row in self.rows if not row.is_matched)

    @property
    def is_valid(self) -> bool:
        return bool(self.rows) and not self.unmatched_rows and not self.duplicate_store_ids


@dataclass(frozen=True)
class EmailListImportResult:
    subject: str
    received_at: datetime | None
    os_numbers: tuple[str, ...]
    tabloid_description: str
    profile_name: str
    category: str
    issue_date: date | None
    tables: tuple[EmailQuantityTable, ...]
    declared_totals: tuple[int, ...]
    warnings: tuple[str, ...] = ()

    @property
    def recommended_table(self) -> EmailQuantityTable:
        if not self.tables:
            raise ValueError("O e-mail não contém uma tabela de distribuição reconhecível.")
        return self.tables[0]


@dataclass
class _TableContext:
    rows: list[list[str]]
    row: list[str] | None = None
    cell: list[str] | None = None


@dataclass(frozen=True)
class _RawQuantityTable:
    label: str
    rows: tuple[tuple[str, str, int], ...]


class _EmailHTMLTableParser(HTMLParser):
    """Extrai as células sem depender de bibliotecas externas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[_TableContext] = []
        self.tables: list[list[list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.casefold()
        if tag == "table":
            self._stack.append(_TableContext(rows=[]))
            return
        if not self._stack:
            return

        context = self._stack[-1]
        if tag == "tr":
            context.row = []
        elif tag in {"td", "th"} and context.row is not None:
            context.cell = []
        elif tag in {"br", "p", "div"} and context.cell is not None:
            context.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1].cell is not None:
            self._stack[-1].cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if not self._stack:
            return

        context = self._stack[-1]
        if tag in {"td", "th"} and context.cell is not None:
            if context.row is not None:
                context.row.append(_clean_text("".join(context.cell)))
            context.cell = None
        elif tag == "tr" and context.row is not None:
            if any(context.row):
                context.rows.append(context.row)
            context.row = None
            context.cell = None
        elif tag == "table":
            completed = self._stack.pop()
            if completed.rows:
                self.tables.append(completed.rows)


class EmailListParser:
    """Lê um `.eml` e transforma tabelas Loja/Endereço/Quantidade em listas."""

    MAX_EMAIL_BYTES = 15 * 1024 * 1024

    def __init__(self, stores: Iterable[Store]):
        self.stores = list(stores)
        self._stores_by_id = {store.id: store for store in self.stores}
        self._name_map = self._build_name_map()
        self._address_map = self._build_address_map()

    def parse(self, content: bytes) -> EmailListImportResult:
        if not content:
            raise ValueError("O arquivo de e-mail está vazio.")
        if len(content) > self.MAX_EMAIL_BYTES:
            raise ValueError("O e-mail excede o limite de 15 MB.")

        message = BytesParser(policy=policy.default).parsebytes(content)
        subject = _clean_text(str(message.get("subject", "")))
        received_at = self._received_at(message)
        plain_texts, html_texts = self._message_bodies(message)
        combined_plain = "\n".join(plain_texts)

        raw_tables: list[_RawQuantityTable] = []
        for html in html_texts:
            raw_tables.extend(self._extract_html_tables(html))
        if not raw_tables:
            for plain_text in plain_texts:
                raw_tables.extend(self._extract_plain_tables(plain_text))

        tables = self._match_and_deduplicate_tables(raw_tables)
        if not tables:
            raise ValueError(
                "Nenhuma tabela com as colunas Loja, Endereço e Quantidade foi encontrada."
            )

        os_numbers = self._extract_os_numbers(subject, combined_plain)
        tabloid_description, profile_name = self._extract_tabloid_description(subject)
        category = self._infer_category(tabloid_description)
        issue_date = self._infer_issue_date(tabloid_description, received_at)
        tables = self._decorate_tables(
            tables=tables,
            subject=subject,
            plain_text=combined_plain,
            global_os_numbers=os_numbers,
            global_description=tabloid_description,
            global_profile_name=profile_name,
            global_category=category,
            global_issue_date=issue_date,
            received_at=received_at,
        )
        declared_totals = self._extract_declared_totals(combined_plain)

        warnings: list[str] = []
        if not os_numbers:
            warnings.append("Nenhuma OS foi identificada no assunto ou no texto mais recente.")
        distinct_labels = {
            normalize_store_name(table.label) for table in tables if table.label.strip()
        }
        if len(distinct_labels) > 1:
            warnings.append(
                "O e-mail contém mais de uma lista diferente. "
                "Cada distribuição será tratada separadamente."
            )
        elif len(tables) > 1:
            warnings.append(
                "O histórico do e-mail contém mais de uma versão da distribuição. "
                "A primeira tabela é a mais recente no topo da mensagem."
            )

        return EmailListImportResult(
            subject=subject,
            received_at=received_at,
            os_numbers=os_numbers,
            tabloid_description=tabloid_description,
            profile_name=profile_name,
            category=category,
            issue_date=issue_date,
            tables=tables,
            declared_totals=declared_totals,
            warnings=tuple(warnings),
        )

    def _build_name_map(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for store in self.stores:
            candidates = [store.name, *store.aliases, store.id.replace("_", " ")]
            for candidate in candidates:
                normalized = normalize_store_name(candidate)
                if normalized:
                    result.setdefault(normalized, set()).add(store.id)
        return result

    def _build_address_map(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for store in self.stores:
            normalized = normalize_store_name(store.address)
            if normalized:
                result.setdefault(normalized, set()).add(store.id)
        return result

    @staticmethod
    def _received_at(message: Message) -> datetime | None:
        raw_date = message.get("date")
        if not raw_date:
            return None
        try:
            return parsedate_to_datetime(str(raw_date))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _message_bodies(message: Message) -> tuple[list[str], list[str]]:
        plain_texts: list[str] = []
        html_texts: list[str] = []
        for part in message.walk():
            if part.is_multipart() or part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                text = part.get_content()
            except (LookupError, UnicodeDecodeError):
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if not isinstance(text, str):
                continue
            if content_type == "text/plain":
                plain_texts.append(text)
            else:
                html_texts.append(text)
        return plain_texts, html_texts

    def _extract_html_tables(self, html: str) -> list[_RawQuantityTable]:
        parser = _EmailHTMLTableParser()
        parser.feed(html)
        candidates: list[_RawQuantityTable] = []
        # Tabelas internas terminam primeiro; source_position é recalculada após a leitura.
        for table in parser.tables:
            parsed = self._parse_tabular_rows(table)
            if parsed:
                candidates.append(_RawQuantityTable(label="", rows=tuple(parsed)))

        # O HTMLParser registra tabelas aninhadas na ordem de fechamento. As tabelas de
        # distribuição do Gmail normalmente não são aninhadas; esta ordenação preserva
        # a posição visual usando a primeira ocorrência textual da assinatura.
        candidates.sort(key=lambda table: html.find(table.rows[0][0]))
        return candidates

    def _extract_plain_tables(self, text: str) -> list[_RawQuantityTable]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            without_quote = re.sub(r"^\s*(?:>\s*)+", "", raw_line)
            line = _clean_text(without_quote.strip().strip("*"))
            if not line or re.fullmatch(r"<https?://[^>]+>", line, flags=re.IGNORECASE):
                continue
            lines.append(line)

        candidates = self._extract_labeled_pair_sections(lines)
        index = 0
        while index + 2 < len(lines):
            headers = [normalize_store_name(lines[index + offset]) for offset in range(3)]
            if not (
                headers[0] in {"loja", "lojas"}
                and headers[1].startswith("endereco")
                and ("qtd" in headers[2] or "quantidade" in headers[2])
            ):
                index += 1
                continue

            index += 3
            rows: list[tuple[str, str, int]] = []
            while index + 2 < len(lines):
                if normalize_store_name(lines[index]) in {"loja", "lojas"}:
                    break
                quantity = _digits_to_int(lines[index + 2])
                if quantity is None:
                    break
                rows.append((lines[index], lines[index + 1], quantity))
                index += 3
            if rows:
                candidates.append(_RawQuantityTable(label="", rows=tuple(rows)))
        return candidates

    @staticmethod
    def _section_label(line: str) -> str | None:
        match = re.match(
            r"^distribui(?:ç|c)(?:ã|a)o\s+(.+?)\s*:?$",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        return _clean_text(match.group(1).strip("* :"))

    def _extract_labeled_pair_sections(self, lines: list[str]) -> list[_RawQuantityTable]:
        candidates: list[_RawQuantityTable] = []
        index = 0
        while index < len(lines):
            label = self._section_label(lines[index])
            if not label:
                index += 1
                continue

            index += 1
            rows: list[tuple[str, str, int]] = []
            while index < len(lines):
                current = lines[index]
                normalized = normalize_store_name(current)
                if self._section_label(current):
                    break
                if normalized in {
                    "nome da loja",
                    "nome loja",
                    "n flyers",
                    "no flyers",
                    "numero flyers",
                    "qtd",
                    "qtd total",
                    "quantidade",
                }:
                    index += 1
                    continue
                if normalized in {"total", "total geral"}:
                    index += 1
                    if index < len(lines) and _digits_to_int(lines[index]) is not None:
                        index += 1
                    break
                if self._is_plain_section_stop(current):
                    break

                inline_match = re.match(r"^(.*?)\s*[;|\t]\s*([0-9][0-9. ]*)$", current)
                if inline_match:
                    quantity = _digits_to_int(inline_match.group(2))
                    if inline_match.group(1).strip() and quantity is not None:
                        rows.append((_clean_text(inline_match.group(1)), "", quantity))
                    index += 1
                    continue

                if index + 1 >= len(lines):
                    break
                quantity = _digits_to_int(lines[index + 1])
                if quantity is None:
                    break
                rows.append((current, "", quantity))
                index += 2

            if rows:
                candidates.append(_RawQuantityTable(label=label, rows=tuple(rows)))
        return candidates

    @staticmethod
    def _is_plain_section_stop(line: str) -> bool:
        normalized = normalize_store_name(line)
        prefixes = (
            "enviado do meu",
            "mensagem original",
            "forwarded message",
            "de ",
            "data ",
            "para ",
            "cc ",
            "assunto ",
            "em seg ",
            "em ter ",
            "em qua ",
            "em qui ",
            "em sex ",
            "em sab ",
            "em dom ",
        )
        return normalized.startswith(prefixes)

    @staticmethod
    def _parse_tabular_rows(table: list[list[str]]) -> list[tuple[str, str, int]]:
        for header_index, header_row in enumerate(table):
            normalized = [normalize_store_name(cell) for cell in header_row]
            name_index = next(
                (index for index, cell in enumerate(normalized) if cell in {"loja", "lojas"}),
                None,
            )
            address_index = next(
                (index for index, cell in enumerate(normalized) if cell.startswith("endereco")),
                None,
            )
            quantity_index = next(
                (
                    index
                    for index, cell in enumerate(normalized)
                    if "qtd" in cell or "quantidade" in cell
                ),
                None,
            )
            if None in {name_index, address_index, quantity_index}:
                continue

            maximum_index = max(name_index, address_index, quantity_index)  # type: ignore[arg-type]
            rows: list[tuple[str, str, int]] = []
            for row in table[header_index + 1 :]:
                if len(row) <= maximum_index:
                    continue
                name = _clean_text(row[name_index])  # type: ignore[index]
                address = _clean_text(row[address_index])  # type: ignore[index]
                quantity = _digits_to_int(row[quantity_index])  # type: ignore[index]
                if name and quantity is not None:
                    rows.append((name, address, quantity))
            if rows:
                return rows
        return []

    def _match_store(self, raw_name: str, raw_address: str) -> tuple[str | None, str | None]:
        normalized_name = normalize_store_name(raw_name)
        name_candidates = [normalized_name]
        without_brand = re.sub(
            r"^(?:supermercado\s+)?st\s+marche\s+",
            "",
            normalized_name,
        )
        if without_brand and without_brand != normalized_name:
            name_candidates.append(without_brand)

        name_ids: set[str] = set()
        for candidate in name_candidates:
            name_ids.update(self._name_map.get(candidate, set()))
        address_ids = self._address_map.get(normalize_store_name(raw_address), set())

        if len(name_ids) == 1:
            store_id = next(iter(name_ids))
            if not address_ids or store_id in address_ids:
                return store_id, "nome"
        if len(address_ids) == 1:
            return next(iter(address_ids)), "endereço"
        intersection = name_ids & address_ids
        if len(intersection) == 1:
            return next(iter(intersection)), "nome e endereço"
        return None, None

    def _match_and_deduplicate_tables(
        self,
        raw_tables: list[_RawQuantityTable],
    ) -> tuple[EmailQuantityTable, ...]:
        tables: list[EmailQuantityTable] = []
        signatures: set[tuple[tuple[str, str, int], ...]] = set()
        for source_position, raw_table in enumerate(raw_tables, start=1):
            raw_rows = raw_table.rows
            signature = tuple(
                (
                    normalize_store_name(name),
                    normalize_store_name(address),
                    quantity,
                )
                for name, address, quantity in raw_rows
            )
            if signature in signatures:
                continue
            signatures.add(signature)

            rows: list[ImportedEmailRow] = []
            matched_ids: list[str] = []
            for raw_name, raw_address, quantity in raw_rows:
                store_id, match_method = self._match_store(raw_name, raw_address)
                store = self._stores_by_id.get(store_id) if store_id else None
                if store_id:
                    matched_ids.append(store_id)
                rows.append(
                    ImportedEmailRow(
                        raw_name=raw_name,
                        raw_address=raw_address,
                        quantity=quantity,
                        store_id=store_id,
                        official_name=store.name if store else None,
                        match_method=match_method,
                    )
                )

            duplicate_ids = tuple(
                sorted({store_id for store_id in matched_ids if matched_ids.count(store_id) > 1})
            )
            tables.append(
                EmailQuantityTable(
                    source_position=source_position,
                    rows=tuple(rows),
                    duplicate_store_ids=duplicate_ids,
                    label=raw_table.label,
                )
            )
        return tuple(tables)

    @staticmethod
    def _top_message_text(text: str) -> str:
        lowered = text.casefold()
        positions = [lowered.find(marker) for marker in _FORWARD_MARKERS]
        positions = [position for position in positions if position >= 0]
        return text[: min(positions)] if positions else text

    def _extract_os_numbers(self, subject: str, plain_text: str) -> tuple[str, ...]:
        def extract_from(source: str) -> list[str]:
            numbers: list[str] = []
            pattern = re.compile(
                r"\bOS(?:S)?\b\s*(?:abertas?\s*)?[:.]?\s*"
                r"([0-9][0-9\s/,&Ee.\-]{3,})",
                flags=re.IGNORECASE,
            )
            for match in pattern.finditer(source):
                for number in re.findall(r"\d{4,}", match.group(1)):
                    if number not in numbers:
                        numbers.append(number)
            return numbers

        subject_numbers = extract_from(subject)
        if subject_numbers:
            return tuple(subject_numbers)
        return tuple(extract_from(self._top_message_text(plain_text)))

    def _decorate_tables(
        self,
        tables: tuple[EmailQuantityTable, ...],
        subject: str,
        plain_text: str,
        global_os_numbers: tuple[str, ...],
        global_description: str,
        global_profile_name: str,
        global_category: str,
        global_issue_date: date | None,
        received_at: datetime | None,
    ) -> tuple[EmailQuantityTable, ...]:
        os_by_index: dict[int, list[str]] = {index: [] for index in range(len(tables))}
        used_os: set[str] = set()

        for number, descriptor in re.findall(
            r"\b(\d{4,})\s*\(([^)]+)\)",
            plain_text,
            flags=re.IGNORECASE,
        ):
            descriptor_tokens = self._identity_tokens(descriptor)
            for index, table in enumerate(tables):
                if descriptor_tokens & self._identity_tokens(table.label):
                    if number not in os_by_index[index]:
                        os_by_index[index].append(number)
                        used_os.add(number)
                    break

        labeled_indexes = [index for index, table in enumerate(tables) if table.label]
        if not labeled_indexes or len(tables) == 1:
            for index in range(len(tables)):
                os_by_index[index] = list(global_os_numbers)
        else:
            unassigned_indexes = [index for index in labeled_indexes if not os_by_index[index]]
            remaining_os = [number for number in global_os_numbers if number not in used_os]
            if len(unassigned_indexes) == 1 and len(remaining_os) == 1:
                os_by_index[unassigned_indexes[0]] = remaining_os

        decorated: list[EmailQuantityTable] = []
        for index, table in enumerate(tables):
            if table.label:
                profile_name = self._profile_name_for_label(
                    table.label,
                    subject,
                    global_profile_name,
                )
                description = profile_name.upper().replace(" A ", " a ")[:160]
                category = self._infer_category(description)
                issue_date = self._infer_issue_date(description, received_at)
                if issue_date is None and category.casefold() == global_category.casefold():
                    issue_date = global_issue_date
                if issue_date is None and received_at is not None:
                    issue_date = received_at.date()
            else:
                profile_name = global_profile_name
                description = global_description
                category = global_category
                issue_date = global_issue_date

            decorated.append(
                replace(
                    table,
                    os_numbers=tuple(os_by_index[index]),
                    profile_name=profile_name[:120],
                    description=description[:160],
                    category=category[:60],
                    issue_date=issue_date,
                )
            )
        return tuple(decorated)

    @staticmethod
    def _identity_tokens(value: str) -> set[str]:
        ignored = {"distribuicao", "tabloide", "sacola", "sacolas", "lista"}
        return {
            token
            for token in normalize_store_name(value).split()
            if len(token) >= 4 and token not in ignored
        }

    @staticmethod
    def _profile_name_for_label(label: str, subject: str, global_profile_name: str) -> str:
        normalized_label = normalize_store_name(label)
        if normalized_label == normalize_store_name(global_profile_name):
            return global_profile_name
        if re.search(
            r"\b\d{1,2}[-/]\d{1,2}\s+a\s+\d{1,2}[-/]\d{1,2}\b",
            label,
            flags=re.IGNORECASE,
        ):
            return _clean_text(label)
        match = re.search(
            rf"\b{re.escape(label)}\b[^|,;]*$",
            subject,
            flags=re.IGNORECASE,
        )
        if match:
            return _clean_text(match.group(0).strip(" -"))
        return _clean_text(label)

    @staticmethod
    def _extract_tabloid_description(subject: str) -> tuple[str, str]:
        cleaned_subject = re.sub(
            r"^(?:(?:fwd?|enc|re)\s*:\s*)+",
            "",
            subject,
            flags=re.IGNORECASE,
        )
        match = re.search(
            r"\btabloide\s+.+?\d{1,2}[-/]\d{1,2}\s+a\s+\d{1,2}[-/]\d{1,2}\b",
            cleaned_subject,
            flags=re.IGNORECASE,
        )
        if match:
            source = _clean_text(match.group(0))
        else:
            source = _clean_text(cleaned_subject.split("|")[-1].split(" - ")[-1])
            source = re.sub(r"^urgente\s*[:|-]?\s*", "", source, flags=re.IGNORECASE)

        profile_name = source[:1].upper() + source[1:]
        description = source.upper().replace(" A ", " a ")
        return description[:160], profile_name[:120]

    @staticmethod
    def _infer_category(description: str) -> str:
        cleaned = re.sub(r"^TABLOIDE\s+", "", description, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"\s+\d{1,2}[-/]\d{1,2}\s+a\s+\d{1,2}[-/]\d{1,2}.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = _clean_text(cleaned)
        return cleaned.title()[:60] if cleaned else "Importada do e-mail"

    @staticmethod
    def _infer_issue_date(description: str, received_at: datetime | None) -> date | None:
        match = re.search(r"\b(\d{1,2})[-/](\d{1,2})\s+a\s+", description, flags=re.IGNORECASE)
        if not match:
            return None
        reference = received_at.date() if received_at else date.today()
        day, month = int(match.group(1)), int(match.group(2))
        try:
            candidate = date(reference.year, month, day)
        except ValueError:
            return None

        if candidate < reference - timedelta(days=180):
            try:
                candidate = date(reference.year + 1, month, day)
            except ValueError:
                return None
        elif candidate > reference + timedelta(days=180):
            try:
                candidate = date(reference.year - 1, month, day)
            except ValueError:
                return None
        return candidate

    @staticmethod
    def _extract_declared_totals(text: str) -> tuple[int, ...]:
        result: list[int] = []
        for raw_number in re.findall(r"\b([0-9][0-9. ]*)\s+unidades\b", text, flags=re.IGNORECASE):
            number = _digits_to_int(raw_number)
            if number and number not in result:
                result.append(number)
        return tuple(result)
