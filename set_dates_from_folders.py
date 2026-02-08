#!/usr/bin/env python3
"""
Скрипт для установки дат файлов на основе имён папок.

Что делает скрипт:
  1. Устанавливает даты файловой системы (mtime/atime) через os.utime()
     — это те даты, которые показывает stat и ls -l.
  2. Если установлен exiftool — также записывает EXIF-метаданные
     (DateTimeOriginal, CreateDate, ModifyDate).

ВАЖНО (Linux):
  Дата создания (birth time / crtime) на Linux НЕ МОЖЕТ быть изменена
  стандартными средствами — это ограничение ядра и файловой системы.
  Скрипт устанавливает mtime (Modify) и atime (Access), которые
  отображаются в stat, ls -l и большинстве файловых менеджеров.

Поддерживаемые форматы имён папок:
  YYYY Описание                      → дата: YYYY-01-01 (1 января)
  YYYY.MM Описание                   → дата: YYYY-MM-01 (1-е число месяца)
  YYYY.MM.DD Описание                → дата: YYYY-MM-DD
  YYYY.MM.DD-DD Описание             → дата: YYYY-MM-DD (начало диапазона)
  YYYY.MM.DD-MM.DD Описание          → дата: YYYY-MM-DD (начало диапазона)

ГАРАНТИИ БЕЗОПАСНОСТИ:
  ✓ Скрипт НИКОГДА не удаляет файлы
  ✓ Скрипт НИКОГДА не переименовывает файлы и папки
  ✓ Скрипт НИКОГДА не перемещает файлы и папки
  ✓ Единственное изменение — даты файловой системы и EXIF-метаданные
    (только с флагом --apply)
  ✓ По умолчанию работает в режиме dry-run (только показывает, что будет сделано)

Требования:
  - Python 3.8+
  - exiftool (https://exiftool.org/)

Примеры:
  python3 set_dates_from_folders.py /path/to/photos              # dry-run
  python3 set_dates_from_folders.py /path/to/photos --apply       # применить
  python3 set_dates_from_folders.py /path/to/photos --apply -v

Поведение:
  Если даты файла уже совпадают с датой из папки — файл пропускается.
"""

import os
import re
import sys
import subprocess
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# ─── Настройки ───────────────────────────────────────────────────────────────

# Регулярное выражение: ищем дату в начале имени папки.
# Поддерживаемые форматы:
#   2018 год              → только год (месяц=01, день=01)
#   2018.01 зима          → год + месяц (день=01)
#   2026.01.01 ...        → полная дата
#   2026.01.29-31 ...     → диапазон дней (берём начало)
#   2026.01.28-02.11 ...  → диапазон мес.дней (берём начало)
# Мы всегда берём ПЕРВУЮ (начальную) дату.
# Lookahead (?=[\s\-]|$) гарантирует, что после даты идёт
# пробел, дефис или конец строки (а не продолжение цифр/точек).
DATE_PATTERN = re.compile(r'^(\d{4})(?:\.(\d{2})(?:\.(\d{2}))?)?(?=[\s\-]|$)')

# ─── Функции ─────────────────────────────────────────────────────────────────


def extract_date_from_name(name: str) -> Optional[datetime]:
    """
    Извлекает начальную дату из строки (имени папки или файла).

    Поддерживаемые форматы:
      YYYY           → 1 января указанного года
      YYYY.MM        → 1-е число указанного месяца
      YYYY.MM.DD     → конкретный день

    Возвращает datetime или None, если дата не найдена или невалидна.
    Время устанавливается в 12:00:00 (полдень), чтобы избежать
    проблем с часовыми поясами при сдвиге на ±несколько часов.
    """
    match = DATE_PATTERN.match(name)
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    day = int(match.group(3)) if match.group(3) else 1

    # Валидация через datetime (проверяет високосные годы, кол-во дней и т.д.)
    try:
        return datetime(year, month, day, 12, 0, 0)
    except ValueError:
        return None


def find_date_for_file(file_path: Path, base_dir: Path) -> Tuple[Optional[datetime], Optional[str], str]:
    """
    Определяет дату для файла.

    Приоритет:
      1. Дата из имени файла (без расширения)
      2. Дата из ближайшей родительской папки с датой

    Возвращает (datetime, источник_имя, источник_тип) или (None, None, "").
    источник_тип: "file" или "folder".
    """
    # 1. Пробуем имя файла (без расширения)
    file_stem = file_path.stem
    date = extract_date_from_name(file_stem)
    if date is not None:
        return date, file_stem, "file"

    # 2. Поднимаемся по дереву каталогов
    current = file_path.parent.resolve()
    base = base_dir.resolve()

    while current != base and current != current.parent:
        date = extract_date_from_name(current.name)
        if date is not None:
            return date, current.name, "folder"
        current = current.parent

    return None, None, ""


