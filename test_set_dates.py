#!/usr/bin/env python3
"""
Полное тестирование всех требований set_dates_from_folders.

Требования из документации скрипта:
  R01: Устанавливает mtime/atime через os.utime()
  R02: Записывает EXIF (DateTimeOriginal, CreateDate, ModifyDate)
  R03: Формат YYYY → YYYY-01-01
  R04: Формат YYYY.MM → YYYY-MM-01
  R05: Формат YYYY.MM.DD → YYYY-MM-DD
  R06: Формат YYYY.MM.DD-DD → YYYY-MM-DD (начало диапазона)
  R07: Формат YYYY.MM.DD-MM.DD → YYYY-MM-DD (начало диапазона)
  R08: НИКОГДА не удаляет файлы
  R09: НИКОГДА не переименовывает файлы и папки
  R10: НИКОГДА не перемещает файлы и папки
  R11: Без --apply — dry-run (файлы НЕ изменяются)
  R12: Пропускает файлы, если даты уже совпадают (идемпотентность)
  R13: Дата из имени файла приоритетнее даты из папки
  R14: Поднимается по дереву каталогов к ближайшей папке с датой
  R15: Пропускает скрытые файлы и папки
  R16: Невалидные даты отклоняются (31 фев, месяц 13, ...)
  R17: Не создаёт *_original копии файлов

Запуск:
  python3 test_set_dates.py
"""

import os
import re
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    HAS_EXIFTOOL = subprocess.run(
        ["exiftool", "-ver"], capture_output=True, timeout=10
    ).returncode == 0
except FileNotFoundError:
    HAS_EXIFTOOL = False

SCRIPT = Path(__file__).parent / "set_dates_from_folders.py"

# ─── Utility ──────────────────────────────────────────────────────────────────

passed = 0
failed = 0
total = 0


def check(req_id: str, description: str, condition: bool, detail: str = ""):
    global passed, failed, total
    total += 1
    status = "PASS" if condition else "FAIL"
    if condition:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {req_id}: {description}")
    if detail and not condition:
        print(f"         → {detail}")


def run_script(base: Path, *extra_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(base)] + list(extra_args),
        capture_output=True, text=True, timeout=120,
    )


