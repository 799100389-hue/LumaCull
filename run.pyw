from lumacull.desktop import main, report_fatal

if __name__ == "__main__":
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException as exc:
        report_fatal(exc)
        code = 1
    raise SystemExit(code)
