"""Entry point for running the NiceGUI pen plotter application."""

from penplotter.app import run


if __name__ == "__main__":
    run(reload=False, host="0.0.0.0", port=8080)
