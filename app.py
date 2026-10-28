"""Double-click entry point for 光筛 LumaCull."""

from __future__ import annotations

from lumacull.desktop import main, report_fatal

if __name__ == "__main__":
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException as exc:  # keep windowed builds from dying silently
        report_fatal(exc)
        code = 1
    raise SystemExit(code)
