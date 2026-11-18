"""One landscape A4 vector page and its preview from the same captured figure."""

import math
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import matplotlib as mpl
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfFile, RendererPdf
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.ft2font import FT2Font
from matplotlib.lines import Line2D

from bruker_nmr.domain.display import display_trace
from bruker_nmr.domain.integrals import has_overlaps
from bruker_nmr.domain.labels import (
    Label,
    format_peak_label,
    label_priority,
    place_peak_labels,
    visible_peaks,
)
from bruker_nmr.domain.style import GRID_COLOUR, TRACE_PX_TO_PT
from bruker_nmr.export.print_layout import (
    PrintLayoutError,
    clipped_positions,
    configure_ticks,
    integral_text,
    orthogonal_leaders,
    place_integrals,
    validate_routes,
)
from bruker_nmr.export.snapshot import ViewSnapshot

PAGE_WIDTH = 297 / 25.4 * 72
PAGE_HEIGHT = 210 / 25.4 * 72
LEFT, RIGHT, TOP, BOTTOM = 30.0, 24.0, 24.0, 22.0
FONT_PATH = Path(mpl.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
RC = {
    "pdf.fonttype": 42,
    "pdf.use14corefonts": False,
    "text.usetex": False,
    "text.parse_math": False,
    "path.simplify": False,
    "savefig.bbox": None,
    "axes.unicode_minus": True,
    "font.family": "DejaVu Sans",
}


@lru_cache(maxsize=1)
def _glyphs():
    return frozenset(FT2Font(str(FONT_PATH)).get_charmap())


def _wrap(text, measure, width, *, max_lines=3):
    """Metric wrapping, including long unbroken words; never shrink or drop text."""
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            if current and measure(current + char) > width:
                split = current.rfind(" ")
                if split > 0:
                    lines.append(current[:split])
                    current = current[split + 1 :] + char
                else:
                    lines.append(current)
                    current = char
            else:
                current += char
        lines.append(current.rstrip())
    if len(lines) > max_lines:
        raise ValueError("Header is too long for three readable lines; shorten it.")
    return tuple(lines)


def proposed_header(value):
    """Prefer compact file/title/facts lines; shorten name text visibly only if needed."""
    lines = value.split("\n")
    if not 1 <= len(lines) <= 3 or any(ord(c) not in _glyphs() for c in value if c != "\n"):
        return value, False
    dummy = PdfFile(BytesIO())
    try:
        renderer = RendererPdf(dummy, 72, 210 / 25.4, 297 / 25.4)
        font = FontProperties(fname=str(FONT_PATH), size=11.5)
        width = PAGE_WIDTH - LEFT - RIGHT

        def measure(text):
            return renderer.get_text_width_height_descent(text, font, False)[0]

        try:
            _wrap(value, measure, width, max_lines=3)
            return value, False
        except ValueError:
            pass
        shortened = False
        for index in range(len(lines) - 1):
            original = lines[index]
            if measure(original) <= width:
                continue
            low, high = 0, len(original)
            while low < high:
                middle = (low + high + 1) // 2
                if measure(original[:middle].rstrip() + "…") <= width:
                    low = middle
                else:
                    high = middle - 1
            name = original[:low].rstrip()
            split = name.rfind(" ")
            if split > len(name) * 0.7:
                name = name[:split]
            lines[index] = name + "…"
            shortened = True
        return "\n".join(lines), shortened
    finally:
        dummy.close()


@dataclass(frozen=True)
class PageGeometry:
    plot: tuple[float, float, float, float]  # left,bottom,width,height in PDF points
    peak_placements: tuple
    integral_placements: tuple
    hidden_peaks: tuple
    hidden_integrals: tuple
    caption_lines: tuple[str, ...]
    footer_lines: tuple[str, ...]


class SpectrumPage:
    def __init__(self, snapshot: ViewSnapshot):
        self.snapshot = snapshot
        view = snapshot.state.view
        if not all(math.isfinite(v) for v in (view.x_max - view.x_min, view.y_max - view.y_min)):
            raise ValueError("View range is too large to print; zoom to a finite range.")
        if any(ord(c) not in _glyphs() for c in snapshot.caption if c != "\n"):
            raise ValueError(
                "Some caption characters cannot be printed; use standard text and symbols."
            )
        with mpl.rc_context(RC):
            self.figure = Figure(
                figsize=(297 / 25.4, 210 / 25.4), dpi=72, facecolor="white", layout="none"
            )
        self.canvas = FigureCanvasAgg(self.figure)
        dummy = PdfFile(BytesIO())
        try:
            renderer = RendererPdf(dummy, 72, 210 / 25.4, 297 / 25.4)
            with mpl.rc_context(RC):
                self._construct(renderer)
                self.canvas.draw()
                self.validate_integrals()
        except BaseException:
            self.figure.clear()
            raise
        finally:
            dummy.close()

    def _construct(self, renderer):
        state, view = self.snapshot.state, self.snapshot.state.view
        width = PAGE_WIDTH - LEFT - RIGHT
        normal = FontProperties(fname=str(FONT_PATH), size=9.5)
        caption_font = FontProperties(fname=str(FONT_PATH), size=11.5)
        small = FontProperties(fname=str(FONT_PATH), size=8)

        def measure(text, font=normal):
            return renderer.get_text_width_height_descent(text, font, False)[0]

        def text(x, y, value, font=normal, **kwargs):
            return self.figure.text(
                x / PAGE_WIDTH,
                y / PAGE_HEIGHT,
                value,
                fontproperties=font,
                parse_math=False,
                **kwargs,
            )

        captions = (
            _wrap(self.snapshot.caption, lambda s: measure(s, caption_font), width)
            if self.snapshot.caption
            else ()
        )
        for i, line in enumerate(captions):
            text(LEFT, PAGE_HEIGHT - TOP - i * 15, line, caption_font, va="top")

        def pixel(ppm):
            return LEFT + (view.x_max - ppm) / (view.x_max - view.x_min) * width

        peaks = []
        if state.peaks.label_mode != "hidden":
            for peak in visible_peaks(state, view):
                value = format_peak_label(peak, state.calibration)
                peaks.append(
                    Label(
                        peak.id,
                        pixel(peak.ppm + state.calibration),
                        13.0,
                        value,
                        label_priority(peak, self.snapshot.selected_peak),
                        -peak.ppm,
                    )
                )
        placed, hidden = place_peak_labels(
            peaks, LEFT, LEFT + width, spacing=16.0, auto=state.peaks.label_mode == "auto"
        )
        rows = {r.id: r for r in self.snapshot.integrals}
        integral_labels, ranges = [], {}
        if state.show_integrals:
            for row in rows.values():
                if row.high < view.x_min or row.low > view.x_max:
                    continue
                a, b = pixel(min(row.high, view.x_max)), pixel(max(row.low, view.x_min))
                ranges[row.id] = (a, b)
                value = integral_text(row, view)
                integral_labels.append(
                    Label(row.id, (a + b) / 2, 13.0, value, 1 if row.reference else 0)
                )
        integrals = place_integrals(
            integral_labels, LEFT, LEFT + width, self.snapshot.integral_bands
        )
        hidden_integrals = ()
        pairs, levels = orthogonal_leaders(integrals)
        corridor = 8 + 2 * max(levels, default=0)
        norm = state.integrals.normalization.kind
        notes = []
        warnings = []
        if integrals:
            if norm in ("absent", "unverified"):
                notes.append(
                    "Integral normalization unverified; assign a reference before interpreting values."
                )
            else:
                notes.append(
                    "Integrals in H."
                    + (
                        " * Reference integral."
                        if any(rows[p.label.id].reference for p in integrals)
                        else ""
                    )
                )
            if has_overlaps(state.integrals):
                notes.append("Integration ranges overlap; values are per range.")
        if state.data.opening_mode == "raw" and not state.referenced:
            notes.append("Chemical-shift zero is unverified.")
        if any(p.needs_review for p in visible_peaks(state, view)):
            notes.append("Picked peak positions require review.")
        warnings.extend(notes)
        if hidden:
            warnings.append(
                f"Auto hides {len(hidden)} peak labels on this page; no peak records deleted."
            )
        if not state.show_integrals and rows:
            warnings.append("Integrals are hidden by the current appearance choice.")
        if state.peaks.label_mode == "all" and len(placed) * 16 > width:
            warnings.append(
                "All peak labels selected: crowded labels may overlap. Choose Auto or zoom before export."
            )
        self.upper_clips = clipped_positions(
            state.data.ppm,
            state.data.real,
            view,
            state.calibration,
            max_marks=max(1, int(width // 6)),
        )
        if self.upper_clips:
            warnings.append(f"Upper-edge clipping indicators: {len(self.upper_clips)}.")
        self.preflight = tuple(warnings)
        footer = tuple(
            line
            for note in notes
            for line in _wrap(note, lambda s: measure(s, small), width, max_lines=3)
        )
        footer_height = 10 * len(footer) + (6 if footer else 0)
        band_bottom = BOTTOM + footer_height
        max_length = max((measure(p.label.text) for p in integrals), default=0)
        # Axis decorations, fixed upward bracket ticks, a separate leader corridor,
        # then one bottom-aligned row of rotated values.
        bottom = band_bottom + (max_length + corridor + 38 if integrals else 32)
        peak_height = max((measure(p.label.text) + 20 for p in placed), default=0)
        top = PAGE_HEIGHT - TOP - (15 * len(captions) + 10 if captions else 0) - peak_height
        if top - bottom < 150:
            raise PrintLayoutError(
                "Annotations leave too little room. Close preview and zoom to fewer ranges or labels."
            )
        self.geometry = PageGeometry(
            (LEFT, bottom, width, top - bottom),
            placed,
            integrals,
            hidden,
            hidden_integrals,
            captions,
            footer,
        )
        self.ax = self.figure.add_axes(
            [
                LEFT / PAGE_WIDTH,
                bottom / PAGE_HEIGHT,
                width / PAGE_WIDTH,
                (top - bottom) / PAGE_HEIGHT,
            ]
        )
        ax = self.ax
        ax.set_xlim(view.x_max, view.x_min)
        ax.set_ylim(view.y_min, view.y_max)
        trace = display_trace(
            state.data.ppm,
            state.data.real,
            view.x_min - state.calibration,
            view.x_max - state.calibration,
            max(1, round(width / 72 * 600)),
        )
        self.trace = trace
        ax.plot(
            trace.x + state.calibration,
            trace.y,
            color=state.trace_color,
            linewidth=state.line_width * TRACE_PX_TO_PT,
            antialiased=True,
            label="spectrum",
        )
        if state.peaks.markers_visible:
            markers = tuple(
                p
                for p in state.peaks.records
                if view.x_min <= p.ppm + state.calibration <= view.x_max
            )
            x = np.array([p.ppm for p in markers])
            indices = np.clip(
                np.rint((x - state.data.ppm[0]) / (state.data.ppm[1] - state.data.ppm[0])).astype(
                    int
                ),
                0,
                len(state.data.real) - 1,
            )
            ax.plot(
                x + state.calibration,
                state.data.real[indices],
                linestyle="none",
                marker="+",
                markersize=5.25,
                markeredgewidth=0.8,
                color="#a24422",
                label="peak markers",
            )
        ax.yaxis.set_visible(False)
        ax.set_xlabel("")
        ax.set_ylabel("")
        configure_ticks(ax, view)
        if self.snapshot.show_grid:
            from matplotlib.ticker import MaxNLocator

            style = dict(colors=GRID_COLOUR, linewidths=0.30,
                         linestyles=(0, (5, 5)), zorder=0)
            ax.vlines(ax.get_xticks(), view.y_min, view.y_max,
                      label="ppm grid", **style)
            ax.hlines(MaxNLocator(nbins=7).tick_values(view.y_min, view.y_max),
                      view.x_min, view.x_max, label="amplitude grid", **style)
            ax.set_xlim(view.x_max, view.x_min)
            ax.set_ylim(view.y_min, view.y_max)
        ax.tick_params(
            axis="x", which="major", direction="out", labelsize=9, pad=4, length=4, width=0.6
        )
        ax.tick_params(axis="x", which="minor", direction="out", length=2, width=0.45)
        for tick in ax.get_xticklabels():
            tick.set_fontproperties(FontProperties(fname=str(FONT_PATH), size=9))
        for name, spine in ax.spines.items():
            spine.set_visible(name == "bottom")
            spine.set_linewidth(0.6)
            spine.set_color("#38424c")
        ax.spines["bottom"].set_position(("outward", 4))
        text(LEFT + width + 2, bottom - 8, "ppm", small, ha="left", va="top")
        if self.upper_clips:
            ax.plot(
                self.upper_clips,
                [1 - 3 / (top - bottom)] * len(self.upper_clips),
                transform=ax.get_xaxis_transform(),
                linestyle="none",
                marker="^",
                markersize=3.5,
                markeredgewidth=0.5,
                color=state.trace_color,
                label="upper clipping",
                clip_on=True,
            )

        def line(xs, ys):
            self.figure.add_artist(
                Line2D(
                    np.asarray(xs) / PAGE_WIDTH,
                    np.asarray(ys) / PAGE_HEIGHT,
                    transform=self.figure.transFigure,
                    color="#31556f",
                    linewidth=0.6,
                    clip_on=False,
                )
            )

        label_bottom = top + 18
        self.peak_texts, self.integral_texts = [], []
        for item in placed:
            center = item.left + item.label.width / 2
            self.peak_texts.append(
                text(
                    center,
                    label_bottom,
                    item.label.text,
                    ha="left",
                    va="center",
                    rotation=90,
                    rotation_mode="anchor",
                )
            )
            line(
                [item.label.center, item.label.center, center, center],
                [top + 1, top + 5, top + 12, label_bottom - 2],
            )
        bracket_y = bottom - 30
        label_baseline = band_bottom
        label_ceiling = label_baseline + max_length
        self.integral_routes = []
        self.integral_brackets = []
        for item, (anchor, center), level in zip(integrals, pairs, levels):
            a, b = ranges[item.label.id]
            line([a, a, b, b], [bracket_y + 5, bracket_y, bracket_y, bracket_y + 5])
            self.integral_brackets.append((a, b, bracket_y))
            elbow = label_ceiling + 4 + 2 * level
            end = label_baseline + measure(item.label.text) + 2
            if abs(center-anchor) < 1e-7:
                route = ((anchor, bracket_y), (anchor, end))
            else:
                route = ((anchor, bracket_y), (anchor, elbow), (center, elbow), (center, end))
            self.integral_routes.append(route)
            line([p[0] for p in route], [p[1] for p in route])
            self.integral_texts.append(
                text(center, label_baseline, item.label.text, ha="left", va="center",
                     rotation=90, rotation_mode="anchor")
            )
        validate_routes(self.integral_routes)
        for i, value in enumerate(footer):
            text(
                LEFT,
                BOTTOM + (len(footer) - 1 - i) * 10,
                value,
                small,
                va="bottom",
                color="#38424c",
            )

    def validate_integrals(self):
        if not self.integral_texts:
            return
        validate_routes(self.integral_routes)
        renderer = self.canvas.get_renderer()
        scale = 72 / self.figure.dpi
        boxes = [t.get_window_extent(renderer).transformed(
            mpl.transforms.Affine2D().scale(scale)) for t in self.integral_texts]
        left, bottom, width, _height = self.geometry.plot
        for i, box in enumerate(boxes):
            if box.x0 < left-0.2 or box.x1 > left+width+0.2:
                raise PrintLayoutError("Integral text exceeds plot bounds; zoom before export.")
            if box.y1 > self.integral_brackets[i][2]-2:
                raise PrintLayoutError("Integral text collides with brackets; change the view.")
            if any(box.overlaps(other) for other in boxes[:i]):
                raise PrintLayoutError("Integral labels overlap; zoom to fewer ranges.")
            if i and box.x0 < boxes[i-1].x0:
                raise PrintLayoutError("Integral label order is invalid.")
        ticks = [t.get_window_extent(renderer).y0 * scale for t in self.ax.get_xticklabels()
                 if t.get_visible() and t.get_text()]
        if ticks and self.integral_brackets[0][2] + 5 > min(ticks)-0.5:
            raise PrintLayoutError("Integral bracket ticks collide with the ppm scale.")
        # The runtime projection must remain the bounded minimum-displacement solution.
        from bruker_nmr.domain.labels import spread
        placements = self.geometry.integral_placements
        expected = spread([p.label.center for p in placements], 16, left+6.5, left+width-6.5)
        if any(abs(p.left+6.5-c) > 1e-7 for p,c in zip(placements,expected)):
            raise PrintLayoutError("Integral label displacement is inconsistent.")

    def preview_rgba(self, dpi=110):
        if not 36 <= dpi <= 200:
            raise ValueError("Unsupported preview resolution")
        with mpl.rc_context(RC):
            self.figure.set_dpi(dpi)
            self.canvas.draw()
            self.validate_integrals()
            return bytes(self.canvas.buffer_rgba()), self.canvas.get_width_height()

    def pdf_bytes(self):
        self.canvas.draw()
        self.validate_integrals()
        output = BytesIO()
        with mpl.rc_context(RC):
            self.figure.savefig(
                output,
                format="pdf",
                metadata={
                    "Creator": "NMR-go",
                    "CreationDate": None,
                    "ModDate": None,
                    "Title": None,
                    "Subject": None,
                },
            )
        return output.getvalue()

    def close(self):
        self.figure.clear()


def write_page(destination, page, *, overwrite=False):
    """Render fully before touching disk; replace atomically or create exclusively.

    Hard-link promotion is atomic on NTFS/POSIX. Other filesystems use exclusive creation
    for new files, cleaning failed writes; an existing file is never replaced by default.
    """
    destination = Path(destination)
    if destination.suffix.lower() != ".pdf":
        raise ValueError("Choose a PDF filename.")
    if destination.is_symlink():
        raise OSError("Choose a regular PDF destination")
    payload = page.pdf_bytes()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".nmr-go-", suffix=".tmp", dir=destination.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise
            except OSError:
                # Network/removable filesystems may not implement hard links.
                created = False
                try:
                    with destination.open("xb") as target:
                        created = True
                        target.write(payload)
                        target.flush()
                        os.fsync(target.fileno())
                except BaseException:
                    if created:
                        destination.unlink(missing_ok=True)
                    raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
