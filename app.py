from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from flask import Flask, Response, jsonify, render_template, request
from PIL import Image


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

CURATED_CATEGORIES: list[str] = [
    "Nopol tidak cocok",
    "Nopol editan",
    "Foto kendaraan dari layar/cetakan",
    "Foto kendaraan terindikasi edit",
    "Foto kendaraan terindikasi buatan AI",
    "Foto nopol salah sudut",
    "Jumlah roda tidak sesuai",
    "Jumlah roda tidak terlihat atau tidak bisa dikalkulasi",
    "STNK editan",
    "STNK non-asli (scan atau screenshot atau tidak berwarna)",
    "STNK terpotong",
    "STNK buram",
    "Dokumen STNK tidak lengkap",
    "Warna plat tidak sesuai dengan dokumen",
    "No rangka tidak sesuai dengan dokumen",
    "Nopol tidak sesuai dengan dokumen",
    "Jenis BBM tidak sesuai dengan dokumen",
    "Alasan penolakan foto stnk lainnya",
]

IMAGE_TIMEOUT_SECONDS = float(os.getenv("IMAGE_TIMEOUT_SECONDS", "8"))
MAX_IMAGE_SIZE = tuple(int(x) for x in os.getenv("MAX_IMAGE_SIZE", "900,700").split(",", 1))


app = Flask(__name__)

CSV_PATH = DEFAULT_CSV_PATH
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

df = pd.DataFrame()
kept_indices: set[int] = set()
deleted_indices: set[int] = set()
skipped_indices: set[int] = set()
start_index = 0


CSV_FILE_HASH = ""
FINETUNE_FILE_HASH = ""


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
    # Scan DATA_DIR and APP_DIR (and its uploads subfolder)
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


def finetune_agent_options() -> list[dict[str, object]]:
    source = FINETUNE_DATA or load_finetune_source(FINETUNE_JSON_PATH)
    options = []
    for group_key, agents in source.items():
        for agent_key, records in agents.items():
            options.append({"group": group_key, "agent_key": agent_key, "count": len(records)})
    return options


def finetune_categories(agent_key: str | None = None) -> list[str]:
    source = FINETUNE_DATA or load_finetune_source(FINETUNE_JSON_PATH)
    categories = set()
    for agents in source.values():
        for current_agent_key, records in agents.items():
            if agent_key and current_agent_key != agent_key:
                continue
            for record in records:
                value = str(record.get("category") or "").strip()
                if value:
                    categories.add(value)
    return sorted(categories)


def finetune_descriptions(agent_key: str | None = None, category: str | None = None) -> list[str]:
    source = FINETUNE_DATA or load_finetune_source(FINETUNE_JSON_PATH)
    descriptions = set()
    for agents in source.values():
        for current_agent_key, records in agents.items():
            if agent_key and current_agent_key != agent_key:
                continue
            for record in records:
                if category is not None and str(record.get("category") or "").strip() != category:
                    continue
                value = str(record.get("description") or "").strip()
                if value:
                    descriptions.add(value)
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


def _migrate_progress(new_path: Path, old_path: Path) -> None:
    """Copy old-style progress file to new hash-based path if new one doesn't exist yet."""
    if not new_path.exists() and old_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(old_path.read_bytes())


def load_dataset(path_value: str | Path, url_col: str | None = None, label_col: str | None = None, agent_key_col: str | None = None, format_preset: str = "") -> None:
    global CSV_PATH, OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP, PROGRESS_FILE, URL_COL, LABEL_COL, RUN_ID, DATASET_MODE, DATASET_FORMAT_PRESET
    global df, kept_indices, deleted_indices, skipped_indices, start_index, ROW_ANNOTATIONS
    global CSV_TAG_AGENT_KEY, CSV_TAG_GROUP_KEY, CSV_FILE_HASH
    DATASET_FORMAT_PRESET = format_preset

    CSV_TAG_AGENT_KEY = ""
    CSV_TAG_GROUP_KEY = ""

    csv_path = resolve_path(str(path_value))
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV tidak ditemukan: {path_label(csv_path)}")
    if csv_path.suffix.lower() != ".csv":
        raise ValueError("Dataset harus berupa file .csv")

    CSV_FILE_HASH = file_content_hash(csv_path)
    loaded_df = pd.read_csv(csv_path)
    columns = [str(column) for column in loaded_df.columns]

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
    _migrate_progress(
        PROGRESS_FILE,
        APP_DIR / ".progress" / f"{safe_filename(path_label(csv_path))}.txt",
    )
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
    global df, kept_indices, deleted_indices, skipped_indices, start_index, ROW_ANNOTATIONS, FINETUNE_FILE_HASH

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

    FINETUNE_JSON_PATH = json_path
    FINETUNE_DATA = source
    FINETUNE_GROUP_KEY = selected_group
    FINETUNE_AGENT_KEY = agent_key
    CSV_PATH = json_path
    PROGRESS_FILE = finetune_progress_path_for(json_path, agent_key)
    _migrate_progress(
        PROGRESS_FILE,
        APP_DIR / ".progress" / f"{safe_filename(path_label(json_path))}__{safe_filename(agent_key)}.txt",
    )
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


