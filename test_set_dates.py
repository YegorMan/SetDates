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

SCRIPT = Path(__file__).parent / "set_dates_from_folders (4).py"

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
    r = subprocess.run(
        ["exiftool", "-s3", "-DateTimeOriginal", str(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def get_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


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


def test_static_analysis():
    """Статический анализ: нет опасных операций в коде."""
    print("\n── Статический анализ: нет опасных операций ──")
    import ast

    source = SCRIPT.read_text()
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

        # Все тесты
        test_static_analysis()
        test_R11_dry_run(base)
        test_R16_invalid_dates(base)
        test_R15_hidden(base)
        test_apply_and_dates(base)
        test_R12_idempotency(base)
        test_R17_no_original_copies(base)
        test_R08_R09_R10_safety(base, snap_before)

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
