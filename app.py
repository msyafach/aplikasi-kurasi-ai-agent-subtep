from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from flask import Flask, Response, jsonify, render_template, request
import exiftool
from PIL import Image, ImageChops, ImageEnhance

# Resolve ExifTool executable: try PATH first, then known install location
_EXIFTOOL_CANDIDATES = [
    "exiftool",
    r"C:\Users\SARM2\AppData\Local\Programs\ExifTool\ExifTool.exe",
]
_EXIFTOOL_EXE: str = "exiftool"
for _candidate in _EXIFTOOL_CANDIDATES:
    try:
        import subprocess
        subprocess.run([_candidate, "-ver"], capture_output=True, check=True, timeout=5)
        _EXIFTOOL_EXE = _candidate
        break
    except Exception:
        continue


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (APP_DIR / path).resolve()


DEFAULT_CSV_PATH = resolve_path(os.getenv("CSV_PATH", "data/kendaraan.screen_document_180526.csv"))
DEFAULT_OUTPUT_KEEP = resolve_path(os.getenv("OUTPUT_KEEP", "data/kendaraan.screen_document_keep_180526.csv"))
DEFAULT_OUTPUT_DELETED = resolve_path(os.getenv("OUTPUT_DELETED", "data/kendaraan.screen_document_deleted_180526.csv"))
DEFAULT_OUTPUT_RAW = resolve_path(os.getenv("OUTPUT_RAW", "data/kendaraan.screen_document_raw_180526.csv"))
OUTPUT_ROOT = resolve_path(os.getenv("OUTPUT_DIR", "data/kurasi_outputs"))
DEFAULT_PROGRESS_FILE = resolve_path(os.getenv("PROGRESS_FILE", ".verifikasi_progress.json"))
DEFAULT_URL_COL = os.getenv("URL_COL", "Foto Kendaraan")
DEFAULT_LABEL_COL = os.getenv("LABEL_COL", "Status")
DEFAULT_FINETUNE_JSON_PATH = resolve_path(
    os.getenv("FINETUNE_JSON_PATH", "../finetuning/data_train_finetune_all_by_agent.json")
)
CURATION_TAXONOMY_PATH = resolve_path(
    os.getenv("CURATION_TAXONOMY_PATH", "curation_taxonomy.json")
)

CATEGORY_TO_REJECTION_CODE: dict[str, str] = {
    # CarPhoto
    "Nopol tidak cocok": "C-01",
    "Nopol editan": "C-02",
    "Foto kendaraan dari layar/cetakan": "C-07",
    "Foto kendaraan terindikasi edit": "C-09",
    "Foto kendaraan terindikasi buatan AI": "C-10",
    "Foto nopol salah sudut": "C-08",
    "Jumlah roda tidak sesuai": "C-06",
    "Jumlah roda tidak terlihat atau tidak bisa dikalkulasi": "C-05",
    "Foto bukan foto kendaraan": "C-11",
    "Kendaraan tidak berhak": "C-12",
    "Alasan penolakan foto kendaraan lainnya": "C-13",
    # OCR STNK
    "Dokumen STNK tidak lengkap": "K-01",
    "STNK terpotong": "K-02",
    "STNK buram": "K-03",
    "STNK non-asli (scan atau screenshot atau tidak berwarna)": "K-04",
    "STNK editan": "K-05",
    "STNK terindikasi buatan AI": "K-06",
    "Nopol tidak sesuai dengan dokumen": "K-07",
    "Warna plat tidak sesuai dengan dokumen": "K-08",
    "No rangka tidak sesuai dengan dokumen": "K-09",
    "CC tidak sesuai dengan dokumen": "K-10",
    "Jenis BBM tidak sesuai dengan dokumen": "K-11",
    "Alasan penolakan foto stnk lainnya": "K-12",
}

CURATED_CATEGORIES: list[str] = list(CATEGORY_TO_REJECTION_CODE.keys())

IMAGE_TIMEOUT_SECONDS = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "8"))
MAX_IMAGE_SIZE = tuple(int(x) for x in os.getenv("MAX_IMAGE_SIZE", "900,700").split(",", 1))


app = Flask(__name__)

CSV_PATH: Path = Path("")  # Set by load_dataset(); empty means no dataset loaded
OUTPUT_KEEP = DEFAULT_OUTPUT_KEEP
OUTPUT_DELETED = DEFAULT_OUTPUT_DELETED
OUTPUT_RAW = DEFAULT_OUTPUT_RAW
OUTPUT_SKIP: Path = OUTPUT_ROOT / "skip.csv"
PROGRESS_FILE = DEFAULT_PROGRESS_FILE
URL_COL = DEFAULT_URL_COL
LABEL_COL = DEFAULT_LABEL_COL
RUN_ID = ""
DATASET_MODE = "csv"
DATASET_FORMAT_PRESET = ""
FINETUNE_JSON_PATH = DEFAULT_FINETUNE_JSON_PATH
FINETUNE_DATA: dict[str, dict[str, list[dict[str, object]]]] = {}
FINETUNE_GROUP_KEY = ""
FINETUNE_AGENT_KEY = ""
ROW_ANNOTATIONS: dict[int, dict[str, str]] = {}
CSV_TAG_AGENT_KEY = ""
CSV_TAG_GROUP_KEY = ""
AGENT_KEY_COL = ""  # Original column name passed to load_dataset (may differ from "agent_key" after rename)

df = pd.DataFrame()
kept_indices: set[int] = set()
deleted_indices: set[int] = set()
skipped_indices: set[int] = set()
start_index = 0

CSV_FILE_HASH = ""
FINETUNE_FILE_HASH = ""

DB_PATH = APP_DIR / "kurasi.db"
SESSION_DB_ID: int | None = None


# ---------- SQLite helpers ----------