def load_progress() -> None:
    global start_index, RUN_ID, ROW_ANNOTATIONS

    # Try JSON path; fall back to legacy .txt path for migration
    path = PROGRESS_FILE
    if not path.exists():
        legacy = path.with_suffix(".txt")
        if legacy.exists():
            path = legacy
        else:
            return

    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _parse_progress_txt(raw)
        # Auto-migrate: save as JSON now that we've parsed it
        _write_progress_json(PROGRESS_FILE, data)

    if "current_index" in data:
        start_index = min(max(int(data["current_index"]), 0), max(len(df) - 1, 0))
    if "run_id" in data:
        RUN_ID = safe_filename(str(data["run_id"]))
    kept_indices.update(int(x) for x in data.get("approved", []))
    deleted_indices.update(int(x) for x in data.get("rejected", []))
    skipped_indices.update(int(x) for x in data.get("skipped", []))
    raw_annotations = data.get("annotations", {})
    ROW_ANNOTATIONS = {
        int(idx): {str(k): str(v) for k, v in ann.items()}
        for idx, ann in raw_annotations.items()
        if isinstance(ann, dict)
    }

    valid = range(len(df))
    kept_indices.intersection_update(valid)
    deleted_indices.intersection_update(valid)
    skipped_indices.intersection_update(valid)
    ROW_ANNOTATIONS = {idx: value for idx, value in ROW_ANNOTATIONS.items() if idx in valid}


def _write_progress_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def save_progress(current_idx: int) -> None:
    global start_index
    start_index = current_idx
    data = {
        "current_index": current_idx,
        "run_id": RUN_ID,
        "approved": sorted(kept_indices),
        "rejected": sorted(deleted_indices),
        "skipped": sorted(skipped_indices),
        "annotations": {str(k): v for k, v in sorted(ROW_ANNOTATIONS.items())},
    }
    _write_progress_json(PROGRESS_FILE, data)


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
                
                # Determine agent key for this specific row
                row_agent = ""
                annotation = ROW_ANNOTATIONS.get(idx, {})
                if annotation.get("agent_key"):
                    row_agent = str(annotation["agent_key"]).strip()
                elif "agent_key" in df.columns:
                    row_agent = str(df.iloc[idx].get("agent_key", "")).strip()
                
                if row_agent and (pd.isna(row_agent) or row_agent == "nan"):
                    row_agent = ""

                if row_agent:
                    row_group = infer_group_key(row_agent)
                    if row_group:
                        output.setdefault(row_group, {}).setdefault(row_agent, []).append(record)
                    else:
                        flat_records.append(record)
                else:
                    flat_records.append(record)

            # If nothing was grouped into agents, output as flat list
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
        "category": annotation["category"],
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


def category_to_code(category: str) -> str:
    return CATEGORY_TO_REJECTION_CODE.get(category.strip(), "")


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
            "mapped_rejection_code": category_to_code(category),
            "mapped_rejection_category": category,
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
    category = annotation["category"]
    derived_code = category_to_code(category)

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
        "mapped_rejection_code": derived_code or safe_value(row.get("mapped_rejection_code", "")),
        "mapped_rejection_category": category or safe_value(row.get("mapped_rejection_category", "")),
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

    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()

    deleted_files = []
    if delete_outputs:
        for path in (OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP):
            if path.exists():
                path.unlink()
                deleted_files.append(path_label(path))

    RUN_ID = new_run_id()
    if DATASET_MODE == "finetune_json":
        OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = finetune_output_paths_for(CSV_PATH, FINETUNE_AGENT_KEY, RUN_ID)
    else:
        OUTPUT_KEEP, OUTPUT_DELETED, OUTPUT_RAW, OUTPUT_SKIP = output_paths_for(CSV_PATH, RUN_ID)

    return {
        "run_id": RUN_ID,
        "progress_file": path_label(PROGRESS_FILE),
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


def dataset_payload() -> dict[str, object]:
    return {
        "mode": DATASET_MODE,
        "format_preset": DATASET_FORMAT_PRESET,
        "path": path_label(CSV_PATH),
        "columns": list(df.columns),
        "url_col": URL_COL,
        "label_col": LABEL_COL,
        "run_id": RUN_ID,
        "finetune": {
            "json_path": path_label(FINETUNE_JSON_PATH),
            "group_key": FINETUNE_GROUP_KEY,
            "agent_key": FINETUNE_AGENT_KEY,
            "csv_tag_agent_key": CSV_TAG_AGENT_KEY,
            "csv_tag_group_key": CSV_TAG_GROUP_KEY,
            "agents": finetune_agent_options() if DEFAULT_FINETUNE_JSON_PATH.exists() else [],
            "categories": finetune_categories(FINETUNE_AGENT_KEY) if DATASET_MODE == "finetune_json" else [],
            "all_categories": finetune_categories() if DEFAULT_FINETUNE_JSON_PATH.exists() else [],
            "descriptions": finetune_descriptions(FINETUNE_AGENT_KEY) if DATASET_MODE == "finetune_json" else [],
        },
        "progress_file": path_label(PROGRESS_FILE),
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
            "csv_path": str(CSV_PATH),
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
    try:
        global FINETUNE_DATA
        if not FINETUNE_DATA:
            FINETUNE_DATA = load_finetune_source(FINETUNE_JSON_PATH)
        return jsonify(
            {
                "json_path": path_label(FINETUNE_JSON_PATH),
                "agents": finetune_agent_options(),
                "categories": finetune_categories(),
                "descriptions": finetune_descriptions(),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


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
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
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


try:
    load_dataset(DEFAULT_CSV_PATH, DEFAULT_URL_COL, DEFAULT_LABEL_COL)
except FileNotFoundError:
    pass  # Start with empty state — user will pick a file via UI


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)
