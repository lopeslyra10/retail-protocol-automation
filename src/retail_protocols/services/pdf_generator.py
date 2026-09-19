from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import TypeVar

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from ..models import Campaign, ProtocolItem, Store

T = TypeVar("T")


class ProtocolPDFGenerator:
    """Gera protocolos A4 e pagina automaticamente listas extensas."""

    MAX_OS_HEADER_LINES = 6
    OS_INDEX_CAPACITY = 40
    ROW_HEIGHT = 14.6

    def __init__(
        self,
        brand_name: str = "Retail Demo",
        filename_brand: str = "RETAIL_DEMO",
    ) -> None:
        self.brand_name = brand_name.strip() or "Retail Demo"
        self.filename_brand = self._safe_filename(filename_brand) or "RETAIL_DEMO"
        self.regular_font, self.bold_font = self._register_fonts()

    @staticmethod
    def _font_candidates() -> tuple[list[str], list[str]]:
        regular = [
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\carlito.ttf",
            "/usr/share/fonts/truetype/crosextra/Caladea-Regular.ttf",
            "/usr/share/fonts/truetype/carlito/Carlito-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        bold = [
            r"C:\Windows\Fonts\calibrib.ttf",
            r"C:\Windows\Fonts\carlitob.ttf",
            "/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf",
            "/usr/share/fonts/truetype/carlito/Carlito-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
        return regular, bold

    @classmethod
    def _find_font(cls, candidates: list[str], fc_name: str) -> str | None:
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        if os.name != "nt":
            try:
                result = subprocess.run(
                    ["fc-match", "-f", "%{file}", fc_name],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                path = result.stdout.strip()
                if path and Path(path).exists():
                    return path
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    @classmethod
    def _register_fonts(cls) -> tuple[str, str]:
        regular_candidates, bold_candidates = cls._font_candidates()
        regular_path = cls._find_font(regular_candidates, "Carlito")
        bold_path = cls._find_font(bold_candidates, "Carlito:style=Bold")
        if regular_path and bold_path:
            try:
                pdfmetrics.registerFont(TTFont("ProtocolRegular", regular_path))
                pdfmetrics.registerFont(TTFont("ProtocolBold", bold_path))
                return "ProtocolRegular", "ProtocolBold"
            except Exception:
                pass
        return "Helvetica", "Helvetica-Bold"

    @staticmethod
    def _fit_font_size(
        text: str,
        font_name: str,
        max_width: float,
        preferred: float,
        minimum: float = 7.5,
    ) -> float:
        size = preferred
        while size > minimum and pdfmetrics.stringWidth(text, font_name, size) > max_width:
            size -= 0.25
        return size

    @staticmethod
    def _safe_filename(value: str) -> str:
        replacements = {
            "Á": "A",
            "À": "A",
            "Â": "A",
            "Ã": "A",
            "Ä": "A",
            "É": "E",
            "È": "E",
            "Ê": "E",
            "Ë": "E",
            "Í": "I",
            "Ì": "I",
            "Î": "I",
            "Ï": "I",
            "Ó": "O",
            "Ò": "O",
            "Ô": "O",
            "Õ": "O",
            "Ö": "O",
            "Ú": "U",
            "Ù": "U",
            "Û": "U",
            "Ü": "U",
            "Ç": "C",
        }
        result = value.upper()
        for source, target in replacements.items():
            result = result.replace(source, target)
        result = "_".join(result.replace("/", " ").replace("-", " ").split())
        return "".join(char for char in result if char.isalnum() or char == "_")

    @staticmethod
    def _chunks(values: list[T], size: int) -> list[list[T]]:
        return [values[index : index + size] for index in range(0, len(values), size)]

    def filename(self, sequence: int, campaign: Campaign, store: Store) -> str:
        store_part = self._safe_filename(store.name)
        return (
            f"{sequence:02d}_PROTOCOLO_OS_{campaign.filename_os_part()}_"
            f"{self.filename_brand}_{store_part}.pdf"
        )

    def _wrap_os_numbers(self, os_numbers: list[str], max_width: float) -> list[str]:
        # Até seis OS, preservamos o visual aprovado: uma OS por linha.
        if len(os_numbers) <= self.MAX_OS_HEADER_LINES:
            return [f"OS. {number}" for number in os_numbers]

        separator = "   |   "
        lines: list[str] = []
        current = ""
        for number in os_numbers:
            token = f"OS. {number}"
            candidate = token if not current else f"{current}{separator}{token}"
            if current and pdfmetrics.stringWidth(candidate, self.regular_font, 8.5) > max_width:
                lines.append(current)
                current = token
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def generate(
        self,
        campaign: Campaign,
        store: Store,
        sequence: int = 1,
        quantity_overrides: dict[str, dict[str, int]] | None = None,
    ) -> tuple[str, bytes]:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
        width, _height = A4

        applicable_items = campaign.items_for_store(store.id, quantity_overrides)
        if not applicable_items:
            raise ValueError(f"Nenhum tabloide selecionado inclui o endereço {store.name}.")

        title_os = "_".join(campaign.header_os_numbers())
        pdf.setTitle(f"Protocolo {self.brand_name} - {store.name} - OS {title_os}"[:1000])
        pdf.setSubject(" / ".join(item.description for item in applicable_items)[:1000])
        pdf.setAuthor(self.brand_name)
        pdf.setCreator("Retail Protocol Automation")

        left, right = 16.5, width - 16.5
        all_os_lines = self._wrap_os_numbers(
            campaign.header_os_numbers(),
            max_width=right - 282.0 - 5.0,
        )
        needs_os_index = len(all_os_lines) > self.MAX_OS_HEADER_LINES
        header_os_lines = (
            ["OS: relação completa nas páginas iniciais"] if needs_os_index else all_os_lines
        )

        layout = self._layout(len(header_os_lines))
        item_capacity = max(
            1,
            int((layout["table_header_bottom"] - layout["table_bottom"] - 13.0) // self.ROW_HEIGHT),
        )
        item_pages = self._chunks(applicable_items, item_capacity)
        os_pages = (
            self._chunks(campaign.header_os_numbers(), self.OS_INDEX_CAPACITY)
            if needs_os_index
            else []
        )
        total_pages = len(os_pages) + len(item_pages)

        page_number = 1
        for os_page in os_pages:
            self._draw_os_index_page(
                pdf,
                campaign,
                store,
                os_page,
                page_number,
                total_pages,
                left,
                right,
            )
            pdf.showPage()
            page_number += 1

        for page_items in item_pages:
            self._draw_protocol_page(
                pdf,
                campaign,
                store,
                page_items,
                header_os_lines,
                layout,
                page_number,
                total_pages,
                left,
                right,
                quantity_overrides,
            )
            pdf.showPage()
            page_number += 1

        pdf.save()
        return self.filename(sequence, campaign, store), buffer.getvalue()

    @staticmethod
    def _layout(os_line_count: int) -> dict[str, float]:
        extra_header = max(0, os_line_count - 2) * 10.0
        return {
            "top": 788.0,
            "bottom": 93.5,
            "header_bottom": 717.0 - extra_header,
            "store_bottom": 659.5 - extra_header,
            "table_header_bottom": 632.0 - extra_header,
            "table_bottom": 376.0,
            "auth_bottom": 320.0,
            "invoice_bottom": 277.0,
            "observations_bottom": 206.0,
        }

    def _draw_protocol_page(
        self,
        pdf: canvas.Canvas,
        campaign: Campaign,
        store: Store,
        items: list[ProtocolItem],
        os_lines: list[str],
        layout: dict[str, float],
        page_number: int,
        total_pages: int,
        left: float,
        right: float,
        overrides: dict[str, dict[str, int]] | None,
    ) -> None:
        top = layout["top"]
        bottom = layout["bottom"]
        header_bottom = layout["header_bottom"]
        store_bottom = layout["store_bottom"]
        table_header_bottom = layout["table_header_bottom"]
        table_bottom = layout["table_bottom"]
        auth_bottom = layout["auth_bottom"]
        invoice_bottom = layout["invoice_bottom"]
        observations_bottom = layout["observations_bottom"]

        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setLineWidth(1.05)
        pdf.rect(left, bottom, right - left, top - bottom, stroke=1, fill=0)

        for y in [
            header_bottom,
            store_bottom,
            table_bottom,
            auth_bottom,
            invoice_bottom,
            observations_bottom,
        ]:
            pdf.line(left, y, right, y)

        self._draw_header(
            pdf,
            campaign,
            os_lines,
            left,
            right,
            top,
            page_number,
            total_pages,
        )
        self._draw_store(pdf, store, left, right, header_bottom, store_bottom)
        self._draw_table(
            pdf,
            campaign,
            store,
            items,
            left,
            right,
            store_bottom,
            table_header_bottom,
            table_bottom,
            overrides,
        )
        self._draw_authorization(pdf, left, right, table_bottom, auth_bottom)
        self._draw_invoice(pdf, campaign, left, right, auth_bottom, invoice_bottom)
        self._draw_observations(pdf, left, right, invoice_bottom, observations_bottom)
        self._draw_proof(pdf, left, right, observations_bottom, bottom)

    def _draw_os_index_page(
        self,
        pdf: canvas.Canvas,
        campaign: Campaign,
        store: Store,
        os_numbers: list[str],
        page_number: int,
        total_pages: int,
        left: float,
        right: float,
    ) -> None:
        top, bottom = 788.0, 93.5
        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setLineWidth(1.05)
        pdf.rect(left, bottom, right - left, top - bottom, stroke=1, fill=0)

        pdf.setFont(self.bold_font, 15.0)
        pdf.drawCentredString((left + right) / 2, top - 38.0, "RELAÇÃO DE ORDENS DE SERVIÇO")
        pdf.setFont(self.regular_font, 10.0)
        pdf.drawString(
            left + 20.0,
            top - 62.0,
            f"Unidade: {self.brand_name.upper()} - {store.name.upper()}",
        )
        pdf.drawString(left + 20.0, top - 78.0, f"Data: {campaign.issue_date.strftime('%d/%m/%Y')}")
        pdf.drawRightString(right - 20.0, top - 62.0, f"Página {page_number}/{total_pages}")
        pdf.line(left, top - 91.0, right, top - 91.0)

        rows_per_column = 20
        column_width = (right - left - 50.0) / 2
        for index, os_number in enumerate(os_numbers):
            column = index // rows_per_column
            row = index % rows_per_column
            x = left + 25.0 + (column * column_width)
            y = top - 119.0 - (row * 27.0)
            pdf.setFont(self.regular_font, 11.0)
            pdf.drawString(x, y, f"OS. {os_number}")

        pdf.setFont(self.regular_font, 9.0)
        pdf.drawCentredString(
            (left + right) / 2,
            bottom + 18.0,
            "A relação continua nas páginas seguintes do mesmo protocolo.",
        )

    def _draw_header(
        self,
        pdf: canvas.Canvas,
        campaign: Campaign,
        os_lines: Iterable[str],
        left: float,
        right: float,
        top: float,
        page_number: int,
        total_pages: int,
    ) -> None:
        x = 282.0
        y = top - 22.0
        pdf.setFont(self.regular_font, 8.5)
        pdf.drawString(left + 116.0, y, f"Página {page_number}/{total_pages}")
        pdf.setFont(self.regular_font, 11.16)
        pdf.drawString(x, y, "Número :")
        pdf.drawString(x, y - 14.0, f"Data: {campaign.issue_date.strftime('%d/%m/%Y')}")
        for index, line in enumerate(os_lines):
            size = self._fit_font_size(line, self.regular_font, right - x - 5.0, 9.5, 7.5)
            pdf.setFont(self.regular_font, size)
            pdf.drawString(x, y - 28.0 - (index * 10.0), line)

    def _draw_store(
        self,
        pdf: canvas.Canvas,
        store: Store,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        x = left + 2.0
        line1 = top - 12.0
        pdf.setFont(self.bold_font, 11.16)
        pdf.drawString(x, line1, "Favorecido :")

        client = f"Nome Cliente: {self.brand_name.upper()} - {store.name.upper()}"
        address = f"Endereço: {store.address.upper()}"
        city_state = f"Cidade: {store.city.upper()}          Estado: {store.state.upper()}"

        client_size = self._fit_font_size(client, self.bold_font, right - x - 6, 11.16)
        address_size = self._fit_font_size(address, self.bold_font, right - x - 6, 11.16)
        city_size = self._fit_font_size(city_state, self.bold_font, right - x - 6, 11.16)

        pdf.setFont(self.bold_font, client_size)
        pdf.drawString(x, line1 - 14.0, client)
        pdf.setFont(self.bold_font, address_size)
        pdf.drawString(x, line1 - 28.0, address)
        pdf.setFont(self.bold_font, city_size)
        pdf.drawString(x, line1 - 42.0, city_state)

    def _draw_table(
        self,
        pdf: canvas.Canvas,
        campaign: Campaign,
        store: Store,
        items: list[ProtocolItem],
        left: float,
        right: float,
        top: float,
        header_bottom: float,
        bottom: float,
        overrides: dict[str, dict[str, int]] | None,
    ) -> None:
        col_x = [left, 95.0, 155.0, 410.0, 466.0, right]
        pdf.setLineWidth(0.65)
        pdf.line(left, header_bottom, right, header_bottom)
        for x in col_x[1:-1]:
            pdf.line(x, top - 13.0, x, bottom)

        headers = ["QUANTIDADE", "UNIDADES", "Descrição", "P.Unitário", "Valor"]
        centers = [
            (col_x[0] + col_x[1]) / 2,
            (col_x[1] + col_x[2]) / 2,
            (col_x[2] + col_x[3]) / 2,
            (col_x[3] + col_x[4]) / 2,
            (col_x[4] + col_x[5]) / 2,
        ]
        for index, (header, center) in enumerate(zip(headers, centers, strict=True)):
            header_size = self._fit_font_size(
                header,
                self.bold_font,
                col_x[index + 1] - col_x[index] - 5.0,
                11.16,
                7.0,
            )
            pdf.setFont(self.bold_font, header_size)
            pdf.drawCentredString(center, header_bottom + 3.0, header)

        row_top = header_bottom
        row_count = int((header_bottom - bottom - 13.0) // self.ROW_HEIGHT)
        for index in range(row_count + 1):
            y = row_top - (index * self.ROW_HEIGHT)
            if y > bottom + 13.0:
                pdf.line(left, y, right, y)

        for item_index, item in enumerate(items):
            y_base = header_bottom - ((item_index + 1) * self.ROW_HEIGHT) + 3.2
            quantity = campaign.quantity_for(store, item, overrides)
            pdf.setFont(self.bold_font, 11.16)
            pdf.drawRightString(col_x[1] - 4.0, y_base, str(quantity))
            pdf.setFont(self.bold_font, 10.0)
            pdf.drawCentredString((col_x[1] + col_x[2]) / 2, y_base, "UNIDADES")
            desc_size = self._fit_font_size(
                item.description,
                self.bold_font,
                col_x[3] - col_x[2] - 12,
                11.16,
                8.2,
            )
            pdf.setFont(self.bold_font, desc_size)
            pdf.drawString(col_x[2] + 6.0, y_base, item.description)

        total_top = bottom + 13.0
        pdf.line(left, total_top, right, total_top)
        pdf.setFont(self.regular_font, 11.16)
        pdf.drawRightString(col_x[3] - 4.0, bottom + 3.0, "Valor Total")

    def _draw_authorization(
        self,
        pdf: canvas.Canvas,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        pdf.setFont(self.bold_font, 11.16)
        pdf.drawString(left + 2.0, top - 11.0, "Autorização para saída dos produtos:")
        pdf.setFont(self.regular_font, 11.16)
        baseline = bottom + 3.0
        pdf.drawString(left + 2.0, baseline, "DIRETORIA/GERENCIA")
        pdf.drawCentredString((left + right) / 2, baseline, "EXPEDIÇÃO")
        pdf.drawString(right - 151.0, baseline, "FATURAMENTO")

    def _draw_invoice(
        self,
        pdf: canvas.Canvas,
        campaign: Campaign,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        pdf.setFont(self.bold_font, 11.16)
        pdf.drawString(left + 2.0, top - 11.0, "Lançamento a posterior de Faturamento:")
        pdf.setFont(self.regular_font, 11.16)
        y = top - 27.0
        pdf.drawString(left + 2.0, y, "Número de Nota Fiscal :")
        pdf.drawString(left + 220.0, y, f"Data: {campaign.issue_date.strftime('%d/%m/%Y')}")
        pdf.drawString(right - 151.0, y, "Valor:")

    def _draw_observations(
        self,
        pdf: canvas.Canvas,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        pdf.setFont(self.bold_font, 11.16)
        pdf.drawString(left + 2.0, top - 11.0, "Observações:")
        pdf.setFont(self.regular_font, 11.16)
        start_x = left + 73.0
        text_y = top - 27.0
        pdf.drawString(start_x, text_y, "CONTATO: Luiz Otávio Mourão")
        line_right = right - 95.0
        for offset in [31.0, 45.0, 59.0]:
            pdf.setLineWidth(0.45)
            pdf.line(start_x, top - offset, line_right, top - offset)

    def _draw_proof(
        self,
        pdf: canvas.Canvas,
        left: float,
        right: float,
        top: float,
        bottom: float,
    ) -> None:
        pdf.setFont(self.bold_font, 11.16)
        pdf.drawString(left + 2.0, top - 11.0, "Comprovante de entrega:")
        center = (left + right) / 2
        pdf.drawCentredString(
            center,
            top - 25.0,
            "O FAVORECIDO DECLARA QUE RECEBEU OS PRODUTOS CONSTANTES",
        )
        pdf.drawCentredString(
            center,
            top - 39.0,
            "NESTE DOCUMENTO COM TOTAL CONFORMIDADE.",
        )
        pdf.setFont(self.regular_font, 11.16)
        pdf.drawString(left + 73.0, top - 67.0, "DATA:__________")
        pdf.drawString(
            left + 73.0,
            top - 94.0,
            "Nome e Assinatura:_______________________________________________",
        )