@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_path TEXT NOT NULL,
                dataset_hash TEXT NOT NULL,
                dataset_mode TEXT NOT NULL DEFAULT 'csv',
                finetune_agent_key TEXT NOT NULL DEFAULT '',
                tagged_agent_key TEXT NOT NULL DEFAULT '',
                tagged_group_key TEXT NOT NULL DEFAULT '',
                format_preset TEXT NOT NULL DEFAULT '',
                url_col TEXT NOT NULL DEFAULT '',
                label_col TEXT NOT NULL DEFAULT '',
                agent_key_col TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                current_index INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(dataset_path, dataset_hash, finetune_agent_key)
            );
            CREATE TABLE IF NOT EXISTS annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                row_index INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'raw',
                expected TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                agent_key TEXT NOT NULL DEFAULT '',
                reviewer_notes TEXT NOT NULL DEFAULT '',
                UNIQUE(session_id, row_index)
            );
        """)
        # Migrate existing DB: add agent_key_col if not present
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN agent_key_col TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # Column already exists


def _current_dataset_path_label() -> str:
    return path_label(CSV_PATH) if CSV_PATH.name else ""


def _session_key() -> tuple[str, str, str]:
    path = _current_dataset_path_label()
    if not path:
        return ("", "", "")
    if DATASET_MODE == "finetune_json":
        return (path, FINETUNE_FILE_HASH, FINETUNE_AGENT_KEY)
    return (path, CSV_FILE_HASH, "")


def _ensure_session() -> int:
    global SESSION_DB_ID
    if SESSION_DB_ID is not None:
        return SESSION_DB_ID

    ds_path, ds_hash, ft_agent = _session_key()
    if not ds_path:
        raise RuntimeError("No dataset loaded")

    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO sessions
            (dataset_path, dataset_hash, dataset_mode, finetune_agent_key,
             tagged_agent_key, tagged_group_key, format_preset, url_col, label_col, agent_key_col, run_id, current_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ds_path, ds_hash, DATASET_MODE, ft_agent,
            CSV_TAG_AGENT_KEY, CSV_TAG_GROUP_KEY,
            DATASET_FORMAT_PRESET, URL_COL, LABEL_COL, AGENT_KEY_COL, RUN_ID, start_index,
        ))
        row = conn.execute(
            "SELECT id FROM sessions WHERE dataset_path=? AND dataset_hash=? AND finetune_agent_key=?",
            (ds_path, ds_hash, ft_agent),
        ).fetchone()
        SESSION_DB_ID = row["id"]

    return SESSION_DB_ID


# ---------- end SQLite helpers ----------


def file_content_hash(path: Path, length: int = 8) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


def infer_group_key(agent_key: str) -> str:
    if agent_key.startswith("carphoto."):
        return "foto_kendaraan"
    if agent_key.startswith("ocr_stnk."):
        return "stnk"
    return ""


def safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return name or "dataset"


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def path_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(APP_DIR))
    except ValueError:
        return str(path.resolve())


FORMAT_PRESETS: list[dict[str, object]] = [
    {
        "id": "screen_document",
        "label": "Data Dashboard (kendaraan)",
        "url_col": "Foto Kendaraan",
        "label_col": "Status",
    },
    {
        "id": "data_train",
        "label": "Data Train (url / reviewer_label)",
        "url_col": "url",
        "label_col": "reviewer_label",
    },
    {
        "id": "custom",
        "label": "Custom (pilih manual)",
        "url_col": "",
        "label_col": "",
    },
]


def _scan_csv_dir(directory: Path, datasets: list) -> None:
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*.csv")):
        try:
            row_count = sum(1 for _ in path.open("rb")) - 1
        except OSError:
            row_count = None
        datasets.append(
            {
                "path": path_label(path),
                "name": path.name,
                "rows": max(row_count, 0) if row_count is not None else None,
            }
        )


def dataset_candidates() -> list[dict[str, object]]:
    datasets: list[dict[str, object]] = []
    seen: set[str] = set()
    for directory in [DATA_DIR, APP_DIR, APP_DIR / "uploads"]:
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.csv")):
            label = path_label(path)
            if label in seen:
                continue
            seen.add(label)
            try:
                row_count = sum(1 for _ in path.open("rb")) - 1
            except OSError:
                row_count = None
            datasets.append(
                {
                    "path": label,
                    "name": path.name,
                    "rows": max(row_count, 0) if row_count is not None else None,
                }
            )
    return datasets


def load_finetune_source(path_value: str | Path = DEFAULT_FINETUNE_JSON_PATH) -> dict[str, dict[str, list[dict[str, object]]]]:
    path = resolve_path(str(path_value))
    if not path.exists():
        raise FileNotFoundError(f"JSON finetuning tidak ditemukan: {path_label(path)}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON finetuning harus berbentuk object root")

    normalized: dict[str, dict[str, list[dict[str, object]]]] = {}
    for group_key, agents in data.items():
        if not isinstance(agents, dict):
            continue
        normalized[str(group_key)] = {}
        for agent_key, records in agents.items():
            if isinstance(records, list):
                normalized[str(group_key)][str(agent_key)] = [
                    record for record in records if isinstance(record, dict)
                ]
    return normalized


_TAXONOMY_CACHE: dict[str, dict[str, dict[str, list[str]]]] | None = None


def load_taxonomy() -> dict[str, dict[str, dict[str, list[str]]]]:
    """Lightweight agent/category/description config used for the curation dropdowns."""
    global _TAXONOMY_CACHE
    if _TAXONOMY_CACHE is None:
        try:
            _TAXONOMY_CACHE = json.loads(CURATION_TAXONOMY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _TAXONOMY_CACHE = {}
    return _TAXONOMY_CACHE


def finetune_agent_options() -> list[dict[str, object]]:
    if FINETUNE_DATA:
        return [
            {"group": group_key, "agent_key": agent_key, "count": len(records)}
            for group_key, agents in FINETUNE_DATA.items()
            for agent_key, records in agents.items()
        ]
    return [
        {"group": group_key, "agent_key": agent_key, "count": 0}
        for group_key, agents in load_taxonomy().items()
        for agent_key in agents
    ]


def finetune_categories(agent_key: str | None = None) -> list[str]:
    categories: set[str] = set()
    if FINETUNE_DATA:
        for agents in FINETUNE_DATA.values():
            for current_agent_key, records in agents.items():
                if agent_key and current_agent_key != agent_key:
                    continue
                for record in records:
                    value = str(record.get("category") or "").strip()
                    if value:
                        categories.add(value)
    else:
        for agents in load_taxonomy().values():
            for current_agent_key, meta in agents.items():
                if agent_key and current_agent_key != agent_key:
                    continue
                categories.update(c for c in meta.get("categories", []) if c)
    return sorted(categories)


def finetune_descriptions(agent_key: str | None = None, category: str | None = None) -> list[str]:
    descriptions: set[str] = set()
    if FINETUNE_DATA:
        for agents in FINETUNE_DATA.values():
            for current_agent_key, records in agents.items():
                if agent_key and current_agent_key != agent_key:
                    continue
                for record in records:
                    if category is not None and str(record.get("category") or "").strip() != category:
                        continue
                    value = str(record.get("description") or "").strip()
                    if value:
                        descriptions.add(value)
    else:
        for agents in load_taxonomy().values():
            for current_agent_key, meta in agents.items():
                if agent_key and current_agent_key != agent_key:
                    continue
                descriptions.update(d for d in meta.get("descriptions", []) if d)
    return sorted(descriptions)


def inferred_url_column(columns: list[str], requested: str | None = None) -> str:
    if requested in columns:
        return requested

    preferred = [DEFAULT_URL_COL, "Foto Kendaraan", "foto_kendaraan", "vehicle_photo", "url", "image_url"]
    for column in preferred:
        if column in columns:
            return column

    for column in columns:
        lowered = column.lower()
        if "foto" in lowered or "photo" in lowered or "image" in lowered or "url" in lowered:
            return column

    return columns[0] if columns else ""


def inferred_label_column(columns: list[str], requested: str | None = None) -> str:
    if requested in columns:
        return requested

    preferred = [DEFAULT_LABEL_COL, "Status", "status", "label", "expected"]
    for column in preferred:
        if column in columns:
            return column

    return columns[0] if columns else ""


def _csv_dir_id(csv_path: Path) -> str:
    stem = safe_filename(csv_path.stem)
    return f"{stem}_{CSV_FILE_HASH}" if CSV_FILE_HASH else stem


def _finetune_dir_id(json_path: Path) -> str:
    stem = safe_filename(json_path.stem)
    return f"{stem}_{FINETUNE_FILE_HASH}" if FINETUNE_FILE_HASH else stem


def output_paths_for(csv_path: Path, run_id: str) -> tuple[Path, Path, Path, Path]:
    dataset_dir = OUTPUT_ROOT / _csv_dir_id(csv_path) / run_id
    stem = safe_filename(csv_path.stem)
    return (
        dataset_dir / f"{stem}_approved.csv",
        dataset_dir / f"{stem}_rejected.csv",
        dataset_dir / f"{stem}_raw.csv",
        dataset_dir / f"{stem}_skip.csv",
    )


def finetune_output_paths_for(json_path: Path, agent_key: str, run_id: str) -> tuple[Path, Path, Path, Path]:
    dataset_dir = OUTPUT_ROOT / _finetune_dir_id(json_path) / safe_filename(agent_key) / run_id
    stem = safe_filename(json_path.stem)
    return (
        dataset_dir / f"{stem}_approved.json",
        dataset_dir / f"{stem}_rejected.json",
        dataset_dir / f"{stem}_raw.json",
        dataset_dir / f"{stem}_skip.json",
    )


def progress_path_for(csv_path: Path) -> Path:
    if csv_path.resolve() == DEFAULT_CSV_PATH.resolve():
        return DEFAULT_PROGRESS_FILE
    progress_dir = APP_DIR / ".progress"
    return progress_dir / f"{_csv_dir_id(csv_path)}.json"


def finetune_progress_path_for(json_path: Path, agent_key: str) -> Path:
    progress_dir = APP_DIR / ".progress"
    return progress_dir / f"{_finetune_dir_id(json_path)}__{safe_filename(agent_key)}.json"


def load_dataset(path_value: str | Path, url_col: str | None = None, label_col: str | None = None, agent_key_col: str | None = None, format_preset: str = "") -> None:
    global CSV_PATH, OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP, PROGRESS_FILE, URL_COL, LABEL_COL, RUN_ID, DATASET_MODE, DATASET_FORMAT_PRESET
    global df, kept_indices, deleted_indices, skipped_indices, start_index, ROW_ANNOTATIONS
    global CSV_TAG_AGENT_KEY, CSV_TAG_GROUP_KEY, CSV_FILE_HASH, SESSION_DB_ID, AGENT_KEY_COL
    DATASET_FORMAT_PRESET = format_preset

    CSV_TAG_AGENT_KEY = ""
    CSV_TAG_GROUP_KEY = ""
    SESSION_DB_ID = None

    csv_path = resolve_path(str(path_value))
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV tidak ditemukan: {path_label(csv_path)}")
    if csv_path.suffix.lower() != ".csv":
        raise ValueError("Dataset harus berupa file .csv")

    CSV_FILE_HASH = file_content_hash(csv_path)
    loaded_df = pd.read_csv(csv_path)
    columns = [str(column) for column in loaded_df.columns]

    # Store original column name before any rename (needed to reproduce the mapping on session resume)
    AGENT_KEY_COL = agent_key_col or ("agent_key" if "agent_key" in columns else "")

    if agent_key_col and agent_key_col != "agent_key" and agent_key_col in columns:
        loaded_df = loaded_df.rename(columns={agent_key_col: "agent_key"})
        columns = [str(column) for column in loaded_df.columns]

    if "agent_key" in columns and not loaded_df.empty:
        first_agent = str(loaded_df["agent_key"].iloc[0])
        if first_agent and pd.notna(first_agent):
            CSV_TAG_AGENT_KEY = first_agent
            CSV_TAG_GROUP_KEY = infer_group_key(first_agent)

    CSV_PATH = csv_path
    PROGRESS_FILE = progress_path_for(csv_path)
    URL_COL = inferred_url_column(columns, url_col)
    LABEL_COL = inferred_label_column(columns, label_col)
    RUN_ID = new_run_id()
    DATASET_MODE = "csv"

    df = loaded_df
    kept_indices = set()
    deleted_indices = set()
    skipped_indices = set()
    ROW_ANNOTATIONS = {}
    start_index = 0
    load_progress()
    OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = output_paths_for(CSV_PATH, RUN_ID)


def load_finetune_agent(agent_key: str, path_value: str | Path = DEFAULT_FINETUNE_JSON_PATH) -> None:
    global CSV_PATH, OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP, PROGRESS_FILE, URL_COL, LABEL_COL, RUN_ID, DATASET_MODE
    global FINETUNE_JSON_PATH, FINETUNE_DATA, FINETUNE_GROUP_KEY, FINETUNE_AGENT_KEY
    global df, kept_indices, deleted_indices, skipped_indices, start_index, ROW_ANNOTATIONS, FINETUNE_FILE_HASH, SESSION_DB_ID

    json_path = resolve_path(str(path_value))
    FINETUNE_FILE_HASH = file_content_hash(json_path)
    source = load_finetune_source(json_path)
    selected_group = ""
    selected_records: list[dict[str, object]] | None = None
    for group_key, agents in source.items():
        if agent_key in agents:
            selected_group = group_key
            selected_records = agents[agent_key]
            break

    if selected_records is None:
        raise ValueError(f"Agent key tidak ditemukan: {agent_key}")

    rows = []
    for idx, record in enumerate(selected_records):
        rows.append(
            {
                "_source_index": idx,
                "url": safe_value(record.get("url", "")),
                "expected": safe_value(record.get("expected", "")),
                "category": safe_value(record.get("category", "")),
                "description": safe_value(record.get("description", "")),
            }
        )

    SESSION_DB_ID = None
    FINETUNE_JSON_PATH = json_path
    FINETUNE_DATA = source
    FINETUNE_GROUP_KEY = selected_group
    FINETUNE_AGENT_KEY = agent_key
    CSV_PATH = json_path
    PROGRESS_FILE = finetune_progress_path_for(json_path, agent_key)
    URL_COL = "url"
    LABEL_COL = "expected"
    RUN_ID = new_run_id()
    DATASET_MODE = "finetune_json"

    df = pd.DataFrame(rows)
    kept_indices = set()
    deleted_indices = set()
    skipped_indices = set()
    ROW_ANNOTATIONS = {}
    start_index = 0
    load_progress()
    OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = finetune_output_paths_for(json_path, agent_key, RUN_ID)


def _parse_progress_txt(text: str) -> dict:
    """Parse legacy line-based progress format into a dict."""
    data: dict = {}
    lines = text.strip().splitlines()
    if lines and lines[0].strip().lstrip("-").isdigit():
        data["current_index"] = int(lines[0].strip())
    for line in lines[1:]:
        if line.startswith("RUN_ID:"):
            data["run_id"] = line[7:].strip()
        elif line.startswith(("APPROVED:", "KEPT:")):
            prefix_len = 9 if line.startswith("APPROVED:") else 5
            data["approved"] = [int(x) for x in line[prefix_len:].split(",") if x]
        elif line.startswith(("REJECTED:", "DELETED:")):
            prefix_len = 9 if line.startswith("REJECTED:") else 8
            data["rejected"] = [int(x) for x in line[prefix_len:].split(",") if x]
        elif line.startswith("SKIPPED:"):
            data["skipped"] = [int(x) for x in line[8:].split(",") if x]
        elif line.startswith("ANNOTATIONS:") and line[12:].strip():
            data["annotations"] = json.loads(line[12:])
    return data


def _migrate_all_progress() -> dict:
    """Scan all legacy .progress JSON/TXT files and import them into SQLite.
    Returns a dict with 'migrated', 'failed', and 'skipped' lists."""

    progress_dir = APP_DIR / ".progress"
    results: dict[str, list] = {"migrated": [], "failed": [], "skipped": []}

    # Collect candidate files from .progress/ dir and root-level defaults
    candidate_files: list[Path] = []
    if progress_dir.exists():
        candidate_files.extend(progress_dir.glob("*.json"))
        candidate_files.extend(progress_dir.glob("*.txt"))
    for root_file in [DEFAULT_PROGRESS_FILE, DEFAULT_PROGRESS_FILE.with_suffix(".txt")]:
        if root_file.exists() and root_file not in candidate_files:
            candidate_files.append(root_file)

    # Exclude already-migrated files
    candidate_files = [f for f in candidate_files if ".migrated" not in f.suffixes and f.suffix != ".migrated"]

    if not candidate_files:
        results["message"] = (
            f"Tidak ada file progress ditemukan.\n"
            f"Letakkan file .json/.txt di: {progress_dir}\n"
            f"atau file default di: {APP_DIR}"
        )
        results["expected_dir"] = str(progress_dir)
        return results

    # Build content-hash → csv_path lookup across data/ and uploads/
    csv_hash_map: dict[str, Path] = {}
    for search_dir in [DATA_DIR, APP_DIR / "uploads"]:
        if not search_dir.exists():
            continue
        for csv_path in sorted(search_dir.rglob("*.csv")):
            try:
                h = file_content_hash(csv_path)
                csv_hash_map.setdefault(h, csv_path)
            except OSError:
                pass

    for prog_file in sorted(candidate_files):
        stem = prog_file.stem

        # Detect finetune files by the __ separator (not yet supported for bulk migrate)
        if "__" in stem:
            results["skipped"].append({
                "file": prog_file.name,
                "reason": "File finetune JSON tidak didukung untuk migrasi massal. Muat agen secara manual dari UI untuk memigrasinya.",
            })
            continue

        # Default progress file → DEFAULT_CSV_PATH
        is_default = prog_file.name in (".verifikasi_progress.json", ".verifikasi_progress.txt")
        if is_default:
            if not DEFAULT_CSV_PATH.exists():
                results["failed"].append({
                    "file": prog_file.name,
                    "reason": f"File CSV default tidak ditemukan. Letakkan di: {DEFAULT_CSV_PATH}",
                    "expected_file": str(DEFAULT_CSV_PATH),
                })
                continue
            matched_csv = DEFAULT_CSV_PATH
            ds_hash = file_content_hash(DEFAULT_CSV_PATH)
        else:
            # Regular CSV progress: {safe_stem}_{hash8}
            parts = stem.rsplit("_", 1)
            ds_hash = parts[1] if len(parts) == 2 and len(parts[1]) == 8 else ""
            matched_csv = csv_hash_map.get(ds_hash)
            if not matched_csv:
                results["failed"].append({
                    "file": prog_file.name,
                    "reason": (
                        f"Tidak ada CSV dengan hash '{ds_hash}' ditemukan di folder data.\n"
                        f"Letakkan file CSV yang sesuai di: {path_label(DATA_DIR)}"
                    ),
                    "expected_dir": path_label(DATA_DIR),
                })
                continue

        ds_path = path_label(matched_csv)

        # Check if already in SQLite
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM sessions WHERE dataset_path=? AND dataset_hash=? AND finetune_agent_key=''",
                (ds_path, ds_hash),
            ).fetchone()
            if existing:
                results["skipped"].append({
                    "file": prog_file.name,
                    "reason": f"Dataset '{ds_path}' sudah ada di database (session id {existing['id']}).",
                })
                continue

        # Parse progress file
        try:
            raw = prog_file.read_text(encoding="utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = _parse_progress_txt(raw)
        except OSError as exc:
            results["failed"].append({"file": prog_file.name, "reason": f"Gagal membaca file: {exc}"})
            continue

        run_id = str(data.get("run_id", new_run_id()))
        current_index = int(data.get("current_index", 0))
        approved = set(int(x) for x in data.get("approved", []))
        rejected = set(int(x) for x in data.get("rejected", []))
        skipped_set = set(int(x) for x in data.get("skipped", []))
        raw_annotations = data.get("annotations", {})
        annotations = {
            int(k): {str(f): str(v) for f, v in ann.items()}
            for k, ann in raw_annotations.items()
            if isinstance(ann, dict)
        }

        try:
            with get_db() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO sessions
                    (dataset_path, dataset_hash, dataset_mode, finetune_agent_key,
                     tagged_agent_key, tagged_group_key, format_preset, url_col, label_col, run_id, current_index)
                    VALUES (?, ?, 'csv', '', '', '', '', '', '', ?, ?)
                """, (ds_path, ds_hash, run_id, current_index))

                session_row = conn.execute(
                    "SELECT id FROM sessions WHERE dataset_path=? AND dataset_hash=? AND finetune_agent_key=''",
                    (ds_path, ds_hash),
                ).fetchone()

                if not session_row:
                    results["failed"].append({"file": prog_file.name, "reason": "Gagal membuat session di database."})
                    continue

                session_id = session_row["id"]
                all_indices = approved | rejected | skipped_set | set(annotations.keys())
                for idx in all_indices:
                    status = (
                        "approved" if idx in approved
                        else "rejected" if idx in rejected
                        else "skipped" if idx in skipped_set
                        else "raw"
                    )
                    annotation = annotations.get(idx, {})
                    conn.execute("""
                        INSERT OR IGNORE INTO annotations
                        (session_id, row_index, status, expected, category, description, agent_key, reviewer_notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        session_id, idx, status,
                        annotation.get("expected", ""), annotation.get("category", ""),
                        annotation.get("description", ""), annotation.get("agent_key", ""),
                        annotation.get("reviewer_notes", ""),
                    ))
        except Exception as exc:
            results["failed"].append({"file": prog_file.name, "reason": f"Error database: {exc}"})
            continue

        try:
            prog_file.rename(prog_file.with_suffix(".migrated"))
        except OSError:
            pass

        results["migrated"].append({
            "file": prog_file.name,
            "dataset": ds_path,
            "annotations": len(all_indices),
            "current_index": current_index,
        })

    return results


def load_progress() -> None:
    global start_index, RUN_ID, ROW_ANNOTATIONS, SESSION_DB_ID, CSV_TAG_AGENT_KEY, CSV_TAG_GROUP_KEY

    ds_path, ds_hash, ft_agent = _session_key()
    if not ds_path:
        SESSION_DB_ID = None
        return

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE dataset_path=? AND dataset_hash=? AND finetune_agent_key=?",
            (ds_path, ds_hash, ft_agent),
        ).fetchone()

        if not row:
            SESSION_DB_ID = None
            return

        SESSION_DB_ID = row["id"]
        start_index = min(max(row["current_index"], 0), max(len(df) - 1, 0))
        if row["run_id"]:
            RUN_ID = row["run_id"]

        # Restore tagged agent key across server restarts
        if DATASET_MODE == "csv" and row["tagged_agent_key"]:
            CSV_TAG_AGENT_KEY = row["tagged_agent_key"]
            CSV_TAG_GROUP_KEY = row["tagged_group_key"] or infer_group_key(row["tagged_agent_key"])

        ann_rows = conn.execute(
            "SELECT row_index, status, expected, category, description, agent_key, reviewer_notes "
            "FROM annotations WHERE session_id=?",
            (SESSION_DB_ID,),
        ).fetchall()

    valid = set(range(len(df)))
    for ann_row in ann_rows:
        idx = ann_row["row_index"]
        if idx not in valid:
            continue
        status = ann_row["status"]
        if status == "approved":
            kept_indices.add(idx)
        elif status == "rejected":
            deleted_indices.add(idx)
        elif status == "skipped":
            skipped_indices.add(idx)
        annotation: dict[str, str] = {}
        for field in ("expected", "category", "description", "agent_key", "reviewer_notes"):
            val = ann_row[field]
            if val:
                annotation[field] = val
        if annotation:
            ROW_ANNOTATIONS[idx] = annotation


def save_progress(current_idx: int) -> None:
    global start_index
    start_index = current_idx

    try:
        session_id = _ensure_session()
    except RuntimeError:
        return  # No dataset loaded

    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET current_index=?, run_id=?, last_accessed=datetime('now') WHERE id=?",
            (current_idx, RUN_ID, session_id),
        )
        all_annotated = (kept_indices | deleted_indices | skipped_indices) | set(ROW_ANNOTATIONS.keys())
        for idx in all_annotated:
            status = (
                "approved" if idx in kept_indices
                else "rejected" if idx in deleted_indices
                else "skipped" if idx in skipped_indices
                else "raw"
            )
            annotation = ROW_ANNOTATIONS.get(idx, {})
            conn.execute("""
                INSERT INTO annotations
                    (session_id, row_index, status, expected, category, description, agent_key, reviewer_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, row_index) DO UPDATE SET
                    status=excluded.status,
                    expected=excluded.expected,
                    category=excluded.category,
                    description=excluded.description,
                    agent_key=excluded.agent_key,
                    reviewer_notes=excluded.reviewer_notes
            """, (
                session_id, idx, status,
                annotation.get("expected", ""),
                annotation.get("category", ""),
                annotation.get("description", ""),
                annotation.get("agent_key", ""),
                annotation.get("reviewer_notes", ""),
            ))


def save_results() -> dict[str, object]:
    reviewed_indices = kept_indices | deleted_indices | skipped_indices
    raw_indices = set(range(len(df))) - reviewed_indices

    for path in (OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP):
        path.parent.mkdir(parents=True, exist_ok=True)

    if DATASET_MODE == "finetune_json":
        write_json_output(OUTPUT_KEEP, kept_indices)
        write_json_output(OUTPUT_DELETED, deleted_indices)
        write_json_output(OUTPUT_SKIP, skipped_indices)
        write_json_output(OUTPUT_RAW, raw_indices)
        keep_count = len(kept_indices)
        deleted_count = len(deleted_indices)
        skip_count = len(skipped_indices)
        raw_count = len(raw_indices)
    else:
        keep_df = df.loc[sorted(kept_indices)]
        deleted_df = df.loc[sorted(deleted_indices)]
        skip_df = df.loc[sorted(skipped_indices)]
        raw_df = df.loc[sorted(raw_indices)]
        keep_df.to_csv(OUTPUT_KEEP, index=False)
        deleted_df.to_csv(OUTPUT_DELETED, index=False)
        skip_df.to_csv(OUTPUT_SKIP, index=False)
        raw_df.to_csv(OUTPUT_RAW, index=False)
        keep_count = len(keep_df)
        deleted_count = len(deleted_df)
        skip_count = len(skip_df)
        raw_count = len(raw_df)

    return {
        "keep": keep_count,
        "deleted": deleted_count,
        "approved": keep_count,
        "rejected": deleted_count,
        "skipped": skip_count,
        "raw": raw_count,
        "paths": {
            "approved": str(OUTPUT_KEEP),
            "rejected": str(OUTPUT_DELETED),
            "skipped": str(OUTPUT_SKIP),
            "raw": str(OUTPUT_RAW),
        },
    }


def indices_for_scope(scope: str) -> set[int]:
    scope_aliases = {"approved": "keep", "rejected": "deleted", "skip": "skipped"}
    scope = scope_aliases.get(scope, scope)
    all_indices = set(range(len(df)))
    reviewed_indices = kept_indices | deleted_indices | skipped_indices
    raw_indices = all_indices - reviewed_indices

    if scope == "keep":
        return set(kept_indices)
    if scope == "deleted":
        return set(deleted_indices)
    if scope == "skipped":
        return set(skipped_indices)
    if scope == "labeled":
        return kept_indices | deleted_indices
    if scope == "raw":
        return raw_indices
    if scope == "reviewed":
        return reviewed_indices
    if scope == "all":
        return all_indices
    if scope == "no_category":
        return {
            idx for idx in reviewed_indices
            if not ROW_ANNOTATIONS.get(idx, {}).get("category", "").strip()
        }
    raise ValueError(f"Scope export tidak dikenal: {scope}")


def full_labelling_skeleton() -> dict[str, dict[str, list]]:
    """Return {group: {agent_key: []}} for every key in the finetune JSON source."""
    try:
        source = FINETUNE_DATA if FINETUNE_DATA else load_finetune_source(DEFAULT_FINETUNE_JSON_PATH)
        return {
            group_key: {agent_key: [] for agent_key in agents.keys()}
            for group_key, agents in source.items()
        }
    except Exception:
        return {}


def export_path_for(scope: str, export_format: str) -> Path:
    mode_name = "finetune" if DATASET_MODE == "finetune_json" else "csv"
    if DATASET_MODE == "finetune_json":
        base_dir = OUTPUT_ROOT / _finetune_dir_id(CSV_PATH) / safe_filename(FINETUNE_AGENT_KEY)
    else:
        base_dir = OUTPUT_ROOT / _csv_dir_id(CSV_PATH)

    suffix = "json" if export_format in {"json_labelling", "raw_json", "test_reason_json"} else "csv"
    return base_dir / RUN_ID / f"export_{mode_name}_{safe_filename(scope)}_{safe_filename(export_format)}.{suffix}"


def export_records(scope: str, export_format: str) -> dict[str, object]:
    indices = indices_for_scope(scope)
    output_path = export_path_for(scope, export_format)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if export_format == "raw":
        if DATASET_MODE == "finetune_json":
            records = [annotated_record(idx) for idx in sorted(indices)]
            output_path = output_path.with_suffix(".json")
            output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            df.loc[sorted(indices)].to_csv(output_path, index=False)
    elif export_format == "raw_json":
        records = [row_to_labelling_record(idx) for idx in sorted(indices)]
        output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif export_format == "json_labelling":
        if DATASET_MODE == "finetune_json":
            write_json_output(output_path, indices)
        else:
            output = full_labelling_skeleton()
            flat_records = []

            for idx in sorted(indices):
                record = row_to_labelling_record(idx)

                row_agent = ""
                annotation = ROW_ANNOTATIONS.get(idx, {})
                if annotation.get("agent_key"):
                    row_agent = str(annotation["agent_key"]).strip()
                elif "agent_key" in df.columns:
                    row_agent = str(df.iloc[idx].get("agent_key", "")).strip()
                if row_agent and (pd.isna(row_agent) or row_agent == "nan"):
                    row_agent = ""
                if not row_agent:
                    row_agent = CSV_TAG_AGENT_KEY

                if row_agent:
                    row_group = infer_group_key(row_agent)
                    if row_group:
                        output.setdefault(row_group, {}).setdefault(row_agent, []).append(record)
                    else:
                        flat_records.append(record)
                else:
                    flat_records.append(record)

            if not any(output.values()) and flat_records:
                output_path.write_text(json.dumps(flat_records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif export_format == "test_reason_json":
        records = [row_to_test_reason_record(idx) for idx in sorted(indices)]
        output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif export_format == "data_train_reviewed":
        output_path = output_path.with_suffix(".csv")
        rows = []
        for idx in sorted(indices):
            row = df.iloc[idx].to_dict()
            annotation = annotation_for_idx(idx)
            row[LABEL_COL] = annotation["expected"]
            row["reviewer_notes"] = annotation.get("reviewer_notes", row.get("reviewer_notes", ""))
            rows.append(row)
        pd.DataFrame(rows).to_csv(output_path, index=False)
    else:
        raise ValueError(f"Format export tidak dikenal: {export_format}")

    return {
        "scope": scope,
        "format": export_format,
        "count": len(indices),
        "path": str(output_path),
    }


def preview_records(scope: str, export_format: str, limit: int = 3) -> dict[str, object]:
    indices = sorted(indices_for_scope(scope))
    preview_indices = set(indices[: max(limit, 1)])

    if export_format == "test_reason_json":
        preview: object = [row_to_test_reason_record(idx) for idx in sorted(preview_indices)]
    elif export_format == "json_labelling" and DATASET_MODE == "finetune_json":
        output = {
            group_key: {agent_key: [] for agent_key in agents.keys()}
            for group_key, agents in FINETUNE_DATA.items()
        }
        output[FINETUNE_GROUP_KEY][FINETUNE_AGENT_KEY] = [
            annotated_record(idx) for idx in sorted(preview_indices)
        ]
        preview = output
    elif export_format in {"json_labelling", "raw_json"}:
        if export_format == "json_labelling" and DATASET_MODE == "csv":
            output = full_labelling_skeleton()
            flat_records = []

            for idx in sorted(preview_indices):
                record = row_to_labelling_record(idx)
                row_agent = ""
                annotation = ROW_ANNOTATIONS.get(idx, {})
                if annotation.get("agent_key"):
                    row_agent = str(annotation["agent_key"]).strip()
                elif "agent_key" in df.columns:
                    row_agent = str(df.iloc[idx].get("agent_key", "")).strip()
                if row_agent and (pd.isna(row_agent) or row_agent == "nan"):
                    row_agent = ""
                if not row_agent:
                    row_agent = CSV_TAG_AGENT_KEY

                if row_agent:
                    row_group = infer_group_key(row_agent)
                    if row_group:
                        output.setdefault(row_group, {}).setdefault(row_agent, []).append(record)
                    else:
                        flat_records.append(record)
                else:
                    flat_records.append(record)

            if not any(output.values()) and flat_records:
                preview = flat_records
            else:
                preview = output
        else:
            preview = [row_to_labelling_record(idx) for idx in sorted(preview_indices)]
    elif export_format == "raw":
        if DATASET_MODE == "finetune_json":
            preview = [annotated_record(idx) for idx in sorted(preview_indices)]
        else:
            preview = df.loc[sorted(preview_indices)].fillna("").to_dict(orient="records")
    elif export_format == "data_train_reviewed":
        rows = []
        for idx in sorted(preview_indices):
            row = df.iloc[idx].to_dict()
            annotation = annotation_for_idx(idx)
            row[LABEL_COL] = annotation["expected"]
            row["reviewer_notes"] = annotation.get("reviewer_notes", row.get("reviewer_notes", ""))
            rows.append({k: ("" if isinstance(v, float) and pd.isna(v) else v) for k, v in row.items()})
        preview = rows
    else:
        raise ValueError(f"Format preview tidak dikenal: {export_format}")

    return {
        "scope": scope,
        "format": export_format,
        "total": len(indices),
        "shown": len(preview_indices),
        "preview": preview,
    }


def annotated_record(idx: int) -> dict[str, object]:
    if DATASET_MODE != "finetune_json":
        return {}

    source_idx = int(df.iloc[idx].get("_source_index", idx))
    record = dict(FINETUNE_DATA[FINETUNE_GROUP_KEY][FINETUNE_AGENT_KEY][source_idx])
    annotation = ROW_ANNOTATIONS.get(idx, {})
    for field in ("expected", "category", "description"):
        if field in annotation:
            record[field] = annotation[field]
    return record


def row_to_labelling_record(idx: int) -> dict[str, object]:
    if DATASET_MODE == "finetune_json":
        return annotated_record(idx)

    row = df.iloc[idx]
    annotation = annotation_for_idx(idx)
    record = {
        "url": safe_value(row.get(URL_COL, "")),
        "expected": annotation["expected"],
        "category": split_categories(annotation["category"]),
        "description": annotation["description"],
    }
    if annotation.get("reviewer_notes"):
        record["reviewer_notes"] = annotation["reviewer_notes"]
    return record


def parse_reading(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if value is None or pd.isna(value):
        return {}

    text = str(value).strip()
    if not text:
        return {}

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def first_value(*values: object) -> object:
    for value in values:
        if value is None:
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        text = safe_value(value)
        if text != "":
            return value
    return ""


def json_safe(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return ""
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def annotation_for_idx(idx: int) -> dict[str, str]:
    row = df.iloc[idx]
    annotation = ROW_ANNOTATIONS.get(idx, {})
    if "expected" not in annotation:
        if idx in kept_indices:
            annotation = {**annotation, "expected": "APPROVED"}
        elif idx in deleted_indices:
            annotation = {**annotation, "expected": "REJECTED"}
        elif idx in skipped_indices:
            annotation = {**annotation, "expected": "SKIP"}

    return {
        "expected": annotation.get("expected", safe_value(row.get("expected", row.get(LABEL_COL, "")))),
        "category": annotation.get("category", safe_value(row.get("category", ""))),
        "description": annotation.get(
            "description",
            safe_value(row.get("description", row.get("Response Reason", ""))),
        ),
        "reviewer_notes": annotation.get("reviewer_notes", safe_value(row.get("reviewer_notes", ""))),
        "agent_key": annotation.get("agent_key", safe_value(row.get("agent_key", ""))),
    }


CATEGORY_SEPARATOR = "; "


def split_categories(category: object) -> list[str]:
    return [part.strip() for part in str(category or "").split(";") if part.strip()]


def category_to_codes(category: object) -> list[str]:
    codes = []
    for name in split_categories(category):
        code = CATEGORY_TO_REJECTION_CODE.get(name, "")
        if code:
            codes.append(code)
    return codes


def row_to_test_reason_record(idx: int) -> dict[str, object]:
    if DATASET_MODE == "finetune_json":
        record = annotated_record(idx)
        category = safe_value(record.get("category", ""))
        return {
            "web_registerasi_detail_id": "",
            "web_register_id": "",
            "police_number": "",
            "nomor_rangka": "",
            "stnk_photo": record.get("url", "") if FINETUNE_GROUP_KEY == "stnk" else "",
            "vehicle_photo": record.get("url", "") if FINETUNE_GROUP_KEY == "foto_kendaraan" else "",
            "fuel_oil_type": "",
            "wheel_count": "",
            "cubicle_centimeter": "",
            "plate_color": "",
            "mapped_rejection_code": category_to_codes(category),
            "mapped_rejection_category": split_categories(category),
            "mapped_rejection_message": "",
            "expected": record.get("expected", ""),
            "description": record.get("description", ""),
            "is_valid_verifikasi_ulang": None,
            "status": "",
        }

    row = df.iloc[idx]
    stnk_reading = parse_reading(row.get("STNK Reading"))
    vehicle_reading = parse_reading(row.get("Vehicle Reading"))
    annotation = annotation_for_idx(idx)
    categories = split_categories(annotation["category"])
    derived_codes = category_to_codes(annotation["category"])
    if not categories:
        categories = split_categories(row.get("mapped_rejection_category", ""))
        derived_codes = split_categories(row.get("mapped_rejection_code", ""))

    return {
        "web_registerasi_detail_id": safe_value(
            first_value(row.get("web_registerasi_detail_id"), row.get("Web Registerasi Detail ID"), row.get("id"))
        ),
        "web_register_id": safe_value(first_value(row.get("web_register_id"), row.get("Web Register ID"))),
        "police_number": safe_value(
            first_value(
                row.get("police_number"),
                row.get("Nopol"),
                stnk_reading.get("police_number"),
                stnk_reading.get("police_number_v2"),
                vehicle_reading.get("police_number"),
            )
        ),
        "nomor_rangka": safe_value(first_value(row.get("nomor_rangka"), row.get("Nomor Rangka"), stnk_reading.get("nomor_rangka"))),
        "stnk_photo": safe_value(first_value(row.get("stnk_photo"), row.get("Foto STNK"))),
        "vehicle_photo": safe_value(first_value(row.get("vehicle_photo"), row.get("Foto Kendaraan"))),
        "fuel_oil_type": safe_value(first_value(row.get("fuel_oil_type"), stnk_reading.get("fuel_oil_type"))),
        "wheel_count": safe_value(first_value(row.get("wheel_count"), vehicle_reading.get("wheel_count"))),
        "cubicle_centimeter": safe_value(first_value(row.get("cubicle_centimeter"), stnk_reading.get("cubicle_centimeter"))),
        "plate_color": safe_value(first_value(row.get("plate_color"), stnk_reading.get("plat_color"), vehicle_reading.get("plat_color"))),
        "mapped_rejection_code": derived_codes,
        "mapped_rejection_category": categories,
        "mapped_rejection_message": safe_value(row.get("mapped_rejection_message", "")),
        "expected": annotation["expected"],
        "description": annotation["description"],
        "is_valid_verifikasi_ulang": None if safe_value(row.get("is_valid_verifikasi_ulang", "")) == "" else row.get("is_valid_verifikasi_ulang"),
        "status": json_safe(row.get("status", row.get("Status", ""))),
    }


def write_json_output(path: Path, indices: set[int]) -> None:
    output = {
        group_key: {agent_key: [] for agent_key in agents.keys()}
        for group_key, agents in FINETUNE_DATA.items()
    }
    output[FINETUNE_GROUP_KEY][FINETUNE_AGENT_KEY] = [
        annotated_record(idx) for idx in sorted(indices)
    ]
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reset_review(delete_outputs: bool = False) -> dict[str, object]:
    global kept_indices, deleted_indices, skipped_indices, start_index, RUN_ID, OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP, ROW_ANNOTATIONS

    kept_indices = set()
    deleted_indices = set()
    skipped_indices = set()
    ROW_ANNOTATIONS = {}
    start_index = 0
    RUN_ID = new_run_id()

    if SESSION_DB_ID is not None:
        with get_db() as conn:
            conn.execute("DELETE FROM annotations WHERE session_id=?", (SESSION_DB_ID,))
            conn.execute(
                "UPDATE sessions SET current_index=0, run_id=?, last_accessed=datetime('now') WHERE id=?",
                (RUN_ID, SESSION_DB_ID),
            )

    deleted_files = []
    if delete_outputs:
        for path in (OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP):
            if path.exists():
                path.unlink()
                deleted_files.append(path_label(path))

    if DATASET_MODE == "finetune_json":
        OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = finetune_output_paths_for(CSV_PATH, FINETUNE_AGENT_KEY, RUN_ID)
    else:
        OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = output_paths_for(CSV_PATH, RUN_ID)

    return {
        "run_id": RUN_ID,
        "db_path": str(DB_PATH),
        "deleted_outputs": deleted_files,
    }


def row_status(idx: int) -> str:
    if idx in kept_indices:
        return "keep"
    if idx in deleted_indices:
        return "deleted"
    if idx in skipped_indices:
        return "skipped"
    return "raw"


def get_row_payload(idx: int) -> dict[str, object]:
    if df.empty:
        return {
            "idx": 0,
            "row_number": 0,
            "total": 0,
            "status": "raw",
            "nopol": "",
            "label": "",
            "reason": "",
            "url": "",
            "counts": {"keep": 0, "deleted": 0, "raw": 0},
        }

    idx = min(max(idx, 0), max(len(df) - 1, 0))
    row = df.iloc[idx]
    annotation = annotation_for_idx(idx)
    raw_count = len(df) - len(kept_indices) - len(deleted_indices) - len(skipped_indices)

    return {
        "idx": idx,
        "row_number": idx + 1,
        "total": len(df),
        "status": row_status(idx),
        "nopol": safe_value(row.get("Nopol", "-")),
        "label": annotation.get("expected", safe_value(row.get(LABEL_COL, "-"))),
        "reason": annotation.get("description", safe_value(row.get("Response Reason", row.get("description", "-")))),
        "url": safe_value(row.get(URL_COL, "")),
        "annotation": {
            "expected": annotation["expected"],
            "category": annotation["category"],
            "description": annotation["description"],
            "agent_key": annotation.get("agent_key", ""),
            "reviewer_notes": annotation.get("reviewer_notes", ""),
        },
        "readings": {
            "stnk": parse_reading(row.get("STNK Reading")),
            "vehicle": parse_reading(row.get("Vehicle Reading")),
        },
        "description_options": finetune_descriptions(FINETUNE_AGENT_KEY, annotation.get("category", safe_value(row.get("category", ""))))
        if DATASET_MODE == "finetune_json"
        else [],
        "dataset": dataset_payload(),
        "ai_info": {
            "ground_truth": safe_value(row.get("ground_truth", "")),
            "gemini_verdict": safe_value(row.get("gemini_verdict", "")),
            "gemini_reason": safe_value(row.get("gemini_reason", "")),
            "confidence": safe_value(row.get("confidence", "")),
            "main_flags": safe_value(row.get("main_flags", "")),
        },
        "extra_data": {
            str(col): safe_value(row.get(col))
            for col in df.columns
            if col not in (URL_COL, LABEL_COL)
        },
        "counts": {
            "keep": len(kept_indices),
            "deleted": len(deleted_indices),
            "skipped": len(skipped_indices),
            "raw": raw_count,
        },
    }


def safe_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if pd.isna(value):
        return ""
    return str(value)


def fetch_image_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=IMAGE_TIMEOUT_SECONDS)
    response.raise_for_status()

    image = Image.open(BytesIO(response.content)).convert("RGB")
    image.thumbnail(MAX_IMAGE_SIZE)

    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def fetch_raw_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=IMAGE_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def compute_ela(image_bytes: bytes, quality: int = 95, amplify: int = 15) -> bytes:
    """Error Level Analysis: re-compress then diff. Bright areas = likely edited."""
    original = Image.open(BytesIO(image_bytes)).convert("RGB")
    # Cap size so processing stays fast
    original.thumbnail((1200, 1200))

    buf = BytesIO()
    original.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    recompressed = Image.open(buf).convert("RGB")

    ela = ImageChops.difference(original, recompressed)
    ela = ela.point(lambda x: min(x * amplify, 255))

    out = BytesIO()
    ela.save(out, "JPEG", quality=95)
    return out.getvalue()


def analyze_image_metadata(image_bytes: bytes) -> dict:
    """Extract metadata via ExifTool, grouped by namespace, with forensic flags."""
    result: dict = {"file_info": {}, "groups": {}, "flags": [], "risk": "rendah"}
    tmp_path = APP_DIR / f"_forensic_tmp_{os.getpid()}.jpg"
    # Namespaces to skip entirely (internal/uninteresting)
    _skip_ns = {"ExifTool", "File"}
    # File-info keys to surface separately
    _file_keys = {"FileType", "FileTypeExtension", "MIMEType", "ImageWidth",
                  "ImageHeight", "FileSize", "ColorComponents", "BitsPerSample",
                  "EncodingProcess", "YCbCrSubSampling"}
    try:
        tmp_path.write_bytes(image_bytes)
        with exiftool.ExifToolHelper(executable=_EXIFTOOL_EXE) as et:
            raw: dict = (et.get_metadata(str(tmp_path)) or [{}])[0]

        # Build flat lookup (short key → value) for flag analysis
        flat: dict[str, str] = {}
        # Build grouped display dict
        groups: dict[str, dict[str, str]] = {}
        for full_key, value in raw.items():
            if full_key == "SourceFile":
                continue
            ns, _, short = full_key.partition(":")
            if not short:          # no namespace
                ns, short = "Other", full_key
            try:
                str_val = str(value)[:400]
            except Exception:
                str_val = ""
            flat[short] = str_val  # last-wins for duplicates
            if ns in _skip_ns:
                # Pull file info out of File: namespace
                if ns == "File" and short in _file_keys:
                    result["file_info"][short] = str_val
                continue
            # Skip binary placeholders
            if "(Binary data" in str_val:
                continue
            groups.setdefault(ns, {})[short] = str_val

        result["groups"] = groups

        # ---- Forensic flag analysis ----
        flags: list[str] = []
        software = flat.get("Software", "").lower()
        creator_tool = flat.get("CreatorTool", "").lower()
        history_sw = flat.get("HistorySoftwareAgent", "").lower()
        all_sw = f"{software} {creator_tool} {history_sw}"

        ai_tools = ["stable diffusion", "midjourney", "dall-e", "dall·e", "firefly", "imagen",
                    "ideogram", "bing image creator", "adobe firefly", "flux", "sora", "runway"]
        editing_tools = ["photoshop", "gimp", "lightroom", "canva", "paint.net", "pixlr",
                         "snapseed", "vsco", "affinity", "capture one", "darktable", "rawtherapee"]

        if any(t in all_sw for t in ai_tools):
            flags.append(f"🚨 Software AI terdeteksi: {flat.get('Software') or flat.get('CreatorTool', '')}")
        elif any(t in all_sw for t in editing_tools):
            flags.append(f"⚠ Software editing terdeteksi: {flat.get('Software') or flat.get('CreatorTool', '')}")

        dt = flat.get("ModifyDate", flat.get("DateTime", ""))
        dto = flat.get("DateTimeOriginal", flat.get("CreateDate", ""))
        if dt and dto and dt != dto:
            flags.append(f"⚠ Tanggal modifikasi ({dt}) ≠ tanggal pengambilan ({dto})")

        if flat.get("HistoryAction") or flat.get("History"):
            flags.append("⚠ Riwayat edit XMP terdeteksi (kemungkinan diedit di Photoshop/Illustrator)")

        profile_desc = flat.get("ProfileDescription", "").lower()
        if profile_desc and "srgb" not in profile_desc:
            flags.append(f"ℹ Profil warna non-standar: {flat.get('ProfileDescription', '')}")

        has_camera_meta = any(flat.get(k) for k in ("Make", "Model", "DateTimeOriginal", "Software", "GPSLatitude"))
        if not has_camera_meta:
            flags.append("ℹ Tidak ada metadata kamera — foto dari HP/kamera biasanya memiliki EXIF (Make, Model, dll.)")

        if flat.get("FileType", "").upper() == "PNG":
            flags.append("ℹ Format PNG — umum untuk screenshot atau hasil editing/AI")

        thumb_w, img_w = flat.get("ThumbnailImageWidth", ""), flat.get("ImageWidth", "")
        if thumb_w and img_w:
            try:
                ratio = int(img_w) / int(thumb_w)
                if not (1.5 <= ratio <= 20):
                    flags.append(f"ℹ Rasio thumbnail/gambar tidak lazim ({thumb_w} vs {img_w}px)")
            except (ValueError, ZeroDivisionError):
                pass

        result["flags"] = flags
        result["risk"] = (
            "tinggi" if any("🚨" in f for f in flags)
            else "sedang" if any("⚠" in f for f in flags)
            else "rendah"
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    return result


def dataset_payload() -> dict[str, object]:
    return {
        "mode": DATASET_MODE,
        "format_preset": DATASET_FORMAT_PRESET,
        "path": _current_dataset_path_label(),
        "columns": list(df.columns),
        "url_col": URL_COL,
        "label_col": LABEL_COL,
        "agent_key_col": AGENT_KEY_COL,
        "run_id": RUN_ID,
        "finetune": {
            "json_path": path_label(FINETUNE_JSON_PATH),
            "group_key": FINETUNE_GROUP_KEY,
            "agent_key": FINETUNE_AGENT_KEY,
            "csv_tag_agent_key": CSV_TAG_AGENT_KEY,
            "csv_tag_group_key": CSV_TAG_GROUP_KEY,
            "agents": finetune_agent_options(),
            "categories": finetune_categories(FINETUNE_AGENT_KEY) if DATASET_MODE == "finetune_json" else [],
            "all_categories": finetune_categories(),
            "descriptions": finetune_descriptions(FINETUNE_AGENT_KEY) if DATASET_MODE == "finetune_json" else [],
        },
        "progress_file": str(DB_PATH),
        "start_index": start_index,
        "curated_categories": CURATED_CATEGORIES,
        "outputs": {
            "approved": path_label(OUTPUT_KEEP),
            "rejected": path_label(OUTPUT_DELETED),
            "skipped": path_label(OUTPUT_SKIP),
            "raw": path_label(OUTPUT_RAW),
        },
    }


@app.get("/")
def index() -> str:
    return render_template(
        "index.html",
        config={
            "csv_path": _current_dataset_path_label(),
            "url_col": URL_COL,
            "label_col": LABEL_COL,
            "start_index": start_index,
            "dataset": dataset_payload(),
        },
    )


@app.get("/api/datasets")
def api_datasets() -> Response:
    return jsonify({"current": dataset_payload(), "datasets": dataset_candidates()})


@app.post("/api/dataset")
def api_dataset() -> Response:
    data = request.get_json(force=True)
    try:
        load_dataset(
            data.get("path", path_label(DEFAULT_CSV_PATH)),
            url_col=data.get("url_col") or None,
            label_col=data.get("label_col") or None,
            agent_key_col=data.get("agent_key_col") or None,
            format_preset=data.get("format_preset", ""),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"row": get_row_payload(start_index), "dataset": dataset_payload()})


@app.get("/api/finetune/meta")
def api_finetune_meta() -> Response:
    return jsonify(
        {
            "json_path": path_label(FINETUNE_JSON_PATH),
            "agents": finetune_agent_options(),
            "categories": finetune_categories(),
            "descriptions": finetune_descriptions(),
        }
    )


@app.post("/api/finetune/agent")
def api_finetune_agent() -> Response:
    data = request.get_json(force=True)
    try:
        load_finetune_agent(
            data.get("agent_key", ""),
            path_value=data.get("path") or path_label(DEFAULT_FINETUNE_JSON_PATH),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"row": get_row_payload(start_index), "dataset": dataset_payload()})


@app.post("/api/tag/agent")
def api_tag_agent() -> Response:
    global CSV_TAG_AGENT_KEY, CSV_TAG_GROUP_KEY
    data = request.get_json(force=True)
    agent_key = data.get("agent_key", "").strip()
    if not agent_key:
        return jsonify({"error": "agent_key wajib diisi"}), 400

    group_key = infer_group_key(agent_key)
    if not group_key:
        return jsonify({"error": f"Tidak dapat menentukan group untuk agent key: {agent_key}"}), 400

    CSV_TAG_AGENT_KEY = agent_key
    CSV_TAG_GROUP_KEY = group_key

    # Persist to SQLite so agent key survives server restarts
    try:
        session_id = _ensure_session()
        with get_db() as conn:
            conn.execute(
                "UPDATE sessions SET tagged_agent_key=?, tagged_group_key=?, last_accessed=datetime('now') WHERE id=?",
                (agent_key, group_key, session_id),
            )
    except RuntimeError:
        pass

    return jsonify({"row": get_row_payload(start_index), "dataset": dataset_payload()})


@app.post("/api/reset")
def api_reset() -> Response:
    data = request.get_json(silent=True) or {}
    result = reset_review(delete_outputs=bool(data.get("delete_outputs")))
    return jsonify({"reset": result, "row": get_row_payload(0), "dataset": dataset_payload()})


@app.post("/api/export")
def api_export() -> Response:
    data = request.get_json(force=True)
    try:
        result = export_records(data.get("scope", "keep"), data.get("format", "json_labelling"))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"export": result, "dataset": dataset_payload()})


@app.post("/api/export/preview")
def api_export_preview() -> Response:
    data = request.get_json(force=True)
    try:
        result = preview_records(
            data.get("scope", "approved"),
            data.get("format", "json_labelling"),
            int(data.get("limit", 3)),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"export": result, "dataset": dataset_payload()})


@app.get("/api/row/<int:idx>")
def api_row(idx: int) -> Response:
    payload = get_row_payload(idx)
    peek = request.args.get("peek", "0") == "1"
    if not peek:
        save_progress(int(payload["idx"]))
    return jsonify(payload)


@app.post("/api/action")
def api_action() -> Response:
    data = request.get_json(force=True)
    action = data.get("action")
    if df.empty:
        return jsonify({"error": "Dataset kosong"}), 400

    idx = min(max(int(data.get("idx", start_index)), 0), max(len(df) - 1, 0))
    annotation = data.get("annotation") if isinstance(data.get("annotation"), dict) else None

    if action in {"approve", "keep", "reject", "delete"} and DATASET_MODE == "csv":
        agent_key = (annotation or {}).get("agent_key", "").strip()
        if not agent_key:
            return jsonify({"error": "Agent key wajib dipilih sebelum approve atau reject"}), 400

    if action in {"approve", "keep"}:
        update_annotation(
            idx,
            {
                **(annotation or {}),
                "expected": "APPROVED",
                "category": "",
                "description": "Foto LOLOS verifikasi",
            },
        )
        kept_indices.add(idx)
        deleted_indices.discard(idx)
        skipped_indices.discard(idx)
        idx += 1
    elif action in {"reject", "delete"}:
        rejected_annotation = {**(annotation or {}), "expected": "REJECTED"}
        if not safe_value(rejected_annotation.get("description", "")):
            row = df.iloc[idx]
            rejected_annotation["description"] = safe_value(row.get("description", row.get("Response Reason", "")))
        update_annotation(idx, rejected_annotation)
        deleted_indices.add(idx)
        kept_indices.discard(idx)
        skipped_indices.discard(idx)
        idx += 1
    elif action == "skip":
        skip_annotation = {**(annotation or {}), "expected": "SKIP"}
        update_annotation(idx, skip_annotation)
        skipped_indices.add(idx)
        kept_indices.discard(idx)
        deleted_indices.discard(idx)
        idx += 1
    elif action == "next":
        ann = ROW_ANNOTATIONS.get(idx, {})
        is_reviewed = idx in kept_indices or idx in deleted_indices or idx in skipped_indices or ann.get("expected")
        if not is_reviewed:
            return jsonify({"error": "Baris ini belum diulas, gunakan Skip jika ingin melewati."}), 400
        update_annotation(idx, annotation)
        idx += 1
    elif action == "back":
        update_annotation(idx, annotation)
        idx -= 1
    elif action == "save":
        update_annotation(idx, annotation)
        save_progress(idx)
        return jsonify({"finished": False, "saved": save_results(), "row": get_row_payload(idx)})
    elif action == "stop":
        update_annotation(idx, annotation)
        save_progress(idx)
        return jsonify({"finished": True, "saved": save_results(), "row": get_row_payload(idx)})
    elif action == "unskip":
        skipped_indices.discard(idx)
        if idx in ROW_ANNOTATIONS:
            annotation_entry = ROW_ANNOTATIONS[idx]
            if annotation_entry.get("expected") == "SKIP":
                annotation_entry.pop("expected", None)
        save_progress(idx)
        return jsonify({"finished": False, "row": get_row_payload(idx)})
    else:
        return jsonify({"error": f"Unknown action: {action}"}), 400

    if idx >= len(df):
        return jsonify({"finished": True, "saved": save_results(), "row": get_row_payload(len(df) - 1)})

    idx = max(idx, 0)
    save_progress(idx)
    return jsonify({"finished": False, "row": get_row_payload(idx)})


def update_annotation(idx: int, annotation: dict[str, object] | None) -> None:
    if not annotation:
        return

    current = ROW_ANNOTATIONS.get(idx, {})
    for field in ("expected", "category", "description", "agent_key", "reviewer_notes"):
        if field in annotation:
            current[field] = safe_value(annotation[field])
    ROW_ANNOTATIONS[idx] = current


@app.get("/api/scope/indices")
def api_scope_indices() -> Response:
    scope = request.args.get("scope", "all")
    no_category = request.args.get("no_category", "0") == "1"
    effective_scope = scope if scope else "reviewed"
    try:
        indices = sorted(indices_for_scope(effective_scope))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if no_category:
        indices = [idx for idx in indices if not ROW_ANNOTATIONS.get(idx, {}).get("category", "").strip()]
    return jsonify({"scope": scope, "indices": indices, "count": len(indices)})


@app.get("/api/peek/<int:idx>")
def api_peek(idx: int) -> Response:
    if df.empty or idx < 0 or idx >= len(df):
        return jsonify({"url": ""})
    return jsonify({"url": safe_value(df.iloc[idx].get(URL_COL, ""))})


@app.get("/image/<int:idx>")
def image(idx: int) -> Response:
    if idx < 0 or idx >= len(df):
        return Response("Index out of range", status=404)

    url = safe_value(df.iloc[idx].get(URL_COL, ""))
    if not url:
        return Response("Image URL is empty", status=404)

    try:
        return Response(fetch_image_bytes(url), mimetype="image/jpeg")
    except Exception as exc:
        return Response(f"Failed to load image: {exc}", status=502)


@app.post("/api/upload")
def api_upload() -> Response:
    """Accept a CSV file upload from the browser and load it as the active dataset."""
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file yang diupload"}), 400
    file = request.files["file"]
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return jsonify({"error": "Hanya file .csv yang didukung"}), 400

    upload_dir = APP_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    file.save(dest)

    url_col = request.form.get("url_col") or None
    label_col = request.form.get("label_col") or None
    try:
        load_dataset(dest, url_col=url_col, label_col=label_col)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"row": get_row_payload(start_index), "dataset": dataset_payload()})


@app.get("/api/format-presets")
def api_format_presets() -> Response:
    return jsonify({"presets": FORMAT_PRESETS})


@app.get("/api/sessions/last")
def api_sessions_last() -> Response:
    """Return the most-recently accessed session so the frontend can offer to resume it."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions ORDER BY last_accessed DESC LIMIT 1"
        ).fetchone()

    if not row:
        return jsonify({"session": None})

    return jsonify({
        "session": {
            "dataset_path": row["dataset_path"],
            "dataset_mode": row["dataset_mode"],
            "finetune_agent_key": row["finetune_agent_key"],
            "tagged_agent_key": row["tagged_agent_key"],
            "format_preset": row["format_preset"],
            "url_col": row["url_col"],
            "label_col": row["label_col"],
            "agent_key_col": row["agent_key_col"],
            "current_index": row["current_index"],
            "last_accessed": row["last_accessed"],
        }
    })


