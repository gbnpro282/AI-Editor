import sys
import os
import hashlib
import fnmatch
import json
import random
import re
import shutil
import sqlite3
import time
import subprocess
import base64
import queue
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from contextlib import contextmanager
import requests
import numpy as np
import cv2
import librosa
from datetime import datetime, timedelta

# ==============================================================================
# AUTOMATIC ENVIRONMENT SETUP & PATH INJECTION
# ==============================================================================
# Otomatis mendeteksi dan mendaftarkan jalur ffmpeg.exe dari AppData jika belum ada di PATH
appdata_local = os.environ.get("LOCALAPPDATA", "")
if appdata_local and os.path.exists(appdata_local):
    for root, dirs, files in os.walk(appdata_local):
        if "ffmpeg.exe" in files:
            os.environ["PATH"] += f";{root}"
            break

# PyQt5 GUI Framework
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QTextEdit,
    QProgressBar, QDoubleSpinBox, QSlider, QGroupBox, QFrame,
    QMessageBox, QComboBox, QCheckBox, QStackedWidget, QButtonGroup,
    QScrollArea,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget

# PySceneDetect Engine
from scenedetect import detect, ContentDetector

OLLAMA_API_URL = "http://localhost:11434/api/generate"
DEFAULT_VISION_MODEL = "qwen2.5vl:3b"


def format_elapsed_time(seconds):
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def check_render_quality(
    video_path, expected_duration=None, timeout=600, allow_silent_audio=False
):
    """Check media decoding, streams, visual defects, duration, and frame timing."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-i",
        video_path,
        "-vf",
        "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-50dB:d=0.75",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "render_path": video_path,
            "checked_at": checked_at,
            "status": "error",
            "findings": [
                {
                    "severity": "error",
                    "code": "quality_check_failed",
                    "message": f"Automatic media check could not run: {error}",
                }
            ],
            "metrics": {},
        }

    output = result.stderr or ""
    findings = []
    black_segments = []
    freeze_segments = []
    current_freeze_start = None

    for line in output.splitlines():
        black_match = re.search(
            r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+"
            r"black_duration:\s*([\d.]+)",
            line,
        )
        if black_match:
            start, end, duration = map(float, black_match.groups())
            segment = {"start": start, "end": end, "duration": duration}
            black_segments.append(segment)
            findings.append(
                {
                    "severity": "warning",
                    "code": "long_black_interval",
                    "message": (
                        f"Long near-black interval ({duration:.2f}s); "
                        "review this scene for an unintended blank frame."
                    ),
                    **segment,
                }
            )

        freeze_start_match = re.search(
            r"freeze_start:\s*([\d.]+)", line
        )
        if freeze_start_match:
            current_freeze_start = float(freeze_start_match.group(1))

        freeze_duration_match = re.search(
            r"freeze_duration:\s*([\d.]+)", line
        )
        if freeze_duration_match and current_freeze_start is not None:
            duration = float(freeze_duration_match.group(1))
            segment = {
                "start": current_freeze_start,
                "end": current_freeze_start + duration,
                "duration": duration,
            }
            freeze_segments.append(segment)
            findings.append(
                {
                    "severity": "warning",
                    "code": "long_frozen_interval",
                    "message": (
                        f"Potentially frozen interval ({duration:.2f}s); "
                        "review to distinguish a glitch from an intentional still shot."
                    ),
                    **segment,
                }
            )
            current_freeze_start = None

    video_stream = re.search(r"Stream #0:\d+.*Video:", output, re.IGNORECASE)
    audio_stream = re.search(r"Stream #0:\d+.*Audio:", output, re.IGNORECASE)
    if not video_stream:
        findings.append(
            {
                "severity": "error",
                "code": "missing_video_stream",
                "message": "The rendered file has no readable video stream.",
            }
        )
    if not audio_stream:
        findings.append(
            {
                "severity": "warning",
                "code": "missing_audio_stream",
                "message": "The rendered file has no readable audio stream.",
            }
        )

    decode_error_lines = [
        line.strip()
        for line in output.splitlines()
        if re.search(
            r"(error while decoding|corrupt decoded|invalid data found|failed to decode)",
            line,
            re.IGNORECASE,
        )
    ]
    if result.returncode != 0 or decode_error_lines:
        findings.append(
            {
                "severity": "error",
                "code": "media_decode_errors",
                "message": (
                    "FFmpeg reported errors while reading the rendered file: "
                    + ("; ".join(decode_error_lines[:5]) if decode_error_lines else output[-1200:])
                ),
            }
        )

    frame_timing_metrics = {}
    try:
        timing_result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if timing_result.returncode != 0:
            raise RuntimeError(timing_result.stderr[-1200:] or "ffprobe failed")

        frame_times = []
        for line in timing_result.stdout.splitlines():
            value = line.strip().split(",", 1)[0]
            try:
                frame_times.append(float(value))
            except ValueError:
                continue
        if not frame_times:
            raise ValueError("No readable video frame timestamps were found.")

        positive_deltas = [
            current - previous
            for previous, current in zip(frame_times, frame_times[1:])
            if current > previous
        ]
        typical_frame_interval = (
            float(np.median(positive_deltas)) if positive_deltas else 0.0
        )
        gap_threshold = max(0.1, typical_frame_interval * 3)
        timing_anomalies = []
        for previous, current in zip(frame_times, frame_times[1:]):
            interval = current - previous
            if interval <= 0:
                kind = "non-monotonic"
            elif interval > gap_threshold:
                kind = "gap"
            elif typical_frame_interval and interval < typical_frame_interval / 3:
                kind = "compressed"
            else:
                continue
            if kind is not None:
                timing_anomalies.append(
                    {
                        "start": previous,
                        "end": current,
                        "duration": interval,
                        "kind": kind,
                    }
                )

        frame_timing_metrics = {
            "frame_count": len(frame_times),
            "typical_frame_interval_seconds": typical_frame_interval,
            "frame_timing_anomaly_count": len(timing_anomalies),
        }
        for anomaly in timing_anomalies[:20]:
            findings.append(
                {
                    "severity": "warning",
                    "code": "irregular_frame_timing",
                    "message": (
                        "Possible playback lag/buffering or a sudden speed change "
                        f"near {anomaly['start']:.2f}s "
                        f"({anomaly['kind']} frame timing)."
                    ),
                    **anomaly,
                }
            )
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        findings.append(
            {
                "severity": "error",
                "code": "frame_timing_check_failed",
                "message": f"Frame timing could not be verified: {error}",
            }
        )

    volume_match = re.search(r"mean_volume:\s*([-\w.]+)\s*dB", output)
    duration_match = re.search(
        r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", output
    )
    duration_seconds = None
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration_seconds = (
            int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        )
        if expected_duration is not None:
            tolerance = max(0.5, float(expected_duration) * 0.02)
            if abs(duration_seconds - float(expected_duration)) > tolerance:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "unexpected_render_duration",
                        "message": (
                            f"Rendered duration is {duration_seconds:.2f}s; "
                            f"expected about {float(expected_duration):.2f}s."
                        ),
                    }
                )

    if current_freeze_start is not None and duration_seconds is not None:
        trailing_freeze_duration = max(0.0, duration_seconds - current_freeze_start)
        if trailing_freeze_duration >= 0.75:
            segment = {
                "start": current_freeze_start,
                "end": duration_seconds,
                "duration": trailing_freeze_duration,
            }
            freeze_segments.append(segment)
            findings.append(
                {
                    "severity": "warning",
                    "code": "long_frozen_interval",
                    "message": (
                        f"Potentially frozen interval ({trailing_freeze_duration:.2f}s); "
                        "review to distinguish a glitch from an intentional still shot."
                    ),
                    **segment,
                }
            )

    mean_volume_db = volume_match.group(1) if volume_match else None
    if mean_volume_db is not None:
        try:
            nearly_silent_audio = float(mean_volume_db) <= -60.0
        except ValueError:
            nearly_silent_audio = mean_volume_db.casefold() == "-inf"
        if nearly_silent_audio and not allow_silent_audio:
            findings.append(
                {
                    "severity": "warning",
                    "code": "nearly_silent_audio",
                    "message": (
                        "The audio track is nearly silent; check soundtrack "
                        "mapping and intended output volume."
                    ),
                }
            )
    metrics = {
        "ffmpeg_return_code": result.returncode,
        "has_video_stream": bool(video_stream),
        "has_audio_stream": bool(audio_stream),
        "duration_seconds": duration_seconds,
        "expected_duration_seconds": expected_duration,
        "black_segments": black_segments,
        "frozen_segments": freeze_segments,
        "mean_audio_volume_db": mean_volume_db,
        **frame_timing_metrics,
    }
    return {
        "render_path": video_path,
        "checked_at": checked_at,
        "status": (
            "error"
            if any(item["severity"] == "error" for item in findings)
            else "warning" if findings else "ok"
        ),
        "findings": findings,
        "metrics": metrics,
    }


def video_has_audio_stream(video_path):
    """Return whether a source video contains an audio stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            video_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Gagal memeriksa audio video bahan: {result.stderr[-1200:]}"
        )
    return bool(result.stdout.strip())


def replace_problem_scenes(selected_cuts, ranked_scenes, findings, excluded_scenes):
    """Replace source shots that overlap QC findings with unused sampled alternatives."""
    affected_indices = set()
    timeline_position = 0.0
    for index, cut in enumerate(selected_cuts):
        cut_start = timeline_position
        cut_end = cut_start + float(cut["duration"])
        timeline_position = cut_end
        for finding in findings:
            start = finding.get("start")
            end = finding.get("end")
            if start is None or end is None:
                affected_indices.update(range(len(selected_cuts)))
                break
            start = float(start)
            end = max(start, float(end))
            if (
                (start < cut_end and end >= cut_start)
                or (start == end and cut_start <= start < cut_end)
            ):
                affected_indices.add(index)

    if not affected_indices:
        return None

    def scene_id(scene):
        return (
            os.path.normcase(os.path.abspath(scene["file"])),
            round(float(scene["start"]), 3),
        )

    excluded_scenes.update(scene_id(cut) for cut in selected_cuts)
    used_scenes = {
        scene_id(cut)
        for index, cut in enumerate(selected_cuts)
        if index not in affected_indices
    }
    replacements = [dict(cut) for cut in selected_cuts]

    for index in sorted(affected_indices):
        cut = selected_cuts[index]
        replacement = None
        for candidate in ranked_scenes:
            candidate_id = scene_id(candidate)
            if candidate_id in excluded_scenes or candidate_id in used_scenes:
                continue
            if float(candidate["source_duration"]) < float(cut["duration"]):
                continue
            replacement = dict(cut)
            replacement.update(
                {
                    "file": candidate["file"],
                    "start": min(
                        float(candidate["start"]),
                        max(
                            0.0,
                            float(candidate["source_duration"])
                            - float(cut["duration"]),
                        ),
                    ),
                    "source_duration": candidate["source_duration"],
                    "ai_predicted_score": candidate["score"],
                    "perceptual_hash": candidate["perceptual_hash"],
                }
            )
            used_scenes.add(candidate_id)
            excluded_scenes.add(candidate_id)
            break
        if replacement is None:
            return None
        replacements[index] = replacement

    return replacements


def build_temporal_sample_starts(
    source_duration, segment_duration, sample_count, random_generator=None
):
    """Return randomized, chronological sample starts spanning a source video."""
    source_duration = max(0.0, float(source_duration))
    segment_duration = max(0.0, float(segment_duration))
    sample_count = max(1, int(sample_count))
    max_start = max(0.0, source_duration - min(segment_duration, source_duration))
    if max_start == 0.0:
        return [0.0]

    generator = random_generator or random.Random()
    if sample_count == 1:
        return [max_start * generator.random()]

    starts = [0.0]
    starts.extend(
        max_start * (index + generator.random()) / (sample_count - 1)
        for index in range(1, sample_count - 1)
    )
    starts.append(max_start)
    return sorted(set(starts))


