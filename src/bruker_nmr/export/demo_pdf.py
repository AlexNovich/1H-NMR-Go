"""One synthetic landscape page to exercise the PDF dependency route."""
from pathlib import Path

from matplotlib.backends.backend_pdf import FigureCanvasPdf
from matplotlib.figure import Figure

from bruker_nmr.demo import DemoSpectrum
from bruker_nmr.domain.style import DEFAULT_TRACE_COLOUR, DEFAULT_TRACE_PT


def write_demo_pdf(destination: Path, spectrum: DemoSpectrum, identity: dict) -> None:
    figure = Figure(figsize=(297 / 25.4, 210 / 25.4))
    FigureCanvasPdf(figure)
    ax = figure.add_subplot(111)
    ax.plot(spectrum.ppm, spectrum.amplitude, color=DEFAULT_TRACE_COLOUR, linewidth=DEFAULT_TRACE_PT)
    ax.set_xlim(12, -1)
    ax.set_ylim(-0.05, 1.15)
    ax.set_xlabel("Chemical shift (ppm)")
    ax.set_ylabel("Synthetic amplitude (arbitrary units)")
    ax.set_title("NMR-go — synthetic dependency smoke test")
    figure.text(0.1, 0.035, f"Version {identity['version']} | "
                f"Source {identity['source_commit'][:12]} | No experimental data", fontsize=9)
    figure.subplots_adjust(left=0.1, right=0.96, top=0.88, bottom=0.14)
    figure.savefig(destination, format="pdf", metadata={"Creator": "NMR-go synthetic demo"})
    figure.clear()
