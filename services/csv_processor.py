"""
Tabular Data Processing Service
Processes CSV/Excel data and generates charts
"""

import glob
import os
import re
import unicodedata
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def normalize_chart_type(chart_type: Optional[Any]) -> str:
    """
    Map DB/UI values like 'Bar Chart', 'Line Chart', 'pie Chart', 'bar_chart' to bar|pie|line.
    """
    if chart_type is None or (isinstance(chart_type, str) and not chart_type.strip()):
        s = "bar"
    else:
        s = unicodedata.normalize("NFKC", str(chart_type)).strip()
    s = s.lower().replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    aliases_bar = {
        "bar",
        "bar chart",
        "barchart",
        "column",
        "column chart",
    }
    aliases_pie = {"pie", "pie chart", "donut", "piechart"}
    aliases_line = {"line", "line chart", "linechart", "trend"}
    if s in aliases_bar or s.startswith("bar "):
        return "bar"
    if s in aliases_pie or s.startswith("pie "):
        return "pie"
    if s in aliases_line or s.startswith("line "):
        return "line"
    return s


def _resolve_kaleido_tmp_dir(output_dir: str) -> tuple[str, str]:
    """
    Resolve writable Kaleido/choreographer temp roots.

    Returns (tmpdir, home_dir). Choreographer uses TMPDIR for normal temp files but
    uses Path.home() (HOME) when Chrome is from snap (sneak=True), creating
    .choreographer-* under HOME — not under TMPDIR.
    """
    override = (os.environ.get("KALEIDO_TMP_DIR") or "").strip()
    if override:
        tmp = os.path.abspath(override)
    else:
        d = os.path.abspath(output_dir)
        project_root = d
        while True:
            if os.path.isfile(os.path.join(project_root, "main.py")):
                break
            parent = os.path.dirname(project_root)
            if parent == project_root:
                project_root = os.path.abspath(os.getcwd())
                break
            project_root = parent
        tmp = os.path.join(project_root, "tmp", "kaleido")
    home = os.path.join(tmp, "home")
    os.makedirs(home, exist_ok=True)
    os.makedirs(tmp, exist_ok=True)
    return tmp, home


def _prepend_ld_library_path(chrome_path: str) -> Optional[str]:
    """
    Chrome for Testing ships shared libs next to the binary; without
    LD_LIBRARY_PATH the process often exits immediately on Linux.
    """
    chrome_dir = os.path.dirname(os.path.abspath(chrome_path))
    if not os.path.isdir(chrome_dir):
        return None
    prev = os.environ.get("LD_LIBRARY_PATH", "").strip()
    if prev:
        os.environ["LD_LIBRARY_PATH"] = f"{chrome_dir}:{prev}"
    else:
        os.environ["LD_LIBRARY_PATH"] = chrome_dir
    return os.environ["LD_LIBRARY_PATH"]