# ==============================================================================
# ENGINE 1: LOCAL OLLAMA VISION AI ANALYST ENGINE
# ==============================================================================
class OllamaAIAnalyst:
    """Mengirim sampel frame video ke Ollama Qwen2.5-VL 3B untuk analisis estetika & sinematografi."""

    _session = requests.Session()

    @staticmethod
    def get_installed_models():
        """Mengecek daftar model terinstal di server Ollama lokal."""
        try:
            res = requests.get("http://localhost:11434/api/tags", timeout=3)
            if res.status_code == 200:
                models = [m["name"] for m in res.json().get("models", [])]
                if DEFAULT_VISION_MODEL in models:
                    models.remove(DEFAULT_VISION_MODEL)
                    models.insert(0, DEFAULT_VISION_MODEL)
                return models if models else [DEFAULT_VISION_MODEL]
        except Exception as e:
            print(f"[Ollama Check Warning] {e}")
        return [DEFAULT_VISION_MODEL]

    @staticmethod
    def score_video_clip(model_name, image_path, prompt_criteria="sinematik, pencahayaan bagus, aksi tajam"):
        """Mengirim frame dalam format Base64 ke Qwen2.5-VL 3B dan mengembalikan skor estetika (1-10)."""
        return OllamaAIAnalyst._score_video_clip(
            model_name, image_path, prompt_criteria, ""
        )

    @staticmethod
    def score_video_clip_with_memory(
        model_name, image_path, memory_engine, prompt_criteria="sinematik, pencahayaan bagus, aksi tajam",
        prompt_override="", image_bytes=None, examples=None, memory_profile=None
    ):
        if examples is None:
            examples = memory_engine.get_few_shot_examples(limit=5)
        if memory_profile is None:
            memory_profile = memory_engine.get_preference_profile()
        memory_context = memory_profile
        if examples:
            example_lines = []
            for example in examples:
                feedback = example["user_feedback"] or "Tanpa komentar."
                previous_prompt = example["external_prompt"]
                prompt_note = (
                    f" Prompt edit: {previous_prompt[:300]}."
                    if previous_prompt
                    else ""
                )
                example_lines.append(
                    f"- Rating pengguna {example['user_rating']}/5; "
                    f"skor AI sebelumnya {example['ai_predicted_score']}/10; "
                    f"komentar: {feedback[:300]}.{prompt_note}"
                )
            example_context = (
                "Contoh evaluasi scene sebelumnya:\n"
                + "\n".join(example_lines)
                + "\nGunakan contoh ini untuk memahami preferensi pengguna."
            )
            memory_context = (
                memory_context + "\n" + example_context
                if memory_context
                else example_context
            )
        if prompt_override.strip():
            memory_context += (
                "\nInstruksi edit SAAT INI dari pengguna (prioritaskan instruksi ini "
                "saat menilai kesesuaian visual): " + prompt_override.strip()
            )
            prompt_criteria += (
                "\nPrioritaskan kesesuaian adegan dengan prompt edit pengguna: "
                + prompt_override.strip()
            )
        return OllamaAIAnalyst._score_video_clip(
            model_name, image_path, prompt_criteria, memory_context, image_bytes
        )

    @staticmethod
    def _score_video_clip(
        model_name, image_path, prompt_criteria, system_context, image_bytes=None
    ):
        try:
            if image_bytes is None:
                with open(image_path, "rb") as f:
                    image_bytes = f.read()
            img_b64 = base64.b64encode(image_bytes).decode('utf-8')

            prompt = (
                f"Analisis foto ini berdasarkan kriteria: {prompt_criteria}.\n"
                "Berikan penilaian estetika dan kejelasan aksi dari skala 1 sampai 10.\n"
                "Jawab HANYA angka skor bulatnya saja tanpa teks tambahan (contoh: 8)."
            )

            payload = {
                "model": model_name,
                "system": system_context,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": 0.2,
                    "num_predict": 10
                }
            }
            
            res = OllamaAIAnalyst._session.post(
                OLLAMA_API_URL, json=payload, timeout=12
            )
            if res.status_code == 200:
                response_text = res.json().get("response", "7").strip()
                digits = [int(s) for s in response_text.split() if s.isdigit()]
                if digits:
                    score = digits[0]
                    return max(1, min(10, score))
        except Exception as e:
            print(f"[Ollama Vision Scoring Error] {e}")
        return 7


class MemoryLearningEngine:
    """Stores compact scene feedback and perceptual hashes in a local SQLite database."""

    def __init__(self, storage_dir="Memory Learning"):
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)
        self.database_path = os.path.join(self.storage_dir, "memory_learning.db")
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scene_evaluations (
                    scene_id TEXT PRIMARY KEY,
                    source_file TEXT NOT NULL,
                    clip_duration REAL NOT NULL,
                    ai_predicted_score INTEGER NOT NULL,
                    user_rating INTEGER NOT NULL CHECK(user_rating BETWEEN 1 AND 5),
                    user_feedback TEXT NOT NULL DEFAULT '',
                    external_prompt TEXT NOT NULL DEFAULT '',
                    perceptual_hash TEXT NOT NULL DEFAULT '',
                    timestamp_start REAL NOT NULL,
                    timestamp_end REAL NOT NULL,
                    evaluated_at TEXT NOT NULL
                )
                """
            )
            evaluation_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(scene_evaluations)"
                ).fetchall()
            }
            if "external_prompt" not in evaluation_columns:
                connection.execute(
                    """
                    ALTER TABLE scene_evaluations
                    ADD COLUMN external_prompt TEXT NOT NULL DEFAULT ''
                    """
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS render_quality_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    render_path TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    finding_count INTEGER NOT NULL,
                    report_json TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def perceptual_hash(image_source):
        if isinstance(image_source, bytes):
            image = cv2.imdecode(
                np.frombuffer(image_source, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
            )
            source_description = "in-memory frame"
        else:
            image = cv2.imread(image_source, cv2.IMREAD_GRAYSCALE)
            source_description = image_source
        if image is None:
            raise ValueError(f"Unable to read scene frame: {source_description}")
        resized = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
        bits = resized[:, 1:] > resized[:, :-1]
        value = 0
        for bit in bits.flatten():
            value = (value << 1) | int(bit)
        return f"{value:016x}"

    def save_evaluation(self, scene_metadata):
        required = (
            "scene_id", "source_file", "clip_duration", "ai_predicted_score",
            "user_rating", "timestamp_range"
        )
        missing = [key for key in required if key not in scene_metadata]
        if missing:
            raise ValueError("Missing scene metadata: " + ", ".join(missing))

        timestamp_range = scene_metadata["timestamp_range"]
        if len(timestamp_range) != 2:
            raise ValueError("timestamp_range must contain start and end seconds")
        rating = int(scene_metadata["user_rating"])
        if rating < 1 or rating > 5:
            raise ValueError("user_rating must be between 1 and 5")

        evaluated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        record = (
            scene_metadata["scene_id"],
            scene_metadata["source_file"],
            float(scene_metadata["clip_duration"]),
            int(scene_metadata["ai_predicted_score"]),
            rating,
            scene_metadata.get("user_feedback", ""),
            scene_metadata.get("external_prompt", ""),
            scene_metadata.get("perceptual_hash", ""),
            float(timestamp_range[0]),
            float(timestamp_range[1]),
            evaluated_at,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scene_evaluations (
                    scene_id, source_file, clip_duration, ai_predicted_score,
                    user_rating, user_feedback, external_prompt, perceptual_hash,
                    timestamp_start, timestamp_end, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    source_file=excluded.source_file,
                    clip_duration=excluded.clip_duration,
                    ai_predicted_score=excluded.ai_predicted_score,
                    user_rating=excluded.user_rating,
                    user_feedback=excluded.user_feedback,
                    external_prompt=excluded.external_prompt,
                    perceptual_hash=excluded.perceptual_hash,
                    timestamp_start=excluded.timestamp_start,
                    timestamp_end=excluded.timestamp_end,
                    evaluated_at=excluded.evaluated_at
                """,
                record,
            )

    def has_evaluations(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM scene_evaluations)"
            ).fetchone()
        return bool(row[0])

    def get_few_shot_examples(self, limit=5):
        if limit <= 0:
            return []
        positive_count = max(1, (limit + 1) // 2)
        negative_count = max(0, limit - positive_count)
        with self._connect() as connection:
            positives = connection.execute(
                """
                SELECT user_rating, ai_predicted_score, user_feedback, external_prompt
                FROM scene_evaluations WHERE user_rating >= 4
                ORDER BY user_rating DESC, evaluated_at DESC LIMIT ?
                """,
                (positive_count,),
            ).fetchall()
            negatives = connection.execute(
                """
                SELECT user_rating, ai_predicted_score, user_feedback, external_prompt
                FROM scene_evaluations WHERE user_rating <= 2
                ORDER BY user_rating ASC, evaluated_at DESC LIMIT ?
                """,
                (negative_count,),
            ).fetchall()
        examples = [
            {
                "user_rating": row[0],
                "ai_predicted_score": row[1],
                "user_feedback": row[2],
                "external_prompt": row[3],
            }
            for row in positives + negatives
        ]
        return examples[:limit]

    def get_preference_profile(self, limit=200):
        stop_words = {
            "yang", "dan", "atau", "dengan", "untuk", "dari", "pada", "ini",
            "itu", "saya", "aku", "banget", "terlalu", "lebih", "kurang",
            "bikin", "buat", "video", "scene", "shot", "klip", "the", "and",
            "for", "with", "this", "that", "very", "too", "more", "less",
            "good", "bad", "nice", "looks", "look", "like", "want", "please",
        }
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*), AVG(user_rating)
                FROM scene_evaluations
                """
            ).fetchone()
            comments = connection.execute(
                """
                SELECT user_rating, user_feedback
                FROM scene_evaluations
                WHERE TRIM(user_feedback) != ''
                ORDER BY evaluated_at DESC
                LIMIT ?
                """,
                (max(0, int(limit)),),
            ).fetchall()

        evaluation_count = int(totals[0] or 0)
        if not evaluation_count:
            return ""

        positive_terms = Counter()
        negative_terms = Counter()
        for rating, feedback in comments:
            words = {
                word for word in re.findall(r"[^\W_]{3,}", feedback.casefold())
                if word not in stop_words and not word.isdigit()
            }
            if rating >= 4:
                positive_terms.update(words)
            elif rating <= 2:
                negative_terms.update(words)

        profile = (
            f"Ringkasan dari {evaluation_count} rating pengguna sebelumnya "
            f"(rata-rata {float(totals[1]):.1f}/5)."
        )
        liked = [word for word, _ in positive_terms.most_common(6)]
        avoided = [word for word, _ in negative_terms.most_common(6)]
        if liked:
            profile += " Ciri yang cenderung disukai berdasarkan komentar: " + ", ".join(liked) + "."
        if avoided:
            profile += " Ciri yang perlu dihindari berdasarkan komentar: " + ", ".join(avoided) + "."
        return profile

    def save_quality_check(self, report):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO render_quality_checks (
                    render_path, checked_at, status, finding_count, report_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report["render_path"],
                    report["checked_at"],
                    report["status"],
                    len(report["findings"]),
                    json.dumps(report, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def get_latest_quality_check(self):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT report_json FROM render_quality_checks
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return json.loads(row[0]) if row else None

    def get_memory_evaluations(self):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT perceptual_hash, user_rating
                FROM scene_evaluations
                WHERE perceptual_hash != ''
                ORDER BY evaluated_at DESC
                """
            ).fetchall()

    def adjust_score_by_memory(self, perceptual_hash, ai_score, evaluations=None):
        if not perceptual_hash:
            return ai_score
        candidate = int(perceptual_hash, 16)
        rows = (
            self.get_memory_evaluations()
            if evaluations is None
            else evaluations
        )

        best_match = None
        for stored_hash, user_rating in rows:
            try:
                distance = bin(candidate ^ int(stored_hash, 16)).count("1")
            except ValueError:
                continue
            similarity = 1.0 - distance / 64.0
            if similarity >= 0.75 and (
                best_match is None or similarity > best_match[0]
            ):
                best_match = (similarity, user_rating)
        if best_match is None:
            return ai_score

        similarity, user_rating = best_match
        preferred_score = user_rating * 2
        adjusted = round(ai_score + (preferred_score - ai_score) * similarity * 0.5)
        return max(1, min(10, adjusted))