def find_files(base_dir: Path) -> list:
    """
    Рекурсивно находит все файлы в директории.
    Пропускает скрытые файлы и директории.
    Возвращает список Path-объектов.
    """
    result = []
    for root, dirs, files in os.walk(base_dir):
        # Пропускаем скрытые директории
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for filename in sorted(files):
            if filename.startswith('.'):
                continue
            result.append(Path(root) / filename)
    return result


def check_exiftool() -> bool:
    """Проверяет доступность exiftool."""
    try:
        result = subprocess.run(
            ['exiftool', '-ver'],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def read_exif_date(file_path: Path) -> Optional[datetime]:
    """
    Читает DateTimeOriginal из EXIF-метаданных файла с помощью exiftool.

    Возвращает datetime или None, если дата отсутствует или не читается.
    """
    cmd = [
        'exiftool',
        '-s3',                  # только значение, без имени тега
        '-DateTimeOriginal',
        str(file_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return None

        raw = result.stdout.strip()
        if not raw:
            return None

        # exiftool возвращает дату в формате "YYYY:MM:DD HH:MM:SS"
        # Иногда с таймзоной: "YYYY:MM:DD HH:MM:SS+03:00"
        # Берём только первые 19 символов (без таймзоны)
        date_part = raw[:19]
        return datetime.strptime(date_part, '%Y:%m:%d %H:%M:%S')

    except (subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def set_exif_date(file_path: Path, date: datetime) -> Tuple[bool, str]:
    """
    Записывает дату в EXIF-метаданные файла с помощью exiftool.

    Возвращает (success: bool, message: str).
    """
    date_str = date.strftime('%Y:%m:%d %H:%M:%S')

    cmd = ['exiftool', '-overwrite_original']

    # Устанавливаем все основные теги дат
    cmd.extend([
        f'-DateTimeOriginal={date_str}',
        f'-CreateDate={date_str}',
        f'-ModifyDate={date_str}',
        # Для видеофайлов QuickTime — также запишем в Track/Media
        f'-Track:CreateDate={date_str}',
        f'-Track:ModifyDate={date_str}',
        f'-Media:CreateDate={date_str}',
        f'-Media:ModifyDate={date_str}',
        '-api', 'QuickTimeUTC',    # Видео QuickTime хранят время в UTC
        '-m',                        # Игнорировать незначительные ошибки формата
        str(file_path)
    ])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            # exiftool выводит "1 image files updated" при успехе
            if 'updated' in stdout.lower() or 'image files read' in stdout.lower():
                return True, stdout
            # Иногда exiftool завершается с 0 но пишет warning
            return True, stdout or "OK"
        else:
            return False, f"returncode={result.returncode}, stderr={stderr}, stdout={stdout}"

    except subprocess.TimeoutExpired:
        return False, "Timeout (60s)"
    except Exception as e:
        return False, str(e)


def set_filesystem_dates(file_path: Path, date: datetime) -> Tuple[bool, str]:
    """
    Устанавливает дату модификации (mtime) и доступа (atime) файла.

    Это те даты, которые показывает команда stat (Modify / Access)
    и ls -l (Modify).

    На Linux дата создания (birth time / crtime) не может быть изменена
    стандартными средствами — это ограничение ядра и файловой системы.

    БЕЗОПАСНОСТЬ: функция НЕ удаляет, НЕ переименовывает и НЕ перемещает файлы.
    """
    try:
        timestamp = date.timestamp()
        os.utime(str(file_path), (timestamp, timestamp))
        return True, "OK"
    except OSError as e:
        return False, str(e)


class ExifToolBatch:
    """
    Пакетный режим exiftool через -stay_open.

    Вместо запуска нового процесса Perl на каждый файл (~1-2 сек overhead),
    держит один процесс exiftool и отправляет ему команды через stdin.
    Ускорение: 10-50x на больших коллекциях файлов.

    БЕЗОПАСНОСТЬ: класс НЕ удаляет, НЕ переименовывает и НЕ перемещает файлы.
    """

    def __init__(self):
        self._counter = 0
        self._process = subprocess.Popen(
            ['exiftool', '-stay_open', 'True', '-@', '-'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def close(self):
        """Корректно завершает процесс exiftool."""
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.write('-stay_open\nFalse\n')
                self._process.stdin.flush()
                self._process.wait(timeout=15)
            except Exception:
                self._process.kill()
                self._process.wait()
        self._process = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _execute(self, *args) -> str:
        """Отправляет команду exiftool и ждёт ответа ({readyN} sentinel)."""
        self._counter += 1
        sentinel = f'{{ready{self._counter}}}'

        for arg in args:
            self._process.stdin.write(arg + '\n')
        self._process.stdin.write(f'-execute{self._counter}\n')
        self._process.stdin.flush()

        output = []
        while True:
            line = self._process.stdout.readline()
            if not line:  # EOF — процесс завершился
                break
            if sentinel in line:
                break
            output.append(line)

        return ''.join(output)

    def read_date(self, file_path: Path) -> Optional[datetime]:
        """Читает DateTimeOriginal из EXIF."""
        raw = self._execute('-s3', '-DateTimeOriginal', str(file_path)).strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw[:19], '%Y:%m:%d %H:%M:%S')
        except (ValueError, IndexError):
            return None

    def set_date(self, file_path: Path, date: datetime) -> Tuple[bool, str]:
        """Записывает даты в EXIF-метаданные файла."""
        date_str = date.strftime('%Y:%m:%d %H:%M:%S')

        args = ['-overwrite_original']
        args.extend([
            f'-DateTimeOriginal={date_str}',
            f'-CreateDate={date_str}',
            f'-ModifyDate={date_str}',
            f'-Track:CreateDate={date_str}',
            f'-Track:ModifyDate={date_str}',
            f'-Media:CreateDate={date_str}',
            f'-Media:ModifyDate={date_str}',
            '-api', 'QuickTimeUTC',
            '-m',
            str(file_path)
        ])

        result = self._execute(*args).strip()

        if 'updated' in result.lower():
            return True, result
        return True, result or "OK"


def check_filesystem_date(file_path: Path, target_date: datetime) -> bool:
    """
    Проверяет, совпадает ли mtime файла с целевой датой (с точностью до 1 сек).
    """
    try:
        current_mtime = file_path.stat().st_mtime
        target_ts = target_date.timestamp()
        return abs(current_mtime - target_ts) < 1.0
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Устанавливает даты файлов (mtime/atime + EXIF) на основе имён папок.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  %(prog)s /photos                    # показать, что будет сделано (dry-run)
  %(prog)s /photos --apply            # применить изменения
  %(prog)s /photos --apply -v            # применить с подробным выводом
        """
    )
    parser.add_argument(
        'directory',
        help='Корневая директория с папками фотографий'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        default=False,
        help='Применить изменения (по умолчанию — dry-run, только показ)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        default=False,
        help='Подробный вывод'
    )

    args = parser.parse_args()

    # ─── Настройка логирования ────────────────────────────────────────────
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(message)s',
        stream=sys.stdout
    )
    log = logging.getLogger('set_dates')

    # ─── Валидация ────────────────────────────────────────────────────────
    base_dir = Path(args.directory).resolve()

    if not base_dir.exists():
        log.error(f"ОШИБКА: Директория не существует: {base_dir}")
        sys.exit(1)

    if not base_dir.is_dir():
        log.error(f"ОШИБКА: Не является директорией: {base_dir}")
        sys.exit(1)

    has_exiftool = check_exiftool()

    if not has_exiftool:
        log.error("ОШИБКА: exiftool не найден. Установите его:")
        log.error("  Ubuntu/Debian: sudo apt install libimage-exiftool-perl")
        log.error("  macOS:         brew install exiftool")
        log.error("  Windows:       https://exiftool.org/")
        sys.exit(1)

    # ─── Режим работы ─────────────────────────────────────────────────────
    if args.apply:
        mode_str = "ПРИМЕНЕНИЕ ИЗМЕНЕНИЙ"
    else:
        mode_str = "DRY-RUN (только показ, файлы НЕ изменяются)"

    log.info("=" * 70)
    log.info(f"  Режим: {mode_str}")
    log.info(f"  Директория: {base_dir}")
    log.info("=" * 70)
    log.info("")

    # ─── Поиск файлов ─────────────────────────────────────────────────────
    log.info("Сканирование файлов...")
    all_files = find_files(base_dir)
    log.info(f"Найдено файлов: {len(all_files)}")
    log.info("")

    # ─── Пакетный exiftool (один процесс на весь прогон) ──────────────────
    exif = ExifToolBatch() if has_exiftool else None

    # ─── Обработка ────────────────────────────────────────────────────────
    stats = {
        'total': len(all_files),
        'with_date': 0,
        'without_date': 0,
        'skipped_match': 0,
        'success': 0,
        'failed': 0,
        'skipped_dry_run': 0,
    }

    undated_folders = set()

    for file_path in all_files:
        rel_path = file_path.relative_to(base_dir)
        date, source_name, source_type = find_date_for_file(file_path, base_dir)

        if date is None:
            stats['without_date'] += 1
            # Запоминаем папку (относительный путь) для итогового отчёта
            rel_folder = file_path.parent.relative_to(base_dir)
            undated_folders.add(str(rel_folder))
            log.debug(f"  ПРОПУСК (нет даты в пути): {rel_path}")
            continue

        stats['with_date'] += 1
        date_display = date.strftime('%Y-%m-%d %H:%M:%S')
        source_label = "Файл" if source_type == "file" else "Папка"

        # Проверяем текущие даты: EXIF и файловую систему
        existing_exif = exif.read_date(file_path) if exif else None
        fs_matches = check_filesystem_date(file_path, date)

        # EXIF требует обновления, только если он УЖЕ ЕСТЬ, но не совпадает.
        # Если EXIF отсутствует — файл может не поддерживать его (txt, pdf, ...),
        # и повторная попытка записи бессмысленна.
        exif_needs_update = existing_exif is not None and existing_exif != date

        # Пропускаем, если mtime совпадает и EXIF не требует обновления
        if fs_matches and not exif_needs_update:
            stats['skipped_match'] += 1
            log.info(f"  ⏭ {rel_path}")
            log.info(f"            Дата уже установлена: {date_display}")
            log.info("")
            continue

        if not args.apply:
            stats['skipped_dry_run'] += 1
            log.info(f"  [DRY-RUN] {rel_path}")
            log.info(f"            {source_label}: {source_name}")
            if existing_exif is not None:
                log.info(f"            EXIF:  {existing_exif.strftime('%Y-%m-%d %H:%M:%S')}")
            try:
                mtime_dt = datetime.fromtimestamp(file_path.stat().st_mtime)
                log.info(f"            mtime: {mtime_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            except OSError:
                pass
            log.info(f"            Будет: {date_display}")
            log.info("")
        else:
            # 1. EXIF-метаданные (если exiftool доступен)
            exif_ok = True
            if exif:
                exif_ok, exif_msg = exif.set_date(file_path, date)
                if not exif_ok:
                    log.debug(f"    EXIF не записан: {exif_msg}")

            # 2. Даты файловой системы (mtime/atime) — ВСЕГДА
            #    Вызываем ПОСЛЕ exiftool, т.к. exiftool меняет mtime на «сейчас»
            fs_ok, fs_msg = set_filesystem_dates(file_path, date)

            if fs_ok:
                stats['success'] += 1
                extra = ""
                if has_exiftool and not exif_ok:
                    extra = "  (EXIF не записан)"
                elif not has_exiftool:
                    extra = "  (только mtime/atime)"
                log.info(f"  ✓ {rel_path}  →  {date_display}{extra}")
            else:
                stats['failed'] += 1
                log.error(f"  ✗ {rel_path}  →  ОШИБКА: {fs_msg}")

    # ─── Завершаем exiftool ───────────────────────────────────────────────
    if exif:
        exif.close()

    # ─── Итоги ────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 70)
    log.info("  ИТОГИ:")
    log.info(f"    Всего файлов:                {stats['total']}")
    log.info(f"    С датой из папки:            {stats['with_date']}")
    log.info(f"    Без даты (пропущены):        {stats['without_date']}")
    log.info(f"    Дата уже совпадает:          {stats['skipped_match']}")

    if args.apply:
        log.info(f"    Успешно обработано:          {stats['success']}")
        log.info(f"    Ошибки:                      {stats['failed']}")
    else:
        log.info(f"    Будет обработано (dry-run):   {stats['skipped_dry_run']}")
        log.info("")
        log.info("  Запустите с --apply, чтобы применить изменения.")

    if undated_folders:
        log.info("")
        log.info("  Папки, из которых не удалось извлечь дату:")
        for folder in sorted(undated_folders):
            log.info(f"    • {folder}")

    log.info("=" * 70)

    return 0 if stats['failed'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