@contextmanager
def _kaleido_writable_env(
    output_dir: str, chrome_path: Optional[str] = None
) -> Iterator[tuple[str, str]]:
    """Point TMPDIR, HOME, and Chrome libs for the duration of chart export."""
    tmp, home = _resolve_kaleido_tmp_dir(output_dir)
    env_keys = ("TMPDIR", "TEMP", "TMP", "HOME", "LD_LIBRARY_PATH")
    saved = {key: os.environ.get(key) for key in env_keys}
    os.environ["TMPDIR"] = tmp
    os.environ["TEMP"] = tmp
    os.environ["TMP"] = tmp
    os.environ["HOME"] = home
    ld_path = _prepend_ld_library_path(chrome_path) if chrome_path else None
    try:
        yield tmp, home
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _is_usable_chrome(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _find_project_root() -> str:
    project_root = os.path.abspath(os.getcwd())
    while True:
        if os.path.isfile(os.path.join(project_root, "main.py")):
            return project_root
        parent = os.path.dirname(project_root)
        if parent == project_root:
            return os.path.abspath(os.getcwd())
        project_root = parent


def _chrome_from_project_install() -> Optional[str]:
    """Chrome from `plotly_get_chrome --path .../tmp/chrome-browser`."""
    pattern = os.path.join(
        _find_project_root(), "tmp", "chrome-browser", "chrome-*", "chrome"
    )
    for match in sorted(glob.glob(pattern)):
        if _is_usable_chrome(match) and "/snap/" not in match:
            return match
    return None


def _resolve_kaleido_browser_path() -> Optional[str]:
    """
    Prefer a non-snap Chrome/Chromium for Kaleido.

    Snap's /snap/bin/chromium often exits immediately under systemd (no TTY/sandbox).
    """
    for key in ("KALEIDO_CHROME_PATH", "BROWSER_PATH"):
        candidate = (os.environ.get(key) or "").strip()
        if _is_usable_chrome(candidate) and "/snap/" not in candidate:
            return os.path.abspath(candidate)

    project_chrome = _chrome_from_project_install()
    if project_chrome:
        return project_chrome

    try:
        from choreographer.cli._cli_utils import get_chrome_download_path

        local = get_chrome_download_path()
        if local is not None and local.exists() and _is_usable_chrome(str(local)):
            return str(local.resolve())
    except Exception:
        pass

    for candidate in (
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ):
        if _is_usable_chrome(candidate) and "/snap/" not in candidate:
            return candidate

    try:
        from choreographer.browsers.chromium import Chromium

        found = Chromium.find_browser(skip_local=False)
        if found and "/snap/" not in found:
            return found
    except Exception:
        pass

    return None


def process_csv(
    csv_path: str, chart_type: str = "bar", output_dir: str = "output"
) -> Dict:
    """
    Process CSV/Excel data and generate chart.

    Args:
        csv_path: Path to CSV/XLS/XLSX file
        chart_type: Type of chart (bar, pie, line)

    Returns:
        Dictionary with table_markdown, chart_path, chart_type, target_section, summary
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Tabular file not found: {csv_path}")

    # Load CSV/Excel
    lower = csv_path.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        try:
            df = pd.read_excel(csv_path)
        except ImportError as e:
            raise ValueError(
                "Excel support requires openpyxl (for .xlsx) and xlrd (for .xls). "
                "Install with: pip install openpyxl xlrd"
            ) from e
    else:
        df = pd.read_csv(csv_path)

    if df.empty:
        raise ValueError("Tabular file is empty")

    chart_type = normalize_chart_type(chart_type)
    if chart_type not in ("bar", "pie", "line"):
        raise ValueError(
            f"Unsupported chart type: {chart_type!r}. "
            "Use bar, pie, or line (e.g. 'Bar Chart' in the DB is OK)."
        )

    # Convert to markdown table
    table_markdown = df.to_markdown(index=False)

    # Generate summary
    num_rows = len(df)
    num_cols = len(df.columns)
    summary = f"Data showing {num_rows} entries across {num_cols} columns"

    # Generate chart
    base_name = os.path.basename(csv_path)
    chart_filename = f"chart_{os.path.splitext(base_name)[0]}.png"

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Use absolute path for chart
    chart_path = os.path.abspath(os.path.join(output_dir, chart_filename))

    print(f"Creating chart for {csv_path}")
    print(f"Chart will be saved to: {chart_path}")
    # Create chart based on type
    try:
        if chart_type == "bar":
            print(f"Creating bar chart for {csv_path}")
            # Use first column as x-axis, second column as y-axis
            x_col = df.columns[0]
            y_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

            fig = px.bar(df, x=x_col, y=y_col, title=f"{y_col} by {x_col}")

        elif chart_type == "pie":
            # Use first column as labels, second column as values
            labels_col = df.columns[0]
            values_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

            fig = go.Figure(data=[go.Pie(labels=df[labels_col], values=df[values_col])])
            fig.update_layout(title=f"{values_col} Distribution")

        elif chart_type == "line":
            # Use first column as x-axis, remaining columns as y-axis
            x_col = df.columns[0]

            fig = go.Figure()
            for col in df.columns[1:]:
                fig.add_trace(
                    go.Scatter(x=df[x_col], y=df[col], mode="lines+markers", name=col)
                )

            fig.update_layout(
                title=f"Trends over {x_col}", xaxis_title=x_col, yaxis_title="Values"
            )

        # Save chart as PNG (Kaleido v1: pass tmp_dir; set HOME for snap Chrome)
        import kaleido

        chrome_path = _resolve_kaleido_browser_path()
        if not chrome_path:
            raise ValueError(
                "Chart export needs Chrome for Kaleido, but only snap Chromium was found "
                "(or no browser). Install Chrome as the API user with a writable --path: "
                "sudo mkdir -p /var/www/donor_report/python/tmp/chrome-browser && "
                "sudo chown -R deploy:deploy /var/www/donor_report/python/tmp && "
                "sudo -u deploy bash -c 'cd /var/www/donor_report/python && "
                "source .venv/bin/activate && plotly_get_chrome -y "
                "--path /var/www/donor_report/python/tmp/chrome-browser' "
                "Then set BROWSER_PATH=.../tmp/chrome-browser/chrome-linux64/chrome in .env"
            )

        with _kaleido_writable_env(output_dir, chrome_path) as (kaleido_tmp, kaleido_home):
            ld = os.environ.get("LD_LIBRARY_PATH", "")
            print(
                f"Kaleido TMPDIR={kaleido_tmp} HOME={kaleido_home} "
                f"chrome={chrome_path} LD_LIBRARY_PATH={ld}"
            )
            img_bytes = kaleido.calc_fig_sync(
                fig,
                kopts={"tmp_dir": kaleido_tmp, "path": chrome_path},
            )
        with open(chart_path, "wb") as out:
            out.write(img_bytes)
        print(f"Chart saved: {chart_path}")

    except Exception as e:
        err = str(e)
        if isinstance(e, PermissionError) or "permission denied" in err.lower():
            tmp, home = _resolve_kaleido_tmp_dir(output_dir)
            raise ValueError(
                "Chart export could not write Kaleido temporary files. "
                "Ensure the API service user can write under "
                f"{tmp} and {home} (or set KALEIDO_TMP_DIR in .env). "
                "Snap Chromium uses HOME, not TMPDIR, for .choreographer-* dirs. "
                "Also check WorkingDirectory=/var/www/donor_report/python in systemd. "
                f"Original error: {err}"
            ) from e
        if (
            "kaleido" in err.lower()
            or "chrome" in err.lower()
            or "browser" in err.lower()
            or "chromium" in err.lower()
        ):
            snap_hint = ""
            if "/snap/" in err or "snap" in err.lower():
                snap_hint = (
                    " Do not use snap Chromium (/snap/bin/chromium) for this API. "
                    "Run plotly_get_chrome as the systemd User= account, then set "
                    "BROWSER_PATH in .env to the downloaded chrome binary."
                )
            elif "close immediately" in err.lower():
                snap_hint = (
                    " Test Chrome as deploy: "
                    "LD_LIBRARY_PATH=/var/www/donor_report/python/tmp/chrome-browser/chrome-linux64 "
                    "/var/www/donor_report/python/tmp/chrome-browser/chrome-linux64/chrome "
                    "--headless --no-sandbox --disable-gpu --dump-dom about:blank "
                    "If that fails, run: ldd .../chrome | grep 'not found' and install missing "
                    "libs (e.g. libnss3 libgbm1 libasound2 libatk-bridge2.0-0)."
                )
            raise ValueError(
                "Chart export requires Google Chrome for Kaleido (plotly). "
                "Ensure BROWSER_PATH points at plotly_get_chrome output under "
                "tmp/chrome-browser/chrome-linux64/chrome and restart donor-report-api."
                f"{snap_hint} Original error: {err}"
            ) from e
        raise ValueError(f"Error generating chart: {err}") from e

    return {
        "table_markdown": table_markdown,
        "chart_path": chart_path,
        "chart_type": chart_type,
        "target_section": "Impact Data",
        "summary": summary,
    }
