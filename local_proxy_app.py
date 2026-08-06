"""Source launcher for the unified Codex and Claude local proxy."""

from local_proxy.application import APP_VERSION, main, smoke_test


if __name__ == "__main__":
    raise SystemExit(main())
