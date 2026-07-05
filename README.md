# NMS Station — индексатор деталей No Man's Sky

Указываешь папку `PCBANKS` игры — программа сама читает .pak-архивы,
расшифровывает таблицы, проходит все связи каждой строительной детали
(паспорт → стили → сцена → геометрия → материалы → текстуры → снап-точки →
габарит → иконка) и выдаёт готовую базу + галерею иконок. Всё берётся
1:1 из файлов игры.

## Возможности

- **Индексация из сырых паков**: оглавления всех .pak (HGPAK, zstd и несжатые),
  при дублях путей приоритет precache-паку; извлечение и декод только нужного.
- **База связей** `parts_links.json`: ~2000 деталей — паспорт из objectstable
  (меню, палитры, флаги, семьи, электрика), стили из partstable, разбор сцен
  (меши LOD0 + материалы, REFERENCE-композиты, снап-локаторы, коллизии),
  материалы (класс, флаги `_F`, сэмплеры-текстуры), хуллы MagicData.
- **Иконки**: все PNG-иконки деталей достаются из паков автоматически
  (таблицы продуктов → DDS → PNG).
- **Окно**: живой лог, галерея иконок под номерами (клик = пути детали),
  паспорт детали по ObjectID, перетаскиваемый разделитель,
  6 языков интерфейса (RU/EN/DE/FR/ZH/ES).
- Повторные запуски **инкрементальны**: готовое не переделывается,
  частичные прогоны сливаются с существующей базой.

## Запуск

Из исходников (Python 3.10+, `pip install pillow zstandard`):

```
python nms_station.py
```

Или собери один exe (см. `build_exe.bat`, нужен `pip install pyinstaller`) —
получится `NMS_Station.exe`, которому нужен только лежащий рядом
`MBINCompiler.exe`.

## Требования

- Windows 10/11.
- [MBINCompiler](https://github.com/monkeyman192/MBINCompiler) (декодер .MBIN
  от monkeyman192) — положи `MBINCompiler.exe` рядом или укажи путь в окне.
  Рекомендуется свежая версия (проверено с 6.45.0-pre1).
- 2–3 ГБ свободного места под извлечённые файлы.

## Консольный режим

```
python nms_indexer.py <PCBANKS> --out <папка> [--limit N] [--tables-only] [--deep N] [--rescan]
```

Результат: `parts_links.json`, `REPORT.txt`, `icons\`, `raw\`.

---

# NMS Station — No Man's Sky base-part indexer (EN)

Point it at the game's `PCBANKS` folder — it reads the .pak archives, decodes
the tables with MBINCompiler, walks every base-building part's chain
(entry → styles → scene → geometry → materials → textures → snap points →
bounds → icon) and produces a JSON database plus an icon gallery. Everything
comes 1:1 from the game files.

- GUI with live log, numbered icon gallery (click = part paths), part
  passport by ObjectID, draggable splitter, 6 UI languages.
- Incremental re-runs; partial runs merge into the existing database.
- Requires Windows and [MBINCompiler](https://github.com/monkeyman192/MBINCompiler)
  next to the exe. From source: Python 3.10+, `pip install pillow zstandard`.

## Заметка для разработки

Рабочие копии скриптов конвейера живут в
`NMS_EXTRACT\10_TOOLS\meshwork\` (машина разработки); этот репозиторий —
канонический источник программы. После правок в meshwork копировать сюда
и коммитить. `nms_lookup.py` содержит пути дампов машины разработки —
на чужих машинах Станция строит паспорт из `parts_links.json` сама.
