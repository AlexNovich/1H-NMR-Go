"""Processed viewer entry point and synthetic file-based frozen self-test."""
import argparse
import json
import sys
import time
from pathlib import Path

from bruker_nmr.identity import build_identity


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser(prog="NMR-go")
    parser.add_argument("--self-test", type=Path, metavar="RESULT_JSON")
    parser.add_argument('--phase-benchmark', type=Path, metavar='RESULT_JSON')
    args = parser.parse_args()
    if args.phase_benchmark:
        from bruker_nmr.phase_benchmark import benchmark
        benchmark(args.phase_benchmark)
        return 0
    if args.self_test:
        import socket

        def blocked_connect(*args, **kwargs):
            raise RuntimeError('Network disabled during synthetic self-test')

        socket.socket.connect = blocked_connect
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AlexanderNovichkov.NMR-go')
    identity = build_identity()
    result = {"status": "failed", "identity": identity,
              "scope": "synthetic dependencies only", "python": sys.version.split()[0]}
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from bruker_nmr.ui.main_window import ViewerWindow
        app = QApplication.instance() or QApplication([])
        window = ViewerWindow(identity)
        window.show()
        if args.self_test:
            def finish():
                try:
                    result["window_ready_seconds"] = time.perf_counter() - started
                    frame = window.frameGeometry()
                    available = window.screen().availableGeometry()
                    import os
                    result['requested_qt_scale_factor'] = os.environ.get('QT_SCALE_FACTOR', '1')
                    result['device_pixel_ratio'] = window.devicePixelRatioF()
                    result['network_connect_blocked'] = True
                    result["window_fits_screen"] = (frame.width() <= available.width()
                                                    and frame.height() <= available.height())
                    from bruker_nmr.smoke import SmokeCheckError, exercise_dependencies
                    result.update(exercise_dependencies(window, args.self_test))
                    result["status"] = "passed"
                except Exception as error:
                    result["error_type"] = type(error).__name__
                    if "SmokeCheckError" in locals() and isinstance(error, SmokeCheckError):
                        result["check_error"] = str(error)
                finally:
                    args.self_test.write_text(json.dumps(result, indent=2), encoding="utf-8")
                    app.exit(0 if result["status"] == "passed" else 1)
            QTimer.singleShot(100, finish)
        return app.exec()
    except Exception as error:
        if args.self_test:
            result["error_type"] = type(error).__name__
            args.self_test.write_text(json.dumps(result, indent=2), encoding="utf-8")
        else:
            from bruker_nmr.diagnostics import record_startup_failure
            record_startup_failure(identity, error)
            try:
                from PySide6.QtWidgets import QMessageBox
                app = QApplication.instance() or QApplication([])
                QMessageBox.critical(None, "Startup failed", "NMR-go could not start. "
                                     "Please report the build version and this error type: "
                                     + type(error).__name__ + ". A short diagnostic, if writable, "
                                     "is in your temporary folder: NMR-go/startup-error.json.")
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