@app.post("/api/migrate/all")
def api_migrate_all() -> Response:
    """Scan legacy .progress JSON/TXT files and import them into SQLite."""
    results = _migrate_all_progress()
    return jsonify(results)


@app.get("/api/forensic/ela/<int:idx>")
def api_forensic_ela(idx: int) -> Response:
    if df.empty or idx < 0 or idx >= len(df):
        return jsonify({"error": "Index tidak valid"}), 400
    url = safe_value(df.iloc[idx].get(URL_COL, ""))
    if not url:
        return jsonify({"error": "Tidak ada URL gambar untuk baris ini"}), 400
    try:
        quality = min(max(int(request.args.get("quality", 95)), 50), 99)
        amplify = min(max(int(request.args.get("amplify", 15)), 1), 50)
        raw = fetch_raw_bytes(url)
        ela_bytes = compute_ela(raw, quality=quality, amplify=amplify)
        return Response(ela_bytes, mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/forensic/meta/<int:idx>")
def api_forensic_meta(idx: int) -> Response:
    if df.empty or idx < 0 or idx >= len(df):
        return jsonify({"error": "Index tidak valid"}), 400
    url = safe_value(df.iloc[idx].get(URL_COL, ""))
    if not url:
        return jsonify({"error": "Tidak ada URL gambar untuk baris ini"}), 400
    try:
        raw = fetch_raw_bytes(url)
        return jsonify(analyze_image_metadata(raw))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