class ClipPauseVideoPlayer(QWidget):
    scene_pause_reached = pyqtSignal(int, float)
    video_finished = pyqtSignal()
    playback_error = pyqtSignal(str)
    replay_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self.video_widget = QVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.setVolume(100)
        self.player.setMuted(False)
        self.boundaries = []
        self.next_boundary = 0
        self.finished_emitted = False
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.error.connect(self._on_player_error)

        layout = QVBoxLayout(self)
        top_controls = QHBoxLayout()
        top_controls.addStretch()
        self.replay_button = QPushButton("🔁 Replay")
        self.replay_button.clicked.connect(self.replay_requested.emit)
        self.skip_button = QPushButton("⏭ Skip")
        self.skip_button.clicked.connect(self.skip_to_completion)
        top_controls.addWidget(self.replay_button)
        top_controls.addWidget(self.skip_button)
        layout.addLayout(top_controls)
        layout.addWidget(self.video_widget, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Play")
        self.play_button.clicked.connect(self.player.play)
        self.pause_button = QPushButton("⏸ Pause")
        self.pause_button.clicked.connect(self.player.pause)
        controls.addWidget(self.play_button)
        controls.addWidget(self.pause_button)
        controls.addStretch()
        layout.addLayout(controls)

    def load_video(self, video_path, scene_metadata):
        self.player.stop()
        self.boundaries = [
            {
                "scene_index": index,
                "timestamp": float(scene["timestamp_range"][1]),
            }
            for index, scene in enumerate(scene_metadata)
        ]
        self.next_boundary = 0
        self.finished_emitted = False
        self.player.setMedia(
            QMediaContent(QUrl.fromLocalFile(os.path.abspath(video_path)))
        )
        self.player.setVolume(100)
        self.player.setMuted(False)
        self.player.play()

    def _on_position_changed(self, position_ms):
        if self.next_boundary >= len(self.boundaries):
            return
        boundary = self.boundaries[self.next_boundary]
        if position_ms >= int(boundary["timestamp"] * 1000):
            self.player.pause()
            self.next_boundary += 1
            self.scene_pause_reached.emit(
                boundary["scene_index"], boundary["timestamp"]
            )

    def _on_media_status_changed(self, status):
        if status != QMediaPlayer.EndOfMedia or self.finished_emitted:
            return
        if self.next_boundary < len(self.boundaries):
            boundary = self.boundaries[self.next_boundary]
            self.next_boundary += 1
            self.scene_pause_reached.emit(
                boundary["scene_index"], boundary["timestamp"]
            )
            return
        self._emit_video_finished()

    def resume_after_review(self):
        if self.next_boundary >= len(self.boundaries):
            self._emit_video_finished()
        else:
            self.player.play()

    def replay(self):
        if not self.player.currentMedia().isNull():
            self.player.stop()
            self.next_boundary = 0
            self.finished_emitted = False
            self.player.setPosition(0)
            self.player.play()

    def skip_to_completion(self):
        self.player.pause()
        self._emit_video_finished()

    def _on_player_error(self, error):
        if error != QMediaPlayer.NoError:
            self.playback_error.emit(self.player.errorString())

    def _emit_video_finished(self):
        if not self.finished_emitted:
            self.finished_emitted = True
            self.video_finished.emit()


class SceneRatingPanel(QGroupBox):
    rating_submitted = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__("Scene review", parent)
        self.scene_id = ""
        self.scene_index = -1
        self.rating = 0
        layout = QVBoxLayout(self)
        self.scene_label = QLabel("Rate the highlighted scene")
        layout.addWidget(self.scene_label)

        star_layout = QHBoxLayout()
        self.star_group = QButtonGroup(self)
        self.star_group.setExclusive(True)
        self.star_buttons = []
        for rating in range(1, 6):
            button = QPushButton("★")
            button.setCheckable(True)
            button.setFixedWidth(44)
            button.setStyleSheet("font-size: 24px; background: transparent;")
            button.toggled.connect(
                lambda checked, value=rating: self._set_rating(value, checked)
            )
            self.star_group.addButton(button, rating)
            self.star_buttons.append(button)
            star_layout.addWidget(button)
        self._update_star_colors()
        star_layout.addStretch()
        layout.addLayout(star_layout)

        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText(
            "Optional feedback (e.g. lighting too dark, cut too fast)"
        )
        layout.addWidget(self.comment_input)
        self.confirm_button = QPushButton("Confirm rating / Next scene")
        self.confirm_button.clicked.connect(self._submit_rating)
        layout.addWidget(self.confirm_button)

    def set_scene(self, scene_index, scene_metadata):
        self.scene_index = scene_index
        self.scene_id = scene_metadata["scene_id"]
        self.rating = 0
        self.star_group.setExclusive(False)
        for button in self.star_buttons:
            button.setChecked(False)
        self.star_group.setExclusive(True)
        self._update_star_colors()
        self.comment_input.clear()
        self.scene_label.setText(
            f"Scene {scene_index + 1} — {os.path.basename(scene_metadata['source_file'])} "
            f"({scene_metadata['timestamp_range'][0]:.2f}s–"
            f"{scene_metadata['timestamp_range'][1]:.2f}s)"
        )

    def _set_rating(self, rating, checked):
        if checked:
            self.rating = rating
            self._update_star_colors()

    def _update_star_colors(self):
        for index, button in enumerate(self.star_buttons, start=1):
            color = "#FFC107" if index <= self.rating else "#808080"
            button.setStyleSheet(
                f"font-size: 24px; color: {color}; background: transparent;"
            )

    def _submit_rating(self):
        if not self.rating:
            QMessageBox.warning(self, "Rating required", "Choose a 1–5 star rating first.")
            return
        self.rating_submitted.emit(
            {
                "scene_index": self.scene_index,
                "scene_id": self.scene_id,
                "rating": self.rating,
                "feedback": self.comment_input.text().strip(),
            }
        )


class ReviewCompletionPanel(QGroupBox):
    regenerate_requested = pyqtSignal(str)
    save_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Review complete", parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("All scenes have been reviewed."))
        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText(
            "Optional global prompt, e.g. faster transitions and focus on 5-star action"
        )
        self.prompt_input.setMaximumHeight(90)
        layout.addWidget(self.prompt_input)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("💾 Save Video")
        self.save_button.clicked.connect(self.save_requested.emit)
        self.regenerate_button = QPushButton("🔄 Regenerate Edit")
        self.regenerate_button.clicked.connect(
            lambda: self.regenerate_requested.emit(self.prompt_input.toPlainText().strip())
        )
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.regenerate_button)
        layout.addLayout(buttons)


# ==============================================================================
# ENGINE 2: AUDIO BEAT & RHYTHM ANALYZER
# ==============================================================================
class AudioBeatAnalyzer:
    """Menganalisis file audio untuk mendapatkan timestamp beat/ritme musik serta total durasi."""

    def __init__(self, audio_path):
        self.audio_path = audio_path

    def get_audio_info(self):
        try:
            y, sr = librosa.load(self.audio_path, sr=None)
            full_duration = float(librosa.get_duration(y=y, sr=sr))
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            beat_times = librosa.frames_to_time(beat_frames, sr=sr)
            
            tempo_float = float(np.mean(tempo))
            beat_list = [float(b) for b in beat_times]
            
            return tempo_float, beat_list, full_duration
        except Exception as e:
            print(f"[AudioAnalyzer Error] {e}")
            return 120.0, [], 30.0

# ==============================================================================
# ENGINE 3: REFERENCE VIDEO STYLE & TIMING ANALYZER
# ==============================================================================
class ReferenceStyleAnalyzer:
    """Menganalisis video referensi: memotong adegan, mengekstrak pola timing klip presisi & FPS."""

    def __init__(self, ref_video_path):
        self.ref_video_path = ref_video_path

    def analyze_style(self):
        analysis_data = {
            "avg_scene_duration": 2.5,
            "scene_durations": [],
            "scene_cuts": [],
            "fps": 60.0
        }
        try:
            scene_list = detect(self.ref_video_path, ContentDetector(threshold=27.0))
            cuts = [float(scene[0].seconds) for scene in scene_list]
            analysis_data["scene_cuts"] = cuts
            
            durations = []
            if len(cuts) > 1:
                durations = [cuts[i] - cuts[i-1] for i in range(1, len(cuts))]
                analysis_data["avg_scene_duration"] = float(np.mean(durations))
            else:
                durations = [2.5]
                
            analysis_data["scene_durations"] = durations
                
            cap = cv2.VideoCapture(self.ref_video_path)
            if cap.isOpened():
                analysis_data["fps"] = float(cap.get(cv2.CAP_PROP_FPS))
            cap.release()
        except Exception as e:
            print(f"[ReferenceAnalyzer Error] {e}")
            
        return analysis_data


class FileScannerEngine:
    """Find source videos while pruning irrelevant paths before file inspection."""

    EXCLUDED_DIRECTORIES = {
        "__pycache__",
        ".git",
        ".venv",
        "node_modules",
        ".cache",
        "cache",
        "tmp",
        "temp",
        "output video",
        "memory learning",
    }
    EXCLUDED_PATTERNS = ("*.tmp", "*.preview.*")

    def __init__(self, valid_extensions):
        self.valid_extensions = tuple(
            extension.casefold() for extension in valid_extensions
        )

    @staticmethod
    def _load_gitignore(root):
        gitignore_path = os.path.join(root, ".gitignore")
        if not os.path.isfile(gitignore_path):
            return []

        patterns = []
        with open(gitignore_path, "r", encoding="utf-8", errors="replace") as rules:
            for line in rules:
                pattern = line.strip()
                if not pattern or pattern.startswith("#"):
                    continue
                negated = pattern.startswith("!")
                if negated:
                    pattern = pattern[1:]
                pattern = pattern.lstrip("/").rstrip("/")
                if pattern:
                    patterns.append((pattern.replace("\\", "/"), negated))
        return patterns

    def _is_ignored(self, relative_path, is_directory, gitignore_rules):
        relative_path = relative_path.replace(os.sep, "/")
        basename = os.path.basename(relative_path).casefold()
        if is_directory and basename in self.EXCLUDED_DIRECTORIES:
            return True
        if not is_directory and any(
            fnmatch.fnmatchcase(basename, pattern)
            for pattern in self.EXCLUDED_PATTERNS
        ):
            return True

        ignored = False
        for pattern, negated in gitignore_rules:
            pattern = pattern.casefold()
            matches = fnmatch.fnmatchcase(relative_path.casefold(), pattern)
            if "/" not in pattern:
                matches = matches or fnmatch.fnmatchcase(basename, pattern)
            if matches:
                ignored = not negated
        return ignored

    def scan(self, root):
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise NotADirectoryError(f"Source folder does not exist: {root}")
        if os.path.basename(root).casefold() in self.EXCLUDED_DIRECTORIES:
            return [], [{"path": root, "status": "SKIPPED_FILTER"}]

        gitignore_rules = self._load_gitignore(root)
        video_files = []
        skipped_files = []

        def raise_walk_error(error):
            raise error

        for current_root, directories, filenames in os.walk(
            root, topdown=True, onerror=raise_walk_error
        ):
            relative_root = os.path.relpath(current_root, root)
            kept_directories = []
            for directory in directories:
                relative_path = (
                    directory
                    if relative_root == "."
                    else os.path.join(relative_root, directory)
                )
                if not self._is_ignored(relative_path, True, gitignore_rules):
                    kept_directories.append(directory)
            directories[:] = kept_directories

            for filename in filenames:
                if not filename.casefold().endswith(self.valid_extensions):
                    continue
                relative_path = (
                    filename
                    if relative_root == "."
                    else os.path.join(relative_root, filename)
                )
                path = os.path.join(current_root, filename)
                if self._is_ignored(relative_path, False, gitignore_rules):
                    skipped_files.append(
                        {"path": path, "status": "SKIPPED_FILTER"}
                    )
                    continue
                video_files.append(path)

        video_files.sort(key=lambda path: os.path.relpath(path, root).casefold())
        return video_files, skipped_files