def snapshot_tree(base: Path) -> dict:
    """Возвращает {relative_path: {type, size, mtime}}."""
    snap = {}
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(dirs)
        rp = str(Path(root).relative_to(base)) if Path(root) != base else "."
        snap[rp] = {"type": "dir"}
        for f in sorted(files):
            fp = Path(root) / f
            st = fp.stat()
            rel = str(fp.relative_to(base))
            snap[rel] = {
                "type": "file",
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
    return snap


def create_jpg(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if HAS_PILLOW:
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(str(path), "JPEG")
    else:
        # Минимальный валидный JPEG (без Pillow — exiftool может не записать EXIF)
        path.write_bytes(
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
            b'\xff\xd9'
        )


def create_txt(path: Path, content: str = "test"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def get_exif_date(path: Path) -> str:
    if not HAS_EXIFTOOL:
        return ""
    r = subprocess.run(
        ["exiftool", "-s3", "-DateTimeOriginal", str(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def get_exif_field(path: Path, field: str) -> str:
    """Читает произвольное поле EXIF/XMP через exiftool. Возвращает пустую строку если поле отсутствует."""
    if not HAS_EXIFTOOL:
        return ""
    r = subprocess.run(
        ["exiftool", "-s3", f"-{field}", str(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def get_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def set_file_date(path: Path, dt: datetime) -> None:
    """Устанавливает EXIF (DateTimeOriginal и др.) и mtime/atime файла."""
    ts = dt.timestamp()
    if HAS_EXIFTOOL:
        exif_str = dt.strftime("%Y:%m:%d %H:%M:%S")
        subprocess.run(
            [
                "exiftool", "-q", "-overwrite_original", "-m",
                "-DateTimeOriginal=" + exif_str,
                "-CreateDate=" + exif_str,
                "-ModifyDate=" + exif_str,
                str(path),
            ],
            capture_output=True,
        )
    os.utime(path, (ts, ts))


# ─── Setup ────────────────────────────────────────────────────────────────────

def setup(base: Path):
    """Создаёт тестовую структуру в base."""
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # R03: YYYY
    create_jpg(base / "2018 год" / "a.jpg")

    # R04: YYYY.MM
    create_jpg(base / "2020.06 лето" / "b.jpg")

    # R05: YYYY.MM.DD
    create_jpg(base / "2023.05.15 Отпуск" / "c.jpg")
    create_txt(base / "2023.05.15 Отпуск" / "notes.txt", "заметка")

    # R06: YYYY.MM.DD-DD
    create_jpg(base / "2024.01.29-31 НГ" / "d.jpg")

    # R07: YYYY.MM.DD-MM.DD
    create_jpg(base / "2024.06.28-07.05 Поездка" / "e.jpg")

    # R13: дата в имени файла (приоритет)
    create_jpg(base / "2025.03.10 Работа" / "2025.03.12 Встреча.jpg")

    # R14: вложенная подпапка без даты
    create_jpg(base / "2022.12.01 ДР" / "sub" / "deep.jpg")

    # R15: скрытые файлы и папки
    create_txt(base / "2023.05.15 Отпуск" / ".hidden", "secret")
    (base / ".hidden_dir").mkdir(exist_ok=True)
    create_txt(base / ".hidden_dir" / "invisible.txt", "boo")

    # R16: невалидные даты
    create_jpg(base / "2023.02.31 Невалид" / "bad.jpg")
    create_jpg(base / "2024.13.01 Месяц13" / "bad2.jpg")

    # Без даты
    create_jpg(base / "Разное" / "random.jpg")


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_R11_dry_run(base: Path):
    """R11: dry-run НЕ изменяет файлы."""
    print("\n── R11: Dry-run не изменяет файлы ──")
    snap_before = snapshot_tree(base)
    r = run_script(base, "--verbose")
    snap_after = snapshot_tree(base)

    check("R11a", "dry-run завершился успешно", r.returncode == 0)
    check("R11b", "количество файлов не изменилось",
          len(snap_before) == len(snap_after))

    mtimes_changed = [
        k for k in snap_before
        if snap_before[k]["type"] == "file"
        and k in snap_after
        and abs(snap_before[k]["mtime"] - snap_after[k]["mtime"]) > 0.01
    ]
    check("R11c", "mtime ни одного файла не изменился",
          len(mtimes_changed) == 0,
          f"изменённые: {mtimes_changed}")


def test_apply_and_dates(base: Path):
    """R01-R07: apply устанавливает правильные даты."""
    print("\n── R01-R07: Apply устанавливает правильные даты ──")
    r = run_script(base, "--apply")
    check("R00", "--apply завершился без ошибок",
          r.returncode == 0 and "Ошибки:                      0" in r.stdout,
          r.stdout[-300:] if r.stdout else r.stderr)

    expected = {
        # R03: YYYY
        "2018 год/a.jpg":                          datetime(2018, 1, 1, 12, 0, 0),
        # R04: YYYY.MM
        "2020.06 лето/b.jpg":                      datetime(2020, 6, 1, 12, 0, 0),
        # R05: YYYY.MM.DD
        "2023.05.15 Отпуск/c.jpg":                 datetime(2023, 5, 15, 12, 0, 0),
        "2023.05.15 Отпуск/notes.txt":             datetime(2023, 5, 15, 12, 0, 0),
        # R06: YYYY.MM.DD-DD
        "2024.01.29-31 НГ/d.jpg":                  datetime(2024, 1, 29, 12, 0, 0),
        # R07: YYYY.MM.DD-MM.DD
        "2024.06.28-07.05 Поездка/e.jpg":          datetime(2024, 6, 28, 12, 0, 0),
        # R13: дата из файла приоритетнее папки
        "2025.03.10 Работа/2025.03.12 Встреча.jpg": datetime(2025, 3, 12, 12, 0, 0),
        # R14: вложенная подпапка
        "2022.12.01 ДР/sub/deep.jpg":              datetime(2022, 12, 1, 12, 0, 0),
    }

    # R01: проверка mtime
    print("\n── R01: mtime установлен через os.utime() ──")
    for rel, exp_dt in sorted(expected.items()):
        fp = base / rel
        actual = get_mtime(fp)
        diff = abs((actual - exp_dt).total_seconds())
        check("R01", f"mtime {rel}", diff < 1.0,
              f"ожидание={exp_dt}, факт={actual}")

    # R02: проверка EXIF
    print("\n── R02: EXIF DateTimeOriginal записан ──")
    for rel, exp_dt in sorted(expected.items()):
        fp = base / rel
        exif_raw = get_exif_date(fp)
        if rel.endswith(".txt"):
            check("R02", f"EXIF {rel} (txt — нет EXIF, ожидаемо)",
                  exif_raw == "")
        else:
            if exif_raw:
                exif_dt = datetime.strptime(exif_raw[:19], "%Y:%m:%d %H:%M:%S")
                check("R02", f"EXIF {rel}", exif_dt == exp_dt,
                      f"ожидание={exp_dt}, факт={exif_dt}")
            else:
                check("R02", f"EXIF {rel}", False, "EXIF отсутствует")

    # R03-R07: правильность конкретных форматов
    print("\n── R03-R07: Правильность форматов дат ──")
    format_checks = {
        "R03": ("2018 год/a.jpg",                          "2018-01-01"),
        "R04": ("2020.06 лето/b.jpg",                      "2020-06-01"),
        "R05": ("2023.05.15 Отпуск/c.jpg",                 "2023-05-15"),
        "R06": ("2024.01.29-31 НГ/d.jpg",                  "2024-01-29"),
        "R07": ("2024.06.28-07.05 Поездка/e.jpg",          "2024-06-28"),
    }
    for rid, (rel, exp_date_str) in format_checks.items():
        actual = get_mtime(base / rel).strftime("%Y-%m-%d")
        check(rid, f"формат {rel} → {exp_date_str}", actual == exp_date_str,
              f"факт={actual}")

    # R13: приоритет даты файла
    print("\n── R13: Дата из имени файла приоритетнее папки ──")
    fp13 = base / "2025.03.10 Работа" / "2025.03.12 Встреча.jpg"
    actual13 = get_mtime(fp13).strftime("%Y-%m-%d")
    check("R13", "файл 2025.03.12 в папке 2025.03.10 → дата 03.12",
          actual13 == "2025-03-12",
          f"факт={actual13} (ожид: 2025-03-12)")

    # R14: вложенная подпапка
    print("\n── R14: Поднимается по дереву к ближайшей папке с датой ──")
    fp14 = base / "2022.12.01 ДР" / "sub" / "deep.jpg"
    actual14 = get_mtime(fp14).strftime("%Y-%m-%d")
    check("R14", "deep.jpg в подпапке → дата из родительской 2022.12.01",
          actual14 == "2022-12-01",
          f"факт={actual14}")


def test_R12_idempotency(base: Path):
    """R12: повторный запуск пропускает все файлы."""
    print("\n── R12: Идемпотентность (повторный запуск) ──")
    r = run_script(base, "--apply")
    check("R12", "повторный --apply: 0 обработано",
          "Успешно обработано:          0" in r.stdout,
          r.stdout.split("\n")[-10:] if r.stdout else "нет вывода")


def test_R08_R09_R10_safety(base: Path, snap_before: dict):
    """R08-R10: ни один файл/папка не удалён, не переименован, не перемещён."""
    print("\n── R08-R10: Безопасность — ничего не удалено/переименовано/перемещено ──")
    snap_after = snapshot_tree(base)

    before_keys = set(snap_before.keys())
    after_keys = set(snap_after.keys())

    deleted = before_keys - after_keys

    check("R08", "ни один файл/папка не удалён", len(deleted) == 0,
          f"удалены: {sorted(deleted)}")

    # Проверяем, что имена файлов/папок не изменились
    check("R09", "все файлы/папки сохранили свои имена и пути",
          before_keys == after_keys,
          f"пропали: {before_keys - after_keys}, появились: {after_keys - before_keys}")

    n_before_f = len([v for v in snap_before.values() if v["type"] == "file"])
    n_after_f = len([v for v in snap_after.values() if v["type"] == "file"])
    n_before_d = len([v for v in snap_before.values() if v["type"] == "dir"])
    n_after_d = len([v for v in snap_after.values() if v["type"] == "dir"])

    check("R10a", f"количество файлов: {n_before_f} → {n_after_f}",
          n_before_f == n_after_f)
    check("R10b", f"количество папок: {n_before_d} → {n_after_d}",
          n_before_d == n_after_d)


def test_R15_hidden(base: Path):
    """R15: скрытые файлы и папки пропускаются."""
    print("\n── R15: Скрытые файлы и папки пропускаются ──")
    r = run_script(base, "--verbose")

    check("R15a", ".hidden файл не упоминается в выводе",
          ".hidden" not in r.stdout.replace(".hidden_dir", ""))
    check("R15b", ".hidden_dir папка не упоминается в выводе",
          ".hidden_dir" not in r.stdout)

    hidden_fp = base / "2023.05.15 Отпуск" / ".hidden"
    mtime = get_mtime(hidden_fp)
    check("R15c", ".hidden — mtime НЕ изменён на дату папки",
          mtime.year >= 2026,
          f"mtime={mtime}")


def test_R16_invalid_dates(base: Path):
    """R16: невалидные даты отклоняются."""
    print("\n── R16: Невалидные даты отклоняются ──")
    r = run_script(base, "--verbose")

    check("R16a", "2023.02.31 — в списке проблемных папок",
          "2023.02.31 Невалид" in r.stdout)
    check("R16b", "2024.13.01 — в списке проблемных папок",
          "2024.13.01 Месяц13" in r.stdout)

    fp1 = base / "2023.02.31 Невалид" / "bad.jpg"
    fp2 = base / "2024.13.01 Месяц13" / "bad2.jpg"
    check("R16c", "bad.jpg — mtime НЕ изменён", get_mtime(fp1).year >= 2026)
    check("R16d", "bad2.jpg — mtime НЕ изменён", get_mtime(fp2).year >= 2026)


def test_R17_no_original_copies(base: Path):
    """R17: не создаёт *_original копий."""
    print("\n── R17: Не создаёт *_original копий файлов ──")
    originals = []
    for root, dirs, files in os.walk(base):
        for f in files:
            if "_original" in f:
                originals.append(str(Path(root, f).relative_to(base)))

    check("R17", "нет *_original файлов", len(originals) == 0,
          f"найдены: {originals}")


def test_R14b_base_dir_date(tmp_root: Path):
    """R14b-d: дата из корневой директории наследуется подпапками без дат."""
    print("\n── R14b-d: Наследование даты из корневой директории ──")

    # Создаём отдельную структуру, где base_dir сам содержит дату
    base2 = tmp_root / "2019 Архив"
    if base2.exists():
        shutil.rmtree(base2)
    base2.mkdir(parents=True)

    # Подпапка без даты
    create_jpg(base2 / "Разное" / "orphan.jpg")
    # Глубоко вложенная подпапка без даты
    create_jpg(base2 / "без даты" / "sub" / "deep_orphan.jpg")
    # Подпапка с собственной датой (ближайшая должна победить)
    create_jpg(base2 / "2023.05.15 Отпуск" / "pic.jpg")

    snap = snapshot_tree(base2)

    # Apply
    r = run_script(base2, "--apply")
    check("R14b-apply", "--apply на 2019 Архив/ завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    # R14b: файл в подпапке без даты → дата из base_dir
    orphan = base2 / "Разное" / "orphan.jpg"
    actual_b = get_mtime(orphan).strftime("%Y-%m-%d")
    check("R14b", "orphan.jpg в Разное/ → 2019-01-01 (из корневой)",
          actual_b == "2019-01-01",
          f"факт={actual_b}")

    # R14c: файл глубоко во вложенных подпапках без дат → дата из base_dir
    deep = base2 / "без даты" / "sub" / "deep_orphan.jpg"
    actual_c = get_mtime(deep).strftime("%Y-%m-%d")
    check("R14c", "deep_orphan.jpg в без даты/sub/ → 2019-01-01 (из корневой)",
          actual_c == "2019-01-01",
          f"факт={actual_c}")

    # R14d: файл в подпапке с собственной датой → дата из подпапки, НЕ из корневой
    pic = base2 / "2023.05.15 Отпуск" / "pic.jpg"
    actual_d = get_mtime(pic).strftime("%Y-%m-%d")
    check("R14d", "pic.jpg в 2023.05.15 Отпуск/ → 2023-05-15 (ближайшая, не корневая)",
          actual_d == "2023-05-15",
          f"факт={actual_d}")

    # Безопасность: ни один файл не удалён
    snap_after = snapshot_tree(base2)
    before_keys = set(snap.keys())
    after_keys = set(snap_after.keys())
    deleted = before_keys - after_keys
    check("R14b-safety", "ни один файл/папка не удалён",
          len(deleted) == 0,
          f"удалены: {sorted(deleted)}")


def test_R18_dash_dates_not_matched(tmp_root: Path):
    """R18: папки с дефисными датами (2009-09-25) НЕ парсятся как дата."""
    print("\n── R18: Дефисные даты не парсятся, точечные — парсятся ──")

    # Воспроизводим точный сценарий пользователя:
    # 2009 год/2009.09.24-25 КЭС-Баскет/2009-09-25 Йошкар-Ола/_DSC4897.JPG
    base3 = tmp_root / "test_dash_dates"
    if base3.exists():
        shutil.rmtree(base3)

    deep_dir = (base3
                / "2009 год"
                / "2009.09.24-25 КЭС-Баскет 2009"
                / "2009-09-25 Йошкар-Ола День 3")
    create_jpg(deep_dir / "_DSC4897.JPG")

    # Файл в папке только с дефисной датой (не должна парситься)
    create_jpg(base3 / "2009-09-25 Концерт" / "photo.jpg")

    # Файл в папке с точечной датой + дефисной подпапкой
    create_jpg(base3 / "2020.03.15 Поездка" / "2020-03-16 День 2" / "img.jpg")

    r = run_script(base3, "--apply")
    check("R18-apply", "--apply завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    # R18a: главный баг — файл должен получить дату 2009-09-24 (из 2009.09.24-25),
    #        а НЕ 2009-01-01 (из «2009» в имени «2009-09-25 Йошкар-Ола»)
    f1 = deep_dir / "_DSC4897.JPG"
    actual1 = get_mtime(f1).strftime("%Y-%m-%d")
    check("R18a", "_DSC4897.JPG → 2009-09-24 (из 2009.09.24-25, не из 2009-09-25)",
          actual1 == "2009-09-24",
          f"факт={actual1}")

    # R18b: папка с дефисной датой без точечного родителя → без даты (пропускается)
    f2 = base3 / "2009-09-25 Концерт" / "photo.jpg"
    mtime2 = get_mtime(f2)
    check("R18b", "photo.jpg в 2009-09-25 Концерт/ → НЕ обработан (дефис — не дата)",
          mtime2.year >= 2026,
          f"mtime={mtime2}")

    # R18c: точечная дата побеждает, дефисная подпапка пропускается
    f3 = base3 / "2020.03.15 Поездка" / "2020-03-16 День 2" / "img.jpg"
    actual3 = get_mtime(f3).strftime("%Y-%m-%d")
    check("R18c", "img.jpg → 2020-03-15 (из 2020.03.15, не из 2020-03-16)",
          actual3 == "2020-03-15",
          f"факт={actual3}")

    # Безопасность
    snap_after = snapshot_tree(base3)
    n_files = len([v for v in snap_after.values() if v["type"] == "file"])
    check("R18-safety", f"все {n_files} файлов на месте", n_files == 3,
          f"файлов: {n_files}")


def test_R19_date_ranges(tmp_root: Path):
    """R19: диапазоны месяцев и дней в именах папок."""
    print("\n── R19: Диапазоны дат (месяцев и дней) ──")

    base19 = tmp_root / "test_date_ranges"
    if base19.exists():
        shutil.rmtree(base19)

    # R19a: диапазон месяцев  "2018.01-03 зима" → 2018-01-01
    create_jpg(base19 / "2018.01-03 зима" / "winter.jpg")
    # R19b: полная дата + диапазон мес.дней  "2018.01.03-02.05 зима" → 2018-01-03
    create_jpg(base19 / "2018.01.03-02.05 зима" / "trip.jpg")
    # R19c: полная дата + диапазон дней  "2018.01.03-04 зима" → 2018-01-03
    create_jpg(base19 / "2018.01.03-04 зима" / "photo.jpg")

    r = run_script(base19, "--apply")
    check("R19-apply", "--apply завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    f1 = base19 / "2018.01-03 зима" / "winter.jpg"
    actual1 = get_mtime(f1).strftime("%Y-%m-%d")
    check("R19a", "winter.jpg → 2018-01-01 (из 2018.01-03, диапазон месяцев)",
          actual1 == "2018-01-01",
          f"факт={actual1}")

    f2 = base19 / "2018.01.03-02.05 зима" / "trip.jpg"
    actual2 = get_mtime(f2).strftime("%Y-%m-%d")
    check("R19b", "trip.jpg → 2018-01-03 (из 2018.01.03-02.05, начальная дата)",
          actual2 == "2018-01-03",
          f"факт={actual2}")

    f3 = base19 / "2018.01.03-04 зима" / "photo.jpg"
    actual3 = get_mtime(f3).strftime("%Y-%m-%d")
    check("R19c", "photo.jpg → 2018-01-03 (из 2018.01.03-04, диапазон дней)",
          actual3 == "2018-01-03",
          f"факт={actual3}")

    # Безопасность
    snap_after = snapshot_tree(base19)
    n_files = len([v for v in snap_after.values() if v["type"] == "file"])
    check("R19-safety", f"все {n_files} файлов на месте", n_files == 3,
          f"файлов: {n_files}")


def test_R20_compact_dates_rejected(tmp_root: Path):
    """R20: слитные даты (20180101) НЕ воспринимаются как даты."""
    print("\n── R20: Слитные даты (без точек) не парсятся ──")

    base20 = tmp_root / "test_compact_dates"
    if base20.exists():
        shutil.rmtree(base20)

    # Папки со слитными датами — не должны парситься
    create_jpg(base20 / "20180101 Новый год" / "a.jpg")
    create_jpg(base20 / "201806 лето" / "b.jpg")
    create_jpg(base20 / "20180115" / "c.jpg")

    r = run_script(base20, "--apply")
    check("R20-apply", "--apply завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    # Все файлы должны остаться с текущим mtime (не обработаны)
    for rel, fname in [("20180101 Новый год", "a.jpg"),
                       ("201806 лето", "b.jpg"),
                       ("20180115", "c.jpg")]:
        f = base20 / rel / fname
        mtime = get_mtime(f)
        check(f"R20-{fname}", f"{fname} в {rel}/ → НЕ обработан (слитная дата)",
              mtime.year >= 2026,
              f"mtime={mtime}")

    # Безопасность
    snap_after = snapshot_tree(base20)
    n_files = len([v for v in snap_after.values() if v["type"] == "file"])
    check("R20-safety", f"все {n_files} файлов на месте", n_files == 3,
          f"файлов: {n_files}")


def run_script_refine(base: Path, *extra_args) -> subprocess.CompletedProcess:
    """Запускает скрипт с --refine и дополнительными аргументами."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(base), "--refine"] + list(extra_args),
        capture_output=True, text=True, timeout=120,
    )


def test_R21_refine_mode(tmp_root: Path):
    """R21: Режим --refine — уточнение дат из имён файлов."""
    print("\n── R21: Режим --refine — уточнение дат из имён файлов ──")

    base21 = tmp_root / "test_refine"
    if base21.exists():
        shutil.rmtree(base21)

    # ── Case 1: Папка = YYYY ──
    # 1a: год совпадает → берём из файла
    create_jpg(base21 / "2019 год" / "IMG_20190102_160000.jpg")
    # 1b: год не совпадает → оставляем папку
    create_jpg(base21 / "2019 год" / "IMG_20181231_180000.jpg")

    # ── Case 2: Папка = YYYY.MM ──
    # 2a: год+месяц совпадают → берём из файла
    create_jpg(base21 / "2019.01 Зима" / "IMG_20190115_160000.jpg")
    # 2b: месяц не совпадает → оставляем папку
    create_jpg(base21 / "2019.01 Зима" / "IMG_20190215_160000.jpg")

    # ── Case 3: Папка = YYYY.MM.DD ──
    # 3a: полная дата совпадает → берём из файла (добавляет время)
    create_jpg(base21 / "2019.01.02 Событие" / "IMG_20190102_160000.jpg")
    # 3b: день не совпадает → оставляем папку
    create_jpg(base21 / "2019.01.02 Событие" / "IMG_20190103_160000.jpg")

    # ── Case 4: Папка = YYYY.MM.DD-DD (диапазон дней) ──
    # 4a: день в диапазоне → берём из файла
    create_jpg(base21 / "2019.01.02-05 Поездка" / "IMG_20190104_160000.jpg")
    # 4b: день вне диапазона → оставляем папку
    create_jpg(base21 / "2019.01.02-05 Поездка" / "IMG_20190110_160000.jpg")

    # ── Case 5: Папка = YYYY.MM-MM (диапазон месяцев) ──
    # 5a: месяц в диапазоне → берём из файла
    create_jpg(base21 / "2019.01-03 Зима" / "IMG_20190215_160000.jpg")
    # 5b: месяц вне диапазона → оставляем папку
    create_jpg(base21 / "2019.01-03 Зима" / "IMG_20190615_160000.jpg")

    # ── Case 6: Папка = YYYY.MM.DD-MM.DD (кросс-месячный диапазон) ──
    # 6a: дата в диапазоне → берём из файла
    create_jpg(base21 / "2019.06.28-07.05 Поездка" / "IMG_20190702_160000.jpg")
    # 6b: дата вне диапазона → оставляем папку
    create_jpg(base21 / "2019.06.28-07.05 Поездка" / "IMG_20190715_160000.jpg")

    # ── Case 7: Нет даты в имени файла ──
    create_jpg(base21 / "2019.01.02 Событие" / "_DSC4897.JPG")

    # ── Case 8: Нет даты в папке ──
    create_jpg(base21 / "Разное" / "IMG_20190102_160000.jpg")

    snap_before = snapshot_tree(base21)

    # ── Тест readonly: --refine без --apply ──
    print("\n  ── R21 readonly: --refine без --apply ──")
    snap_pre_readonly = snapshot_tree(base21)
    r_ro = run_script_refine(base21)
    snap_post_readonly = snapshot_tree(base21)

    check("R21-ro-exit", "--refine (readonly) завершился без ошибок",
          r_ro.returncode == 0,
          r_ro.stderr if r_ro.stderr else r_ro.stdout[-300:])

    # Проверяем, что ни один mtime не изменился в readonly-режиме
    mtimes_changed = [
        k for k in snap_pre_readonly
        if snap_pre_readonly[k]["type"] == "file"
        and k in snap_post_readonly
        and abs(snap_pre_readonly[k]["mtime"] - snap_post_readonly[k]["mtime"]) > 0.01
    ]
    check("R21-ro-nochg", "readonly-режим не изменяет файлы",
          len(mtimes_changed) == 0,
          f"изменённые: {mtimes_changed}")

    # Проверяем, что вывод содержит [УТОЧНЕНИЕ] и [КОНФЛИКТ]
    check("R21-ro-refine", "вывод содержит [УТОЧНЕНИЕ]",
          "УТОЧНЕНИЕ" in r_ro.stdout,
          r_ro.stdout[-500:])
    check("R21-ro-conflict", "вывод содержит [КОНФЛИКТ]",
          "КОНФЛИКТ" in r_ro.stdout,
          r_ro.stdout[-500:])

    # ── Тест apply: --refine --apply ──
    print("\n  ── R21 apply: --refine --apply ──")
    r_ap = run_script_refine(base21, "--apply")
    check("R21-apply", "--refine --apply завершился без ошибок",
          r_ap.returncode == 0,
          r_ap.stderr if r_ap.stderr else r_ap.stdout[-300:])

    # ── Case 1a: YYYY, год совпадает → дата из файла ──
    f1a = base21 / "2019 год" / "IMG_20190102_160000.jpg"
    m1a = get_mtime(f1a)
    check("R21-1a", "IMG_20190102 в '2019 год' → 2019-01-02 16:00",
          m1a.strftime("%Y-%m-%d %H:%M") == "2019-01-02 16:00",
          f"факт={m1a}")

    # ── Case 1b: YYYY, год не совпадает → дата папки ──
    f1b = base21 / "2019 год" / "IMG_20181231_180000.jpg"
    m1b = get_mtime(f1b)
    check("R21-1b", "IMG_20181231 в '2019 год' → 2019-01-01 12:00 (конфликт)",
          m1b.strftime("%Y-%m-%d %H:%M") == "2019-01-01 12:00",
          f"факт={m1b}")

    # ── Case 2a: YYYY.MM, год+месяц совпадают → дата из файла ──
    f2a = base21 / "2019.01 Зима" / "IMG_20190115_160000.jpg"
    m2a = get_mtime(f2a)
    check("R21-2a", "IMG_20190115 в '2019.01 Зима' → 2019-01-15 16:00",
          m2a.strftime("%Y-%m-%d %H:%M") == "2019-01-15 16:00",
          f"факт={m2a}")

    # ── Case 2b: YYYY.MM, месяц не совпадает → дата папки ──
    f2b = base21 / "2019.01 Зима" / "IMG_20190215_160000.jpg"
    m2b = get_mtime(f2b)
    check("R21-2b", "IMG_20190215 в '2019.01 Зима' → 2019-01-01 12:00 (конфликт)",
          m2b.strftime("%Y-%m-%d %H:%M") == "2019-01-01 12:00",
          f"факт={m2b}")

    # ── Case 3a: YYYY.MM.DD, полная дата совпадает → дата из файла (с временем) ──
    f3a = base21 / "2019.01.02 Событие" / "IMG_20190102_160000.jpg"
    m3a = get_mtime(f3a)
    check("R21-3a", "IMG_20190102 в '2019.01.02 Событие' → 2019-01-02 16:00",
          m3a.strftime("%Y-%m-%d %H:%M") == "2019-01-02 16:00",
          f"факт={m3a}")

    # ── Case 3b: YYYY.MM.DD, день не совпадает → дата папки ──
    f3b = base21 / "2019.01.02 Событие" / "IMG_20190103_160000.jpg"
    m3b = get_mtime(f3b)
    check("R21-3b", "IMG_20190103 в '2019.01.02 Событие' → 2019-01-02 12:00 (конфликт)",
          m3b.strftime("%Y-%m-%d %H:%M") == "2019-01-02 12:00",
          f"факт={m3b}")

    # ── Case 4a: YYYY.MM.DD-DD, день в диапазоне → дата из файла ──
    f4a = base21 / "2019.01.02-05 Поездка" / "IMG_20190104_160000.jpg"
    m4a = get_mtime(f4a)
    check("R21-4a", "IMG_20190104 в '2019.01.02-05' → 2019-01-04 16:00 (в диапазоне)",
          m4a.strftime("%Y-%m-%d %H:%M") == "2019-01-04 16:00",
          f"факт={m4a}")

    # ── Case 4b: YYYY.MM.DD-DD, день вне диапазона → дата папки ──
    f4b = base21 / "2019.01.02-05 Поездка" / "IMG_20190110_160000.jpg"
    m4b = get_mtime(f4b)
    check("R21-4b", "IMG_20190110 в '2019.01.02-05' → 2019-01-02 12:00 (вне диапазона)",
          m4b.strftime("%Y-%m-%d %H:%M") == "2019-01-02 12:00",
          f"факт={m4b}")

    # ── Case 5a: YYYY.MM-MM, месяц в диапазоне → дата из файла ──
    f5a = base21 / "2019.01-03 Зима" / "IMG_20190215_160000.jpg"
    m5a = get_mtime(f5a)
    check("R21-5a", "IMG_20190215 в '2019.01-03' → 2019-02-15 16:00 (в диапазоне месяцев)",
          m5a.strftime("%Y-%m-%d %H:%M") == "2019-02-15 16:00",
          f"факт={m5a}")

    # ── Case 5b: YYYY.MM-MM, месяц вне диапазона → дата папки ──
    f5b = base21 / "2019.01-03 Зима" / "IMG_20190615_160000.jpg"
    m5b = get_mtime(f5b)
    check("R21-5b", "IMG_20190615 в '2019.01-03' → 2019-01-01 12:00 (вне диапазона)",
          m5b.strftime("%Y-%m-%d %H:%M") == "2019-01-01 12:00",
          f"факт={m5b}")

    # ── Case 6a: YYYY.MM.DD-MM.DD, дата в кросс-месячном диапазоне → дата из файла ──
    f6a = base21 / "2019.06.28-07.05 Поездка" / "IMG_20190702_160000.jpg"
    m6a = get_mtime(f6a)
    check("R21-6a", "IMG_20190702 в '2019.06.28-07.05' → 2019-07-02 16:00 (в диапазоне)",
          m6a.strftime("%Y-%m-%d %H:%M") == "2019-07-02 16:00",
          f"факт={m6a}")

    # ── Case 6b: YYYY.MM.DD-MM.DD, дата вне кросс-месячного диапазона → дата папки ──
    f6b = base21 / "2019.06.28-07.05 Поездка" / "IMG_20190715_160000.jpg"
    m6b = get_mtime(f6b)
    check("R21-6b", "IMG_20190715 в '2019.06.28-07.05' → 2019-06-28 12:00 (вне диапазона)",
          m6b.strftime("%Y-%m-%d %H:%M") == "2019-06-28 12:00",
          f"факт={m6b}")

    # ── Case 7: Нет даты в имени файла → дата папки ──
    f7 = base21 / "2019.01.02 Событие" / "_DSC4897.JPG"
    m7 = get_mtime(f7)
    check("R21-7", "_DSC4897.JPG в '2019.01.02' → 2019-01-02 12:00 (нет даты в имени)",
          m7.strftime("%Y-%m-%d %H:%M") == "2019-01-02 12:00",
          f"факт={m7}")

    # ── Case 8: Нет даты в папке → не обработан ──
    f8 = base21 / "Разное" / "IMG_20190102_160000.jpg"
    m8 = get_mtime(f8)
    check("R21-8", "IMG_20190102 в 'Разное' → НЕ обработан (нет даты в папке)",
          m8.year >= 2026,
          f"mtime={m8}")

    # ── Безопасность: ни один файл не удалён/перемещён ──
    snap_after = snapshot_tree(base21)
    before_keys = set(snap_before.keys())
    after_keys = set(snap_after.keys())
    deleted = before_keys - after_keys
    check("R21-safety", "ни один файл/папка не удалён",
          len(deleted) == 0,
          f"удалены: {sorted(deleted)}")

    n_files = len([v for v in snap_after.values() if v["type"] == "file"])
    check("R21-count", f"все {n_files} файлов на месте",
          n_files == 14,
          f"файлов: {n_files}")

    # ── Идемпотентность: повторный --refine --apply ──
    print("\n  ── R21 идемпотентность ──")
    r_idem = run_script_refine(base21, "--apply")
    check("R21-idem", "повторный --refine --apply: 0 обработано",
          "Успешно обработано:          0" in r_idem.stdout,
          r_idem.stdout[-300:] if r_idem.stdout else "нет вывода")


def test_R21_refine_no_utoch_when_date_already_set(tmp_root: Path):
    """R21: --refine не выводит [УТОЧНЕНИЕ], если дата из имени файла уже установлена в файле."""
    print("\n── R21: --refine без вывода при совпадении даты ──")

    base = tmp_root / "test_refine_no_utoch"
    if base.exists():
        shutil.rmtree(base)

    # Один файл: дата в папке и в имени совпадают, предпочтительна дата из имени
    folder = base / "2019.01.02 Событие"
    f = folder / "IMG_20190102_160000.jpg"
    create_jpg(f)
    # Устанавливаем в файле ту же дату, что в имени (2019-01-02 16:00:00)
    set_file_date(f, datetime(2019, 1, 2, 16, 0, 0))

    r = run_script_refine(base)

    check("R21-no-utoch-exit", "--refine завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-300:])

    # Для этого файла расхождение не выводится — в выводе не должно быть [УТОЧНЕНИЕ]
    check("R21-no-utoch-hide", "при совпадении даты [УТОЧНЕНИЕ] не выводится",
          "[УТОЧНЕНИЕ]" not in r.stdout,
          r.stdout[-500:] if r.stdout else "нет вывода")

    # В итогах должна быть строка «Дата уже совпадает: 1»
    check("R21-no-utoch-stats", "в итогах «Дата уже совпадает: 1»",
          bool(re.search(r"Дата уже совпадает:\s*1\b", r.stdout)),
          r.stdout[-400:] if r.stdout else "нет вывода")


def test_R22_unit_date_comparison():
    """R22-unit: _mtime_date_matches сравнивает только дату (год-месяц-день), игнорируя время."""
    print("\n── R22-unit: _mtime_date_matches (без exiftool) ──")

    import sys, tempfile, os
    sys.path.insert(0, str(SCRIPT.parent))
    from set_dates_from_folders import _mtime_date_matches

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        tmp_path = Path(tmp.name)

    try:
        folder_date = datetime(2023, 5, 15, 12, 0, 0)   # дата из папки (полдень)
        file_time   = datetime(2023, 5, 15, 16, 30, 0)  # время уже есть в файле

        os.utime(tmp_path, (file_time.timestamp(), file_time.timestamp()))

        # Одна и та же дата, разное время → должна совпадать
        result = _mtime_date_matches(tmp_path, folder_date)
        check("R22-unit-same-day",
              "_mtime_date_matches: тот же день (16:30 vs 12:00) → True",
              result == True,
              f"вернула {result!r}")

        # Другой день → не должна совпадать
        other_date = datetime(2023, 5, 14, 12, 0, 0)
        result2 = _mtime_date_matches(tmp_path, other_date)
        check("R22-unit-diff-day",
              "_mtime_date_matches: другой день (15 vs 14) → False",
              result2 == False,
              f"вернула {result2!r}")
    finally:
        tmp_path.unlink(missing_ok=True)


def test_R22_preserve_time_when_date_matches(tmp_root: Path):
    """R22: если у файла уже установлена ДАТА из папки (день совпадает),
    то ВРЕМЯ файла не должно быть перезатёрто при применении даты из папки.

    Сценарий:
      - папка: 2023.05.15 Отпуск → дата = 2023-05-15 12:00:00 (полдень по умолчанию)
      - файл уже имеет mtime/EXIF = 2023-05-15 16:30:00 (совпадает ДАТА, не ВРЕМЯ)
      Ожидание: скрипт не перезатирает 16:30:00, файл остаётся без изменений.
    """
    print("\n── R22: Время файла сохраняется, если дата из папки уже совпадает ──")

    base22 = tmp_root / "test_R22_preserve_time"
    if base22.exists():
        shutil.rmtree(base22)

    folder = base22 / "2023.05.15 Отпуск"
    f = folder / "photo.jpg"
    create_jpg(f)

    # Устанавливаем в файле ту же ДАТУ (2023-05-15), но другое ВРЕМЯ (16:30:00)
    existing_time = datetime(2023, 5, 15, 16, 30, 0)
    set_file_date(f, existing_time)

    mtime_before = get_mtime(f)
    exif_before = get_exif_date(f)

    # Запускаем скрипт: дата папки — 2023-05-15 (12:00 по умолчанию),
    # файл уже несёт 2023-05-15 — только время отличается.
    r = run_script(base22, "--apply")
    check("R22-apply", "--apply завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    actual_mtime = get_mtime(f)
    actual_exif = get_exif_date(f)

    # Ключевая проверка: mtime НЕ перезатёрт на 12:00
    check("R22-mtime-preserved",
          "mtime файла сохранён (16:30:00 не заменён на 12:00:00 из папки)",
          actual_mtime.strftime("%Y-%m-%d %H:%M:%S") == "2023-05-15 16:30:00",
          f"до={mtime_before.strftime('%Y-%m-%d %H:%M:%S')}, "
          f"после={actual_mtime.strftime('%Y-%m-%d %H:%M:%S')}")

    # EXIF тоже не должен быть перезатёрт
    check("R22-exif-preserved",
          "EXIF DateTimeOriginal сохранён (16:30:00 не заменён на 12:00:00)",
          actual_exif == exif_before,
          f"до={exif_before!r}, после={actual_exif!r}")

    # Дата (день) при этом должна оставаться корректной
    check("R22-date-correct",
          "дата файла соответствует папке (2023-05-15)",
          actual_mtime.strftime("%Y-%m-%d") == "2023-05-15",
          f"факт={actual_mtime.strftime('%Y-%m-%d')}")


def test_R23_creation_date_set(tmp_root: Path):
    """R23: скрипт записывает CreationDate (приоритет #4 в Immich) для HEIC/MOV."""
    print("\n── R23: CreationDate выставлен (для HEIC/QuickTime) ──")

    base23 = tmp_root / "test_R23_creation_date"
    if base23.exists():
        shutil.rmtree(base23)

    folder = base23 / "2024.03.15 Фото"
    f = folder / "photo.jpg"
    create_jpg(f)

    r = run_script(base23, "--apply")
    check("R23-apply", "--apply завершился без ошибок",
          r.returncode == 0,
          r.stderr if r.stderr else r.stdout[-200:])

    expected_dt = datetime(2024, 3, 15, 12, 0, 0)
    expected_str = expected_dt.strftime("%Y:%m:%d %H:%M:%S")

    # Проверяем CreationDate
    raw = get_exif_field(f, "CreationDate")
    check("R23-creation-date",
          f"CreationDate выставлен в {expected_str}",
          raw[:19] == expected_str if raw else False,
          f"факт={raw!r}")

    # Проверяем DateTimeOriginal — должен тоже быть выставлен
    raw_dto = get_exif_date(f)
    check("R23-dto",
          f"DateTimeOriginal выставлен в {expected_str}",
          raw_dto[:19] == expected_str if raw_dto else False,
          f"факт={raw_dto!r}")

    # Проверяем CreateDate
    raw_cd = get_exif_field(f, "CreateDate")
    check("R23-create-date",
          f"CreateDate выставлен в {expected_str}",
          raw_cd[:19] == expected_str if raw_cd else False,
          f"факт={raw_cd!r}")


def test_static_analysis():
    """Статический анализ: нет опасных операций в коде."""
    print("\n── Статический анализ: нет опасных операций ──")
    import ast

    source = SCRIPT.read_text(encoding='utf-8')
    tree = ast.parse(source)

    dangerous = {"remove", "unlink", "rmdir", "rmtree", "rename",
                 "replace", "move", "send2trash"}
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in dangerous:
                findings.append(f"line {node.lineno}: .{node.func.attr}()")

    check("STATIC", "нет вызовов remove/unlink/rmdir/rename/move/...",
          len(findings) == 0, f"найдено: {findings}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  ТЕСТИРОВАНИЕ ТРЕБОВАНИЙ set_dates_from_folders")
    print("=" * 60)

    if not SCRIPT.exists():
        print(f"ОШИБКА: скрипт не найден: {SCRIPT}")
        sys.exit(1)

    # Создаём тестовую директорию во временной папке
    tmp_root = Path(tempfile.mkdtemp(prefix="test_set_dates_"))
    base = tmp_root / "test_photos"

    try:
        setup(base)
        snap_before = snapshot_tree(base)
        n_files = len([v for v in snap_before.values() if v["type"] == "file"])
        n_dirs = len([v for v in snap_before.values() if v["type"] == "dir"])
        print(f"\nТестовая директория: {base}")
        print(f"Создана структура: {n_files} файлов, {n_dirs} папок")

        # Тесты, не требующие exiftool
        test_static_analysis()
        test_R22_unit_date_comparison()

        if not HAS_EXIFTOOL:
            print("\n  [SKIP] exiftool не найден — интеграционные тесты пропущены.")
            print("         Установите exiftool и запустите снова для полной проверки.")
        else:
            test_R11_dry_run(base)
            test_R16_invalid_dates(base)
            test_R15_hidden(base)
            test_apply_and_dates(base)
            test_R12_idempotency(base)
            test_R17_no_original_copies(base)
            test_R08_R09_R10_safety(base, snap_before)
            test_R14b_base_dir_date(tmp_root)
            test_R18_dash_dates_not_matched(tmp_root)
            test_R19_date_ranges(tmp_root)
            test_R20_compact_dates_rejected(tmp_root)
            test_R21_refine_mode(tmp_root)
            test_R21_refine_no_utoch_when_date_already_set(tmp_root)
            test_R22_preserve_time_when_date_matches(tmp_root)
            test_R23_creation_date_set(tmp_root)

    finally:
        # Очистка временной директории
        shutil.rmtree(tmp_root, ignore_errors=True)

    # Итого
    print("\n" + "=" * 60)
    print(f"  ИТОГО: {passed} PASS / {failed} FAIL / {total} всего")
    if failed == 0:
        print("  ✓ ВСЕ ТРЕБОВАНИЯ ВЫПОЛНЕНЫ")
    else:
        print("  ✗ ЕСТЬ НЕВЫПОЛНЕННЫЕ ТРЕБОВАНИЯ")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