class FileInspectionCache:
    """Persist file hashes keyed by normalized path, modification time, and size."""

    CHUNK_SIZE = 1024 * 1024

    def __init__(self, database_path):
        self.database_path = database_path
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS file_inspection_cache (
                    file_path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    file_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _cache_key(file_path):
        return os.path.normcase(os.path.abspath(file_path))

    def get(self, file_path, mtime_ns, file_size):
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT sha256 FROM file_inspection_cache
                WHERE file_path = ? AND mtime_ns = ? AND file_size = ?
                """,
                (self._cache_key(file_path), mtime_ns, file_size),
            ).fetchone()
        return row[0] if row else None

    def put(self, file_path, mtime_ns, file_size, sha256):
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO file_inspection_cache
                    (file_path, mtime_ns, file_size, sha256)
                VALUES (?, ?, ?, ?)
                """,
                (self._cache_key(file_path), mtime_ns, file_size, sha256),
            )


def inspect_video_file(file_path, cache):
    """Hash a source in bounded chunks and verify that OpenCV can decode a frame."""
    initial_stat = os.stat(file_path)
    mtime_ns = initial_stat.st_mtime_ns
    file_size = initial_stat.st_size
    cached_hash = cache.get(file_path, mtime_ns, file_size)
    if cached_hash is None:
        digest = hashlib.sha256()
        with open(file_path, "rb") as source_file:
            while True:
                chunk = source_file.read(FileInspectionCache.CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        sha256 = digest.hexdigest()
        current_stat = os.stat(file_path)
        if (
            current_stat.st_mtime_ns != mtime_ns
            or current_stat.st_size != file_size
        ):
            raise OSError("File changed during content inspection.")
        cache.put(file_path, mtime_ns, file_size, sha256)
        status = "SUCCESS"
    else:
        sha256 = cached_hash
        status = "SKIPPED_CACHE"

    capture = cv2.VideoCapture(file_path)
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not capture.isOpened() or fps <= 0 or frame_count <= 0:
            raise ValueError("Video metadata or stream structure is unreadable.")
        readable, frame = capture.read()
        if not readable or frame is None:
            for frame_index in sorted({frame_count // 2, frame_count - 1}):
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                readable, frame = capture.read()
                if readable and frame is not None:
                    break
        if not readable or frame is None:
            raise ValueError("No sampled video frame could be decoded.")
    finally:
        capture.release()

    current_stat = os.stat(file_path)
    if (
        current_stat.st_mtime_ns != mtime_ns
        or current_stat.st_size != file_size
    ):
        raise OSError("File changed during content inspection.")

    return {
        "path": file_path,
        "status": status,
        "sha256": sha256,
        "duration": frame_count / fps,
        "frame_count": frame_count,
        "fps": fps,
    }


class FileWorkerPoolController:
    """Run bounded file tasks with producer-consumer queues and per-file errors."""

    def __init__(self):
        self.max_workers = max(1, min(os.cpu_count() or 1, 4))

    def process(self, file_paths, worker, progress_callback=None):
        if not file_paths:
            return []

        tasks = queue.Queue()
        results = queue.Queue()
        for file_path in file_paths:
            tasks.put(file_path)
        for _ in range(self.max_workers):
            tasks.put(None)

        def consume_tasks():
            while True:
                file_path = tasks.get()
                if file_path is None:
                    tasks.task_done()
                    return
                try:
                    result = worker(file_path)
                    if not isinstance(result, dict):
                        raise TypeError("File worker must return a result dictionary.")
                    result = dict(result)
                    result.setdefault("path", file_path)
                except Exception as error:
                    result = {
                        "path": file_path,
                        "status": "FAILED",
                        "error": str(error),
                    }
                finally:
                    tasks.task_done()
                results.put(result)

        completed_results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            worker_futures = [
                executor.submit(consume_tasks)
                for _ in range(self.max_workers)
            ]
            for completed_count in range(1, len(file_paths) + 1):
                result = results.get()
                completed_results.append(result)
                if progress_callback:
                    progress_callback(
                        completed_count, len(file_paths), result
                    )
            for future in worker_futures:
                future.result()

        return completed_results


# ==============================================================================
# ENGINE 4: MULTI-THREADED RENDER & AI EDITING WORKER
# ==============================================================================
class VideoProcessingWorker(QThread):
    """Thread utama pemrosesan AI, scanning folder, sync timing, audio mapping, & GPU 4K 60FPS render."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(str, list, str, dict)
    error_signal = pyqtSignal(str)

    def __init__(self, raw_folder, sound_path, ref_path, output_folder, max_duration, 
                 is_full_sound, target_width, target_height, enable_stabilizer,
                 use_ollama, ollama_model, mute_raw_audio, timing_mode, scan_all_videos,
                 memory_engine, prompt_override=""):
        super().__init__()
        self.raw_folder = raw_folder
        self.sound_path = sound_path
        self.ref_path = ref_path
        self.output_folder = output_folder
        self.max_duration = float(max_duration)
        self.is_full_sound = is_full_sound
        self.target_width = target_width
        self.target_height = target_height
        self.enable_stabilizer = enable_stabilizer
        self.use_ollama = use_ollama
        self.ollama_model = ollama_model
        self.mute_raw_audio = mute_raw_audio
        self.timing_mode = timing_mode
        self.scan_all_videos = scan_all_videos
        self.memory_engine = memory_engine
        self.prompt_override = prompt_override

    def run(self):
        try:
            self.log_signal.emit("🚀 Memulai Autopilot AI Video Editing Pipeline...")
            self.progress_signal.emit(5)

            # Step 1: Scan Seluruh File Video Bahan di Folder
            self.log_signal.emit(f"📂 Memindai seluruh folder video bahan: {self.raw_folder}")
            valid_extensions = ('.mp4', '.mov', '.avi', '.mkv')
            scanner = FileScannerEngine(valid_extensions)
            video_files, filtered_files = scanner.scan(self.raw_folder)
            if filtered_files:
                self.log_signal.emit(
                    f"⏭️ {len(filtered_files)} video dilewati oleh filter "
                    "direktori/pola pengecualian."
                )

            if not video_files:
                self.error_signal.emit("Tidak ada file video valid (.mp4, .mov, .avi, .mkv) di folder bahan!")
                return

            self.log_signal.emit(
                f"✅ Berhasil menemukan {len(video_files)} kandidat video bahan; "
                "memulai inspeksi konten paralel."
            )
            inspection_cache = FileInspectionCache(
                self.memory_engine.database_path
            )
            inspection_pool = FileWorkerPoolController()

            def report_inspection_progress(completed, total, result):
                self.progress_signal.emit(5 + int(10 * completed / total))
                if result["status"] == "FAILED":
                    self.log_signal.emit(
                        f"⚠️ Video gagal diinspeksi dan dilewati: "
                        f"{os.path.basename(result['path'])} ({result['error']})"
                    )

            inspection_results = inspection_pool.process(
                video_files,
                lambda path: inspect_video_file(path, inspection_cache),
                report_inspection_progress,
            )
            video_files = [
                result["path"]
                for result in inspection_results
                if result["status"] in ("SUCCESS", "SKIPPED_CACHE")
            ]
            video_files.sort(
                key=lambda path: os.path.relpath(
                    path, self.raw_folder
                ).casefold()
            )
            cache_hits = sum(
                result["status"] == "SKIPPED_CACHE"
                for result in inspection_results
            )
            failed_inspections = sum(
                result["status"] == "FAILED"
                for result in inspection_results
            )
            self.log_signal.emit(
                f"✅ Inspeksi selesai: {len(video_files)} valid, "
                f"{cache_hits} hash dari cache, {failed_inspections} gagal."
            )
            if not video_files:
                self.error_signal.emit(
                    "Tidak ada file video yang lolos inspeksi struktur. "
                    "Periksa file video atau codec-nya."
                )
                return

            self.progress_signal.emit(15)

            # Step 2: Analyze optional soundtrack.
            if self.sound_path:
                self.log_signal.emit("🎵 Menganalisis ritme, tempo, & beat musik audio...")
                beat_analyzer = AudioBeatAnalyzer(self.sound_path)
                tempo, beats, audio_total_duration = beat_analyzer.get_audio_info()
                target_duration = (
                    audio_total_duration
                    if self.is_full_sound
                    else min(self.max_duration, audio_total_duration)
                )
                self.log_signal.emit(
                    f"🎶 Tempo Audio: {tempo:.1f} BPM | Total Beat Points: {len(beats)}"
                )
            else:
                tempo, beats = 120.0, []
                target_duration = self.max_duration
                self.log_signal.emit(
                    "🎵 Soundtrack eksternal tidak dipilih; audio akan diambil "
                    "dari video bahan sesuai opsi mute."
                )
            self.log_signal.emit(f"⏱️ Durasi Target Output: {target_duration:.1f} detik")
            self.progress_signal.emit(30)

            # Step 3: Analyze optional reference timing.
            if self.ref_path:
                self.log_signal.emit(
                    "🎯 Menganalisis struktur adegan & pola durasi potongan video referensi..."
                )
                ref_analyzer = ReferenceStyleAnalyzer(self.ref_path)
                ref_style = ref_analyzer.analyze_style()
            else:
                ref_style = {
                    "avg_scene_duration": 2.5,
                    "scene_durations": [2.5],
                    "scene_cuts": [],
                    "fps": 60.0,
                }
                self.log_signal.emit(
                    "🎯 Video referensi tidak dipilih; gunakan pola timing default, "
                    "Memory Learning, dan prompt jika tersedia."
                )
            ref_durations = ref_style["scene_durations"]
            if self.ref_path:
                self.log_signal.emit(
                    f"📊 Terdeteksi {len(ref_durations)} pola potongan adegan pada "
                    f"video referensi (Rata-rata: {ref_style['avg_scene_duration']:.2f}s)"
                )
            self.progress_signal.emit(40)

            self.progress_signal.emit(55)

            # Step 5: Penentuan Timing Presisi (Sesuai Pilihan Mode User)
            self.log_signal.emit(f"🎬 Menyusun urutan klip dengan Algoritma Timing: [{self.timing_mode}]...")
            selected_cuts = []
            current_time = 0.0
            video_idx = 0

            if self.timing_mode == "Reference":
                # Mode 1: Meniru persis pola durasi potongan video referensi
                ref_seq_idx = 0
                while current_time < target_duration:
                    dur = ref_durations[ref_seq_idx % len(ref_durations)]
                    cut_dur = min(dur, target_duration - current_time)
                    if cut_dur >= 0.3:
                        source_vid = video_files[video_idx % len(video_files)]
                        selected_cuts.append({
                            "file": source_vid,
                            "start": 0.0,
                            "duration": cut_dur
                        })
                        current_time += cut_dur
                        video_idx += 1
                    ref_seq_idx += 1

            elif self.timing_mode == "Beat":
                # Mode 2: Pemotongan murni berdasarkan ketukan beat musik (BPM)
                min_cut_duration = 0.8 if tempo > 150 else 0.5
                if beats and len(beats) > 1:
                    accumulated_dur = 0.0
                    for i in range(len(beats) - 1):
                        if current_time >= target_duration:
                            break
                        gap = beats[i+1] - beats[i]
                        accumulated_dur += gap
                        
                        if accumulated_dur >= min_cut_duration:
                            source_vid = video_files[video_idx % len(video_files)]
                            selected_cuts.append({
                                "file": source_vid,
                                "start": 0.0,
                                "duration": accumulated_dur
                            })
                            current_time += accumulated_dur
                            video_idx += 1
                            accumulated_dur = 0.0
                else:
                    self.timing_mode = "Reference" # Fallback jika beat tidak terdeteksi

            if not selected_cuts or self.timing_mode == "Hybrid":
                # Mode 3: Hybrid (Menyelaraskan durasi referensi dengan beat musik terdekat)
                ref_seq_idx = 0
                while current_time < target_duration:
                    target_ref_dur = ref_durations[ref_seq_idx % len(ref_durations)]
                    
                    # Cari beat terdekat dengan target durasi referensi
                    matched_dur = target_ref_dur
                    if beats:
                        closest_beat_gap = min(beats, key=lambda x: abs(x - (current_time + target_ref_dur)))
                        if closest_beat_gap > current_time:
                            matched_dur = closest_beat_gap - current_time

                    cut_dur = min(max(0.4, matched_dur), target_duration - current_time)
                    source_vid = video_files[video_idx % len(video_files)]
                    selected_cuts.append({
                        "file": source_vid,
                        "start": 0.0,
                        "duration": cut_dur
                    })
                    current_time += cut_dur
                    video_idx += 1
                    ref_seq_idx += 1

            # Keep beat timing within the requested render duration.
            bounded_cuts = []
            remaining_duration = target_duration
            for cut in selected_cuts:
                if remaining_duration <= 0:
                    break
                bounded_cut = dict(cut)
                bounded_cut["duration"] = min(
                    float(cut["duration"]), remaining_duration
                )
                if bounded_cut["duration"] > 0:
                    bounded_cuts.append(bounded_cut)
                    remaining_duration -= bounded_cut["duration"]
            if remaining_duration > 0.001:
                bounded_cuts.append(
                    {
                        "file": video_files[video_idx % len(video_files)],
                        "start": 0.0,
                        "duration": remaining_duration,
                    }
                )
            selected_cuts = bounded_cuts
            if not selected_cuts:
                self.error_signal.emit("Tidak ada potongan klip yang dapat dirender.")
                return

            # Sample and score candidate shots throughout every source video.
            use_prompt_vision = bool(self.prompt_override.strip())
            should_use_ollama = self.use_ollama or use_prompt_vision
            should_rank = should_use_ollama or self.memory_engine.has_evaluations()
            candidate_scenes = []
            memory_examples = (
                self.memory_engine.get_few_shot_examples(limit=5)
                if should_use_ollama
                else None
            )
            memory_profile = (
                self.memory_engine.get_preference_profile()
                if should_use_ollama
                else None
            )
            memory_evaluations = (
                self.memory_engine.get_memory_evaluations()
                if should_rank
                else None
            )
            candidate_count = max(
                4,
                min(
                    32,
                    (len(selected_cuts) + len(video_files) - 1) // len(video_files) + 2,
                ),
            )
            sample_videos = video_files if self.scan_all_videos else video_files[:30]
            sample_video_set = set(sample_videos)
            cut_durations = sorted(float(cut["duration"]) for cut in selected_cuts)
            minimum_cut_duration = min(cut_durations)
            probe_duration = max(
                0.3, cut_durations[len(cut_durations) // 2]
            )
            sample_rng = random.Random()
            scan_started_at = time.perf_counter()
            scan_expected_samples = candidate_count * len(video_files)
            completed_samples = 0
            recent_sample_durations = []
            last_scan_eta_at = scan_started_at

            def report_scan_progress(file_index, video_path, sample_index=None):
                nonlocal last_scan_eta_at
                now = time.perf_counter()
                elapsed = now - scan_started_at
                remaining_samples = max(
                    0, scan_expected_samples - completed_samples
                )
                average_sample_time = (
                    sum(recent_sample_durations) / len(recent_sample_durations)
                    if recent_sample_durations
                    else 0.0
                )
                remaining_seconds = average_sample_time * remaining_samples
                finish_time = (
                    datetime.now().astimezone()
                    + timedelta(seconds=remaining_seconds)
                )
                fraction_done = (
                    min(1.0, completed_samples / scan_expected_samples)
                    if scan_expected_samples
                    else 1.0
                )
                self.progress_signal.emit(55 + int(12 * fraction_done))

                if now - last_scan_eta_at < 2.0 and sample_index is not None:
                    return
                sample_progress = (
                    f" | sampel video ini {sample_index}/{len(starts)}"
                    if sample_index is not None
                    else ""
                )
                self.log_signal.emit(
                    f"⏱️ Penilaian video [{file_index + 1}/{len(video_files)}] "
                    f"{os.path.basename(video_path)}{sample_progress} | "
                    f"waktu berjalan {format_elapsed_time(elapsed)} | "
                    f"sisa ~{format_elapsed_time(remaining_seconds)} | "
                    f"perkiraan selesai {finish_time.strftime('%H:%M:%S')}"
                )
                last_scan_eta_at = now

            if should_use_ollama:
                self.log_signal.emit(
                    f"🤖 Menilai potongan dari seluruh rentang waktu {len(sample_videos)} video "
                    f"dengan Ollama [{self.ollama_model}] dan prompt edit..."
                )
                self.log_signal.emit(
                    "⏳ Perkiraan waktu dan jam selesai diperbarui selama penilaian; "
                    "sampel awal dapat lebih lama saat model dimuat."
                )
            else:
                self.log_signal.emit(
                    "🎞️ Mengambil sampel acak-tersebar dari awal hingga akhir setiap video..."
                )

            for file_index, video_path in enumerate(video_files):
                capture = cv2.VideoCapture(video_path)
                starts = []
                try:
                    fps = float(capture.get(cv2.CAP_PROP_FPS))
                    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                    if not capture.isOpened() or fps <= 0 or frame_count <= 0:
                        self.log_signal.emit(
                            f"⚠️ Video dilewati karena durasi/frame tidak dapat dibaca: "
                            f"{os.path.basename(video_path)}"
                        )
                        scan_expected_samples -= candidate_count
                        report_scan_progress(file_index, video_path)
                        continue

                    source_duration = frame_count / fps
                    starts = build_temporal_sample_starts(
                        source_duration,
                        minimum_cut_duration,
                        candidate_count,
                        sample_rng,
                    )
                    scan_expected_samples += len(starts) - candidate_count
                    for sample_index, start in enumerate(starts, start=1):
                        sample_started_at = time.perf_counter()
                        sample_time = min(
                            source_duration - 1.0 / fps,
                            start
                            + min(probe_duration, source_duration - start) / 2.0,
                        )
                        frame_index = max(
                            0, min(frame_count - 1, int(sample_time * fps))
                        )
                        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                        ret, frame = capture.read()
                        if not ret:
                            completed_samples += 1
                            recent_sample_durations.append(
                                time.perf_counter() - sample_started_at
                            )
                            del recent_sample_durations[:-20]
                            report_scan_progress(
                                file_index, video_path, sample_index
                            )
                            continue

                        encoded, encoded_frame = cv2.imencode(".jpg", frame)
                        if not encoded:
                            raise OSError(
                                "Gagal mengenkode frame sementara untuk evaluasi."
                            )
                        image_bytes = encoded_frame.tobytes()
                        frame_hash = self.memory_engine.perceptual_hash(image_bytes)
                        score = 7
                        if should_use_ollama and video_path in sample_video_set:
                            score = OllamaAIAnalyst.score_video_clip_with_memory(
                                self.ollama_model,
                                None,
                                self.memory_engine,
                                prompt_override=self.prompt_override,
                                image_bytes=image_bytes,
                                examples=memory_examples,
                                memory_profile=memory_profile,
                            )
                        if should_rank:
                            score = self.memory_engine.adjust_score_by_memory(
                                frame_hash, score, memory_evaluations
                            )

                        candidate_scenes.append(
                            {
                                "file": video_path,
                                "file_index": file_index,
                                "start": start,
                                "source_duration": source_duration,
                                "score": score,
                                "perceptual_hash": frame_hash,
                            }
                        )
                        completed_samples += 1
                        recent_sample_durations.append(
                            time.perf_counter() - sample_started_at
                        )
                        del recent_sample_durations[:-20]
                        report_scan_progress(file_index, video_path, sample_index)
                finally:
                    capture.release()

                self.log_signal.emit(
                    f"   ├ [{file_index + 1}/{len(video_files)}] "
                    f"{os.path.basename(video_path)}: "
                    f"{len(starts)} posisi sampel"
                )
                report_scan_progress(file_index, video_path)

            scan_finished_at = datetime.now().astimezone()
            scan_elapsed = time.perf_counter() - scan_started_at
            self.log_signal.emit(
                "✅ Penilaian folder selesai dalam "
                f"{format_elapsed_time(scan_elapsed)}; "
                f"selesai pukul {scan_finished_at.strftime('%H:%M:%S')}."
            )
            if not candidate_scenes:
                self.error_signal.emit(
                    "Tidak ada frame dari video bahan yang dapat dibaca. "
                    "Periksa file video atau codec-nya."
                )
                return

            sample_rng.shuffle(candidate_scenes)
            ranked_scenes = sorted(
                candidate_scenes, key=lambda scene: scene["score"], reverse=True
            )
            alternative_scenes = min(2, max(0, len(ranked_scenes) - 1))
            initial_scenes = (
                ranked_scenes[:-alternative_scenes]
                if alternative_scenes
                else ranked_scenes
            )
            chosen_scenes = initial_scenes[:len(selected_cuts)]
            while len(chosen_scenes) < len(selected_cuts):
                chosen_scenes.append(
                    initial_scenes[
                        len(chosen_scenes) % len(initial_scenes)
                    ]
                )
            chosen_scenes.sort(
                key=lambda scene: (scene["file_index"], scene["start"])
            )

            for cut, scene in zip(selected_cuts, chosen_scenes):
                cut["file"] = scene["file"]
                cut["start"] = min(
                    scene["start"],
                    max(0.0, scene["source_duration"] - cut["duration"]),
                )
                cut["source_duration"] = scene["source_duration"]
                cut["ai_predicted_score"] = scene["score"]
                cut["perceptual_hash"] = scene["perceptual_hash"]

            if should_rank:
                self.log_signal.emit(
                    f"🌟 {len(candidate_scenes)} posisi dari awal sampai akhir dinilai "
                    "berdasarkan AI/memori; potongan dengan skor terbaik dipilih "
                    "dan diurutkan berdasarkan waktu sumber."
                )
            else:
                self.log_signal.emit(
                    f"🌟 {len(candidate_scenes)} posisi dari awal sampai akhir diambil "
                    "secara acak-tersebar dan diurutkan berdasarkan waktu sumber."
                )

            self.progress_signal.emit(70)
            self.log_signal.emit(f"✂️ Berhasil menyusun {len(selected_cuts)} potongan klip video berpresisi tinggi.")

            self.log_signal.emit(f"📐 Target Resolusi Video: {self.target_width} x {self.target_height} @ 60FPS NVENC")
            
            if self.mute_raw_audio:
                self.log_signal.emit("🔇 Opsi Mute Suara Asli Video Bahan: AKTIF (Suara Asli Dihapus 100%)")
            else:
                self.log_signal.emit("🔊 Opsi Mute Suara Asli Video Bahan: NONAKTIF")
            has_source_audio = (
                not self.mute_raw_audio
                and video_has_audio_stream(selected_cuts[0]["file"])
            )
            if not has_source_audio and not self.sound_path:
                self.log_signal.emit(
                    "⚠️ Audio video bahan tidak tersedia atau dimute; hasil akan "
                    "memakai track hening."
                )

            if self.enable_stabilizer:
                self.log_signal.emit("🛡️ Mode Stabilizer (FFmpeg Deshake): AKTIF")

            self.progress_signal.emit(80)

            max_qc_retries = 2
            excluded_scenes = set()
            render_base = (
                f"AI_Render_{self.target_width}x{self.target_height}_60FPS_"
                f"{int(time.time())}"
            )
            final_output_path = ""
            preview_output_path = ""
            scene_metadata = []
            quality_report = {}

            for attempt in range(max_qc_retries + 1):
                scene_metadata = []
                timeline_position = 0.0
                for scene_index, cut in enumerate(selected_cuts):
                    scene_start = timeline_position
                    scene_end = scene_start + float(cut["duration"])
                    perceptual_hash = cut["perceptual_hash"]
                    scene_id = hashlib.sha256(
                        (
                            os.path.abspath(cut["file"])
                            + f"|{scene_index}|{scene_start:.6f}|{scene_end:.6f}|"
                            + perceptual_hash
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    scene_metadata.append(
                        {
                            "scene_id": f"hash_{scene_id}",
                            "source_file": cut["file"],
                            "clip_duration": float(cut["duration"]),
                            "ai_predicted_score": cut["ai_predicted_score"],
                            "external_prompt": self.prompt_override,
                            "perceptual_hash": perceptual_hash,
                            "timestamp_range": [scene_start, scene_end],
                        }
                    )
                    timeline_position = scene_end

                attempt_suffix = f"_QC_RETRY{attempt}" if attempt else ""
                final_output_path = os.path.join(
                    self.output_folder, f"{render_base}{attempt_suffix}.mp4"
                )
                preview_output_path = (
                    os.path.splitext(final_output_path)[0] + "_preview.wmv"
                )
                concat_list_path = os.path.join(
                    self.output_folder, "ffmpeg_concat_list.txt"
                )
                self.progress_signal.emit(80)
                with open(concat_list_path, "w", encoding="utf-8") as concat_file:
                    for cut in selected_cuts:
                        clean_path = (
                            cut["file"].replace("\\", "/").replace("'", "'\\''")
                        )
                        concat_file.write(f"file '{clean_path}'\n")
                        concat_file.write(f"inpoint {cut['start']:.6f}\n")
                        concat_file.write(
                            f"outpoint {cut['start'] + cut['duration']:.6f}\n"
                        )
                        concat_file.write(f"duration {cut['duration']:.3f}\n")

                vf_filters = []
                if self.enable_stabilizer:
                    vf_filters.append(
                        "deshake=x=0:y=0:w=0:h=0:rx=64:ry=64:edge=1"
                    )
                vf_filters.extend(
                    [
                        f"scale={self.target_width}:{self.target_height}:"
                        "force_original_aspect_ratio=increase",
                        f"crop={self.target_width}:{self.target_height}",
                        "fps=60",
                        "format=yuv420p",
                    ]
                )
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", concat_list_path,
                ]
                if self.sound_path:
                    ffmpeg_cmd.extend(["-i", self.sound_path])
                    if has_source_audio:
                        ffmpeg_cmd.extend(
                            [
                                "-filter_complex",
                                "[1:a:0]aresample=48000[music];"
                                "[0:a:0]aresample=48000[source];"
                                "[music][source]amix=inputs=2:duration=first:"
                                "dropout_transition=2[aout]",
                                "-map",
                                "[aout]",
                            ]
                        )
                    else:
                        ffmpeg_cmd.extend(["-map", "1:a:0"])
                elif has_source_audio:
                    ffmpeg_cmd.extend(["-map", "0:a:0"])
                else:
                    ffmpeg_cmd.extend(
                        [
                            "-f",
                            "lavfi",
                            "-t",
                            str(target_duration),
                            "-i",
                            "anullsrc=channel_layout=stereo:sample_rate=48000",
                            "-map",
                            "1:a:0",
                        ]
                    )
                ffmpeg_cmd.extend([
                    "-t", str(target_duration),
                    "-map", "0:v:0",
                    "-vf", ",".join(vf_filters),
                    "-c:v", "h264_nvenc",
                    "-preset", "p4",
                    "-cq", "19",
                    "-b:v", "35M",
                    "-maxrate", "50M",
                    "-bufsize", "70M",
                    "-c:a", "aac",
                    "-b:a", "320k",
                    "-shortest",
                    final_output_path,
                ])

                if attempt:
                    self.log_signal.emit(
                        f"QC gagal; merender ulang dengan adegan alternatif "
                        f"(percobaan {attempt}/{max_qc_retries})."
                    )
                self.log_signal.emit("Menjalankan Enkoder GPU NVIDIA NVENC RTX 4050...")
                process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in process.stdout:
                    if "frame=" in line or "fps=" in line:
                        self.log_signal.emit(f"Render Progress: {line.strip()}")
                process.wait()
                if os.path.exists(concat_list_path):
                    os.remove(concat_list_path)
                if process.returncode != 0:
                    self.error_signal.emit(
                        "Gagal merender video dengan FFmpeg! Periksa keterhubungan "
                        "file audio & driver GPU."
                    )
                    return

                self.log_signal.emit(
                    "QC hasil render: decode, frame hitam/beku, audio/video, "
                    "durasi, dan kelancaran timestamp."
                )
                render_report = check_render_quality(
                    final_output_path,
                    expected_duration=target_duration,
                    timeout=max(120, int(target_duration * 3)),
                    allow_silent_audio=not self.sound_path,
                )
                quality_report = render_report

                if render_report["status"] == "ok":
                    preview_cmd = [
                        "ffmpeg", "-y",
                        "-i", final_output_path,
                        "-map", "0:v:0",
                        "-map", "0:a:0?",
                        "-vf", "scale=-2:720,fps=30,format=yuv420p",
                        "-c:v", "wmv2",
                        "-q:v", "4",
                        "-c:a", "wmav2",
                        "-b:a", "192k",
                        "-ar", "44100",
                        "-ac", "2",
                        "-shortest",
                        preview_output_path,
                    ]
                    self.log_signal.emit(
                        "Menyiapkan dan memeriksa preview kompatibel Windows..."
                    )
                    preview_process = subprocess.run(
                        preview_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if preview_process.returncode != 0:
                        quality_report["findings"].append(
                            {
                                "severity": "error",
                                "code": "preview_creation_failed",
                                "message": (
                                    "Preview WMV gagal dibuat: "
                                    + preview_process.stderr[-1200:]
                                ),
                            }
                        )
                        quality_report["status"] = "error"
                    else:
                        preview_report = check_render_quality(
                            preview_output_path,
                            expected_duration=target_duration,
                            timeout=max(120, int(target_duration * 3)),
                            allow_silent_audio=not self.sound_path,
                        )
                        quality_report = {
                            **render_report,
                            "findings": (
                                render_report["findings"]
                                + preview_report["findings"]
                            ),
                            "metrics": {
                                "render": render_report["metrics"],
                                "preview": preview_report["metrics"],
                            },
                            "status": (
                                "error"
                                if "error" in (
                                    render_report["status"],
                                    preview_report["status"],
                                )
                                else "warning"
                                if (
                                    render_report["findings"]
                                    or preview_report["findings"]
                                )
                                else "ok"
                            ),
                        }

                try:
                    self.memory_engine.save_quality_check(quality_report)
                except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                    quality_report["findings"].append(
                        {
                            "severity": "error",
                            "code": "quality_report_not_saved",
                            "message": f"Hasil pemeriksaan kualitas tidak tersimpan: {error}",
                        }
                    )
                    quality_report["status"] = "error"

                if quality_report["status"] == "ok":
                    break

                self.log_signal.emit(
                    f"QC menemukan {len(quality_report['findings'])} masalah:"
                )
                for finding in quality_report["findings"][:8]:
                    self.log_signal.emit(f"   - {finding['message']}")

                if any(
                    finding["code"] == "quality_report_not_saved"
                    for finding in quality_report["findings"]
                ):
                    self.error_signal.emit(
                        "Laporan QC tidak dapat disimpan. Preview dan download "
                        "ditahan agar hasil yang belum terverifikasi tidak digunakan."
                    )
                    return
                if attempt == max_qc_retries:
                    break

                replacement_cuts = replace_problem_scenes(
                    selected_cuts,
                    ranked_scenes,
                    quality_report["findings"],
                    excluded_scenes,
                )
                if replacement_cuts is None:
                    break
                selected_cuts = replacement_cuts

            if quality_report["status"] != "ok":
                findings_text = "\n".join(
                    f"- {finding['message']}"
                    for finding in quality_report.get("findings", [])[:8]
                )
                self.error_signal.emit(
                    "QC belum lolos setelah mencoba adegan alternatif. Preview dan "
                    "download ditahan. Periksa file sumber atau jalankan ulang edit."
                    + (f"\n\n{findings_text}" if findings_text else "")
                )
                return

            self.progress_signal.emit(100)
            self.log_signal.emit(
                f"QC render dan preview lulus. Hasil siap ditinjau: {final_output_path}"
            )
            self.finished_signal.emit(
                final_output_path,
                scene_metadata,
                preview_output_path,
                quality_report,
            )
        except Exception as e:
            self.error_signal.emit(f"Error tidak terduga pada pipeline: {str(e)}")

# ==============================================================================
# ENGINE 5: ADVANCED PYQT5 STUDIO GUI PANEL
# ==============================================================================
class AIStudioPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Local Video Editor Studio Pro - 4K 60FPS (RTX Hardware Accelerated)")
        self.resize(1150, 950)
        self.setMinimumSize(1020, 820)
        
        self.is_full_sound_selected = False
        self.memory_engine = MemoryLearningEngine(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memory Learning")
        )
        self.current_scene_metadata = []
        self.last_render_path = ""
        self.init_ui()
        self.apply_dark_theme()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        self.page_stack = QStackedWidget()
        main_layout.addWidget(self.page_stack)

        self.setup_page = QWidget()
        self.page_stack.addWidget(self.setup_page)
        setup_layout = QVBoxLayout(self.setup_page)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_scroll = QScrollArea()
        setup_scroll.setObjectName("setupScroll")
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.NoFrame)
        setup_layout.addWidget(setup_scroll)

        setup_content = QWidget()
        setup_content.setObjectName("setupContent")
        setup_scroll.setWidget(setup_content)
        main_layout = QVBoxLayout(setup_content)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(4, 4, 12, 12)

        # Header Title Banner
        header_frame = QFrame()
        header_frame.setObjectName("headerCard")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(6)
        title_label = QLabel("🎬  LOCAL AI VIDEO EDITOR STUDIO PRO")
        title_label.setObjectName("pageTitle")
        title_label.setFont(QFont("Segoe UI", 20, QFont.Bold))
        subtitle_label = QLabel("Autopilot Video Editing • Full Folder Scanning • Reference Timing Sync • Qwen2.5-VL 3B • 4K 60FPS")
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setFont(QFont("Segoe UI", 10))
        subtitle_label.setWordWrap(True)
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        main_layout.addWidget(header_frame)

        # Form Controls Container
        form_group = QGroupBox("Pengaturan Input Project & File Media")
        form_layout = QVBoxLayout(form_group)
        form_layout.setSpacing(12)

        # 1. Raw Videos Folder Input
        self.txt_raw_folder = QLineEdit()
        self.txt_raw_folder.setPlaceholderText("Pilih folder yang berisi video-video bahan...")
        btn_browse_raw = QPushButton("Browse Folder")
        btn_browse_raw.clicked.connect(self.browse_raw_folder)
        form_layout.addLayout(self.create_form_row("📁 Folder Video Bahan:", self.txt_raw_folder, btn_browse_raw))

        # 2. Sound/Audio File Input
        self.txt_sound_path = QLineEdit()
        self.txt_sound_path.setPlaceholderText(
            "Opsional — pilih musik/soundtrack; kosong = gunakan audio video bahan"
        )
        btn_browse_sound = QPushButton("Browse Audio")
        btn_browse_sound.clicked.connect(self.browse_sound_file)
        form_layout.addLayout(
            self.create_form_row(
                "🎵 Sound / Musik Utama (Opsional):",
                self.txt_sound_path,
                btn_browse_sound,
            )
        )

        # 3. Reference Video Input
        self.txt_ref_path = QLineEdit()
        self.txt_ref_path.setPlaceholderText(
            "Opsional — kosong = gunakan Memory Learning dan prompt"
        )
        btn_browse_ref = QPushButton("Browse Referensi")
        btn_browse_ref.clicked.connect(self.browse_ref_file)
        form_layout.addLayout(
            self.create_form_row(
                "🎯 Video Referensi Gaya (Opsional):",
                self.txt_ref_path,
                btn_browse_ref,
            )
        )

        # 4. Save Output Location Input
        self.txt_output_folder = QLineEdit()
        self.txt_output_folder.setPlaceholderText("Pilih folder lokasi penyimpanan hasil render...")
        btn_browse_out = QPushButton("Browse Output")
        btn_browse_out.clicked.connect(self.browse_output_folder)
        form_layout.addLayout(self.create_form_row("💾 Folder Simpan Hasil:", self.txt_output_folder, btn_browse_out))

        # 5. Audio Control & Timing Engine Group
        audio_timing_group = QGroupBox("🎛️ Pengaturan Suara & Penentu Timing Potongan Clip")
        audio_timing_layout = QHBoxLayout(audio_timing_group)
        
        # Audio Mute Checkbox
        self.chk_mute_raw = QCheckBox("🔇 Hapus Suara Asli Video Bahan (Mute Raw Audio)")
        self.chk_mute_raw.setChecked(False)
        self.chk_mute_raw.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        # Timing Mode Dropdown
        lbl_timing = QLabel("⏱️ Penentu Timing Clip:")
        lbl_timing.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        self.combo_timing_mode = QComboBox()
        self.combo_timing_mode.addItem("🎯 Ikuti Timing & Durasi Video Referensi", "Reference")
        self.combo_timing_mode.addItem("🎵 Ikuti Beat & Rhythm Musik Audio (BPM)", "Beat")
        self.combo_timing_mode.addItem("⚡ Hybrid (Perpaduan Referensi & Beat AI)", "Hybrid")
        self.combo_timing_mode.setCurrentIndex(2)

        audio_timing_layout.addWidget(self.chk_mute_raw)
        audio_timing_layout.addSpacing(15)
        audio_timing_layout.addWidget(lbl_timing)
        audio_timing_layout.addWidget(self.combo_timing_mode)
        audio_timing_layout.addStretch()
        form_layout.addWidget(audio_timing_group)

        # 6. Ollama Local AI Integration Group
        ollama_group = QGroupBox("🤖 Integrasi Ollama Local AI Vision Model & Folder Scanning")
        ollama_layout = QHBoxLayout(ollama_group)
        
        self.chk_ollama = QCheckBox("Gunakan Ollama Vision AI untuk Seleksi Klip Terbaik")
        self.chk_ollama.setChecked(True)
        self.chk_ollama.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        self.chk_scan_all = QCheckBox("🔍 Scan Seluruh Video Bahan (100% Full Folder)")
        self.chk_scan_all.setChecked(True)
        self.chk_scan_all.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        lbl_model = QLabel("Model Vision Ollama:")
        lbl_model.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        self.combo_ollama_models = QComboBox()
        installed_models = OllamaAIAnalyst.get_installed_models()
        self.combo_ollama_models.addItems(installed_models)

        ollama_layout.addWidget(self.chk_ollama)
        ollama_layout.addWidget(self.chk_scan_all)
        ollama_layout.addSpacing(15)
        ollama_layout.addWidget(lbl_model)
        ollama_layout.addWidget(self.combo_ollama_models)
        ollama_layout.addStretch()
        form_layout.addWidget(ollama_group)

        external_prompt_group = QGroupBox("📝 External Promt (Opsional)")
        external_prompt_layout = QVBoxLayout(external_prompt_group)
        self.txt_external_prompt = QTextEdit()
        self.txt_external_prompt.setPlaceholderText(
            "Contoh: fokus pada aksi terbaik, gunakan potongan cepat, "
            "hindari bagian gelap, dan ikuti musik."
        )
        self.txt_external_prompt.setFixedHeight(72)
        external_prompt_layout.addWidget(self.txt_external_prompt)
        prompt_hint = QLabel(
            "Prompt ini memandu pemilihan klip bersama pengalaman Memory Learning. "
            "Jika diisi, penilaian Ollama akan digunakan."
        )
        prompt_hint.setWordWrap(True)
        prompt_hint.setObjectName("helperText")
        external_prompt_layout.addWidget(prompt_hint)
        form_layout.addWidget(external_prompt_group)

        # 7. Aspect Ratio & Stabilizer Controls Group
        config_group = QGroupBox("📐 Ukuran Tampilan Video & Stabilizer Engine")
        config_layout = QHBoxLayout(config_group)
        config_layout.setSpacing(20)

        lbl_aspect = QLabel("📐 Ukuran / Rasio Tampilan:")
        lbl_aspect.setFont(QFont("Segoe UI", 9, QFont.Bold))
        
        self.combo_aspect = QComboBox()
        self.combo_aspect.addItem("📱 9:16 Vertical (2160x3840 - TikTok / Reels / Shorts)", (2160, 3840))
        self.combo_aspect.addItem("🖥️ 16:9 Horizontal (3840x2160 - YouTube / Landscape)", (3840, 2160))
        self.combo_aspect.addItem("🔲 1:1 Square (2160x2160 - Feed / Square)", (2160, 2160))
        self.combo_aspect.addItem("📸 4:5 Portrait (1728x2160 - Instagram Post)", (1728, 2160))
        self.combo_aspect.setCurrentIndex(0)

        self.chk_stabilizer = QCheckBox("🛡️ Aktifkan AI Stabilizer (Deshake Video Getar)")
        self.chk_stabilizer.setFont(QFont("Segoe UI", 9, QFont.Bold))

        config_layout.addWidget(lbl_aspect)
        config_layout.addWidget(self.combo_aspect)
        config_layout.addSpacing(15)
        config_layout.addWidget(self.chk_stabilizer)
        config_layout.addStretch()

        form_layout.addWidget(config_group)

        # 8. Dedicated Duration Control Section
        dur_group = QGroupBox("⏱️ Pengaturan Durasi Maksimal Video Output")
        dur_vlayout = QVBoxLayout(dur_group)
        
        btn_preset_layout = QHBoxLayout()
        lbl_presets = QLabel("⚡ Tombol Preset Cepat:")
        lbl_presets.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl_presets.setObjectName("accentLabel")
        btn_preset_layout.addWidget(lbl_presets)

        self.btn_15s = QPushButton("⚡ 15s (Shorts)")
        self.btn_30s = QPushButton("🔥 30s (Reels)")
        self.btn_60s = QPushButton("📱 60s (TikTok)")
        self.btn_full_sound = QPushButton("🎵 Ikuti Durasi Sound Full")

        self.btn_15s.clicked.connect(lambda: self.set_duration_preset(15.0))
        self.btn_30s.clicked.connect(lambda: self.set_duration_preset(30.0))
        self.btn_60s.clicked.connect(lambda: self.set_duration_preset(60.0))
        self.btn_full_sound.clicked.connect(self.set_full_sound_preset)

        for btn in [self.btn_15s, self.btn_30s, self.btn_60s, self.btn_full_sound]:
            btn.setProperty("variant", "secondary")
            btn_preset_layout.addWidget(btn)

        btn_preset_layout.addStretch()
        dur_vlayout.addLayout(btn_preset_layout)

        slider_layout = QHBoxLayout()
        lbl_custom = QLabel("🎚️ Atur Presisi Durasi:")
        lbl_custom.setFont(QFont("Segoe UI", 9, QFont.Bold))

        self.duration_slider = QSlider(Qt.Horizontal)
        self.duration_slider.setRange(5, 300)
        self.duration_slider.setValue(30)

        self.spin_duration = QDoubleSpinBox()
        self.spin_duration.setRange(5.0, 600.0)
        self.spin_duration.setValue(30.0)
        self.spin_duration.setSuffix(" detik")
        self.spin_duration.setSingleStep(1.0)
        self.spin_duration.setFixedWidth(120)

        self.duration_slider.valueChanged.connect(self.on_slider_changed)
        self.spin_duration.valueChanged.connect(self.on_spinbox_changed)

        slider_layout.addWidget(lbl_custom)
        slider_layout.addWidget(self.duration_slider)
        slider_layout.addWidget(self.spin_duration)
        dur_vlayout.addLayout(slider_layout)

        form_layout.addWidget(dur_group)
        main_layout.addWidget(form_group)

        # Process Log & Progress Bar
        log_group = QGroupBox("Live Console Log & Progress")
        log_layout = QVBoxLayout(log_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        log_layout.addWidget(self.progress_bar)

        self.txt_console = QTextEdit()
        self.txt_console.setObjectName("console")
        self.txt_console.setReadOnly(True)
        self.txt_console.setFont(QFont("Consolas", 9))
        self.txt_console.setMinimumHeight(130)
        log_layout.addWidget(self.txt_console)

        main_layout.addWidget(log_group)

        # Action Button
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("🚀 MULAI BUAT VIDEO AI (4K 60FPS)")
        self.btn_start.setObjectName("primaryAction")
        self.btn_start.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.btn_start.setMinimumHeight(50)
        self.btn_start.clicked.connect(lambda: self.start_processing())
        btn_layout.addWidget(self.btn_start)

        main_layout.addLayout(btn_layout)

        self.review_page = QWidget()
        review_layout = QVBoxLayout(self.review_page)
        review_layout.addWidget(QLabel("🎞️ Rendered video review"))
        self.quality_report_label = QLabel(
            "Automatic render check will appear here after processing."
        )
        self.quality_report_label.setWordWrap(True)
        self.quality_report_label.setStyleSheet(
            "padding: 8px; border: 1px solid #3D3D3D; border-radius: 5px;"
        )
        review_layout.addWidget(self.quality_report_label)
        self.video_player = ClipPauseVideoPlayer(self.review_page)
        self.video_player.setMinimumHeight(420)
        self.video_player.scene_pause_reached.connect(self.on_scene_pause_reached)
        self.video_player.video_finished.connect(self.on_video_finished)
        review_layout.addWidget(self.video_player, 1)

        self.rating_panel = SceneRatingPanel(self.review_page)
        self.rating_panel.rating_submitted.connect(self.on_rating_submitted)
        review_layout.addWidget(self.rating_panel)
        self.video_player.replay_requested.connect(self.on_replay_requested)
        self.video_player.playback_error.connect(self.on_preview_error)

        self.completion_panel = ReviewCompletionPanel(self.review_page)
        self.completion_panel.save_requested.connect(self.save_rendered_video)
        self.completion_panel.regenerate_requested.connect(self.regenerate_edit)
        self.completion_panel.hide()
        review_layout.addWidget(self.completion_panel)
        self.page_stack.addWidget(self.review_page)

    def create_form_row(self, label_text, line_edit, button):
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(label_text)
        label.setMinimumWidth(170)
        label.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        button.setMinimumWidth(126)
        row.addWidget(label)
        row.addWidget(line_edit)
        row.addWidget(button)
        return row

    def set_duration_preset(self, val):
        self.is_full_sound_selected = False
        self.spin_duration.setValue(val)
        self.duration_slider.setValue(int(val))
        self.append_log(f"⏱️ Durasi maksimal diatur ke: {val} detik")

    def set_full_sound_preset(self):
        self.is_full_sound_selected = True
        sound_path = self.txt_sound_path.text().strip()
        if sound_path and os.path.exists(sound_path):
            try:
                y, sr = librosa.load(sound_path, sr=None)
                full_dur = float(librosa.get_duration(y=y, sr=sr))
                self.spin_duration.setValue(full_dur)
                self.duration_slider.setValue(int(min(full_dur, 300)))
                self.append_log(f"🎵 Durasi diatur mengikuti full sound: {full_dur:.1f} detik")
                return
            except Exception as e:
                pass
        if not sound_path:
            self.is_full_sound_selected = False
            self.append_log(
                "🎵 Soundtrack belum dipilih; durasi akan mengikuti batas "
                "durasi yang diatur."
            )
            return
        self.append_log("🎵 Mode Full Sound Aktif (Akan otomatis menyesuaikan durasi audio saat proses berjalan).")

    def on_slider_changed(self, value):
        self.is_full_sound_selected = False
        self.spin_duration.blockSignals(True)
        self.spin_duration.setValue(float(value))
        self.spin_duration.blockSignals(False)

    def on_spinbox_changed(self, value):
        self.is_full_sound_selected = False
        self.duration_slider.blockSignals(True)
        self.duration_slider.setValue(int(min(value, 300)))
        self.duration_slider.blockSignals(False)

    def browse_raw_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Video Bahan")
        if folder:
            self.txt_raw_folder.setText(folder)

    def browse_sound_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih File Audio/Soundtrack", "", "Audio Files (*.mp3 *.wav *.aac *.m4a)")
        if file_path:
            self.txt_sound_path.setText(file_path)

    def browse_ref_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Pilih Video Referensi", "", "Video Files (*.mp4 *.mov *.mkv *.avi)")
        if file_path:
            self.txt_ref_path.setText(file_path)

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Pilih Folder Output Simpan Video")
        if folder:
            self.txt_output_folder.setText(folder)

    def append_log(self, text):
        self.txt_console.append(text)
        self.txt_console.moveCursor(QTextCursor.End)

    def start_processing(self, prompt_override=""):
        raw_folder = self.txt_raw_folder.text().strip()
        sound_path = self.txt_sound_path.text().strip()
        ref_path = self.txt_ref_path.text().strip()
        output_folder = self.txt_output_folder.text().strip()
        max_duration = self.spin_duration.value()

        target_width, target_height = self.combo_aspect.currentData()
        enable_stabilizer = self.chk_stabilizer.isChecked()
        use_ollama = self.chk_ollama.isChecked()
        ollama_model = self.combo_ollama_models.currentText()
        mute_raw_audio = self.chk_mute_raw.isChecked()
        timing_mode = self.combo_timing_mode.currentData()
        scan_all_videos = self.chk_scan_all.isChecked()
        external_prompt = self.txt_external_prompt.toPlainText().strip()
        if prompt_override.strip():
            if external_prompt:
                external_prompt += (
                    "\n\nArahan tambahan dari panel review:\n" + prompt_override.strip()
                )
            else:
                external_prompt = prompt_override.strip()

        if not os.path.exists(raw_folder):
            QMessageBox.warning(self, "Input Error", "Silakan pilih folder video bahan yang valid!")
            return
        if sound_path and not os.path.isfile(sound_path):
            QMessageBox.warning(self, "Input Error", "Silakan pilih file sound/audio yang valid!")
            return
        if ref_path and not os.path.isfile(ref_path):
            QMessageBox.warning(self, "Input Error", "Silakan pilih file video referensi yang valid!")
            return
        if not os.path.exists(output_folder):
            QMessageBox.warning(self, "Input Error", "Silakan pilih folder simpan hasil output yang valid!")
            return

        self.btn_start.setEnabled(False)
        self.page_stack.setCurrentWidget(self.setup_page)
        self.progress_bar.setValue(0)
        self.txt_console.clear()

        # Inisialisasi Worker Thread
        self.worker = VideoProcessingWorker(
            raw_folder, sound_path, ref_path, output_folder, 
            max_duration, self.is_full_sound_selected,
            target_width, target_height, enable_stabilizer,
            use_ollama, ollama_model, mute_raw_audio,
            timing_mode, scan_all_videos, self.memory_engine, external_prompt
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self.on_processing_finished)
        self.worker.error_signal.connect(self.on_processing_error)
        self.worker.start()

    def on_processing_finished(
        self, result_path, scene_metadata, preview_path, quality_report
    ):
        self.btn_start.setEnabled(True)
        self.last_render_path = result_path
        self.current_scene_metadata = scene_metadata
        self.quality_report_label.setText(
            self.format_quality_report(quality_report)
        )
        self.completion_panel.hide()
        self.rating_panel.show()
        self.page_stack.setCurrentWidget(self.review_page)
        self.video_player.load_video(preview_path, scene_metadata)
        self.append_log("🎬 Render selesai. Preview dan evaluasi adegan dimulai.")

    @staticmethod
    def format_quality_report(report):
        status = report.get("status", "error")
        if status == "ok":
            return (
                "✅ QC render dan preview lulus: file dapat dibaca, durasi/audio/video "
                "valid, dan tidak ada indikasi freeze, layar hitam, atau gangguan timing. "
                "Tetap tinjau preview sebelum menyimpan."
            )

        title = (
            "❌ Pemeriksaan kualitas mengalami masalah:"
            if status == "error"
            else "⚠️ Temuan pemeriksaan kualitas (perlu ditinjau):"
        )
        details = [
            f"• {finding['message']}"
            for finding in report.get("findings", [])[:5]
        ]
        if not details:
            details.append("• Pemeriksaan otomatis tidak menghasilkan detail temuan.")
        return title + "\n" + "\n".join(details)

    def on_scene_pause_reached(self, scene_index, timestamp):
        if scene_index >= len(self.current_scene_metadata):
            return
        self.rating_panel.set_scene(
            scene_index, self.current_scene_metadata[scene_index]
        )
        self.rating_panel.show()
        self.append_log(
            f"⏸️ Playback berhenti pada adegan {scene_index + 1} ({timestamp:.2f}s)."
        )

    def on_rating_submitted(self, rating_data):
        scene_index = rating_data["scene_index"]
        scene = dict(self.current_scene_metadata[scene_index])
        scene["user_rating"] = rating_data["rating"]
        scene["user_feedback"] = rating_data["feedback"]
        try:
            self.memory_engine.save_evaluation(scene)
        except (OSError, sqlite3.Error, ValueError) as error:
            QMessageBox.critical(
                self, "Memory save failed", f"Could not save this evaluation:\n{error}"
            )
            return

        self.append_log(
            f"💾 Rating {rating_data['rating']}/5 tersimpan untuk adegan {scene_index + 1}."
        )
        self.video_player.resume_after_review()

    def on_video_finished(self):
        self.rating_panel.hide()
        self.completion_panel.show()
        self.append_log("✅ Semua adegan selesai ditinjau.")

    def on_replay_requested(self):
        self.completion_panel.hide()
        self.rating_panel.show()
        self.video_player.replay()
        self.append_log("🔁 Preview diputar ulang dari awal.")

    def on_preview_error(self, error_text):
        self.append_log(f"❌ Preview gagal diputar: {error_text}")
        QMessageBox.critical(
            self,
            "Preview playback error",
            "Windows tidak dapat memutar file preview.\n\n"
            f"{error_text}\n\n"
            "File MP4 hasil render tetap tersedia di folder output.",
        )

    def save_rendered_video(self):
        if not self.last_render_path or not os.path.isfile(self.last_render_path):
            QMessageBox.critical(self, "Save failed", "Rendered video is no longer available.")
            return
        suggested_path = os.path.join(
            os.path.dirname(self.last_render_path),
            os.path.basename(self.last_render_path),
        )
        destination, _ = QFileDialog.getSaveFileName(
            self, "Save rendered video", suggested_path, "MP4 Video (*.mp4)"
        )
        if not destination:
            return
        if not destination.lower().endswith(".mp4"):
            destination += ".mp4"
        try:
            if os.path.abspath(destination).lower() != os.path.abspath(self.last_render_path).lower():
                shutil.copy2(self.last_render_path, destination)
            QMessageBox.information(self, "Video saved", f"Video saved to:\n{destination}")
        except OSError as error:
            QMessageBox.critical(self, "Save failed", f"Could not save video:\n{error}")

    def regenerate_edit(self, prompt_override):
        self.completion_panel.hide()
        self.video_player.player.stop()
        self.page_stack.setCurrentWidget(self.setup_page)
        self.append_log("🔄 Membuat ulang edit dengan feedback tersimpan...")
        self.start_processing(prompt_override)

    def on_processing_error(self, error_msg):
        self.btn_start.setEnabled(True)
        self.page_stack.setCurrentWidget(self.setup_page)
        QMessageBox.critical(self, "Process Error", f"Terjadi kesalahan:\n{error_msg}")

    def apply_dark_theme(self):
        dark_stylesheet = """
        QWidget {
            color: #E5EAF0;
            font-family: 'Segoe UI', sans-serif;
            font-size: 10pt;
        }
        QMainWindow, QWidget#setupScroll, QWidget#setupContent {
            background-color: #11161D;
        }
        QFrame#headerCard {
            background-color: #19232D;
            border: 1px solid #293744;
            border-radius: 12px;
        }
        QLabel#pageTitle {
            color: #F4F7FA;
            letter-spacing: 0.4px;
        }
        QLabel#pageSubtitle, QLabel#helperText {
            color: #A7B3BF;
        }
        QLabel#accentLabel {
            color: #73E0B1;
            font-weight: 600;
        }
        QGroupBox {
            font-weight: 600;
            border: 1px solid #293744;
            border-radius: 10px;
            margin-top: 12px;
            padding: 18px 14px 12px 14px;
            background-color: #19212A;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 7px;
            color: #73E0B1;
        }
        QLineEdit, QTextEdit, QComboBox, QDoubleSpinBox {
            background-color: #111820;
            border: 1px solid #344351;
            border-radius: 7px;
            padding: 8px 10px;
            selection-background-color: #287A5A;
        }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
            border: 1px solid #73E0B1;
        }
        QLineEdit { min-height: 20px; }
        QComboBox { min-height: 20px; }
        QComboBox::drop-down {
            border: none;
            width: 28px;
        }
        QComboBox QAbstractItemView {
            background-color: #19212A;
            border: 1px solid #344351;
            selection-background-color: #287A5A;
        }
        QCheckBox { spacing: 9px; }
        QCheckBox::indicator {
            width: 17px;
            height: 17px;
            border-radius: 5px;
            border: 1px solid #526271;
            background-color: #111820;
        }
        QCheckBox::indicator:checked {
            background-color: #55C994;
            border: 1px solid #55C994;
        }
        QPushButton {
            background-color: #287A5A;
            color: #FFFFFF;
            border: 1px solid #348A67;
            border-radius: 7px;
            padding: 8px 14px;
            font-weight: 600;
        }
        QPushButton:hover { background-color: #32966E; }
        QPushButton:pressed { background-color: #206348; }
        QPushButton:disabled {
            background-color: #29333D;
            color: #84919D;
            border-color: #34404B;
        }
        QPushButton[variant="secondary"] {
            background-color: #202B35;
            border: 1px solid #3A4A58;
            color: #DCE5EC;
        }
        QPushButton[variant="secondary"]:hover {
            background-color: #2A3945;
            border-color: #73E0B1;
        }
        QPushButton#primaryAction {
            background-color: #21865E;
            border: 1px solid #46A87E;
            border-radius: 9px;
            font-size: 12pt;
            font-weight: 700;
        }
        QPushButton#primaryAction:hover { background-color: #2A9A6E; }
        QTextEdit#console {
            background-color: #0D1319;
            border: 1px solid #293744;
            color: #9CE2BE;
        }
        QProgressBar {
            min-height: 16px;
            border: 1px solid #344351;
            border-radius: 7px;
            text-align: center;
            background-color: #111820;
            color: #F4F7FA;
            font-weight: 600;
        }
        QProgressBar::chunk {
            background-color: #55C994;
            border-radius: 6px;
        }
        QSlider::groove:horizontal {
            height: 6px;
            background: #344351;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #73E0B1;
            border: 2px solid #111820;
            width: 16px;
            height: 16px;
            margin: -6px 0;
            border-radius: 9px;
        }
        QScrollBar:vertical {
            width: 10px;
            background: transparent;
            margin: 2px;
        }
        QScrollBar::handle:vertical {
            background: #3A4A58;
            min-height: 28px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover { background: #536776; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        """
        self.setStyleSheet(dark_stylesheet)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AIStudioPanel()
    window.show()
    sys.exit(app.exec_())