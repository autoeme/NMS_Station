# -*- coding: utf-8 -*-
"""
nms_station.py — графическая оболочка (в духе NMS Modding Station) над нашими
инструментами. Указываешь пути сверху, жмёшь кнопку — внизу в окне бежит весь
ход работы и итоговый результат. Справа — иконка детали и галерея всех иконок.

Запуск:  python nms_station.py          (или собранный NMS_Station.exe)
Языки интерфейса: русский / English / Deutsch / Français / 中文 / Español
(переключатель сверху справа, выбор запоминается).

Маршруты .exe: NMS_Station.exe --run-indexer <...> / --run-lookup <...> —
exe сам служит и окном, и дочерними задачами.
Настройки путей запоминаются в nms_station_settings.json рядом со скриптом/exe.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))          # собрано PyInstaller'ом в .exe
BASE = os.path.dirname(sys.executable) if FROZEN else HERE
SETTINGS = os.path.join(BASE, "nms_station_settings.json")
INDEXER = os.path.join(HERE, "nms_indexer.py")
LOOKUP = os.path.join(HERE, "nms_lookup.py")
ICONS_PNG = r"C:\Users\User\Desktop\NMS_EXTRACT\ИКОНКИ_PNG"

# СТАНЦИЯ МЕШЕЙ (объединена сюда по команде юзера 06.07.2026): движок nms_meshwork.py.
# Раздел показывается только там, где есть движок и каталог проекта (машина разработки).
MESHWORK = os.path.join(HERE, "nms_meshwork.py")
PARTS_DB = r"C:\Users\User\Documents\Unreal Projects\NMS_BuilderApp\Content\nms_parts_db.json"
MESH_STAGING = r"C:\Users\User\Desktop\MESHWORK_STAGING"
HAS_MESHWORK = os.path.isfile(MESHWORK) and os.path.isfile(PARTS_DB)

# признак машины разработки: есть локальные дампы. У друзей — портативные пути рядом с exe.
DEV_MBIN = r"C:\Users\User\Desktop\MBINCompiler\MBINCompiler.exe"
DEV_OBJECTSTABLE = (r"C:\Users\User\Desktop\MBINCompiler\PRECACHE_FULL"
                    r"\metadata\reality\tables\basebuildingobjectstable.MXML")
if os.path.isfile(DEV_MBIN):
    DEFAULTS = {
        "pcbanks": r"C:\Users\User\Desktop\NMS_EXTRACT\1",
        "out": r"C:\Users\User\Desktop\NMS_INDEX",
        "mbin": DEV_MBIN,
    }
else:
    DEFAULTS = {
        "pcbanks": "",
        "out": os.path.join(BASE, "NMS_INDEX"),
        "mbin": os.path.join(BASE, "MBINCompiler.exe"),
    }

# ------------------------------------------------------------------ переводы
LANGS = [("ru", "Русский"), ("en", "English"), ("de", "Deutsch"),
         ("fr", "Français"), ("zh", "中文"), ("es", "Español")]

T = {
"ru": {
 "title": "NMS Станция — индексатор и паспорт деталей",
 "pcbanks": "Папка PCBANKS (.pak):", "out": "Папка результата:", "mbin": "MBINCompiler.exe:",
 "browse": "Обзор…", "index_all": "ИНДЕКСИРОВАТЬ ВСЁ", "tables_only": "только таблицы",
 "limit": "  лимит деталей:", "oid": "ObjectID:", "style": "стиль:",
 "passport": "ПАСПОРТ ДЕТАЛИ", "passport_all": "ВСЕ ПАСПОРТА", "stop": "СТОП", "clear": "Очистить",
 "open_result": "Открыть результат", "icon_panel": "ИКОНКА ДЕТАЛИ",
 "gallery_btn": "ГАЛЕРЕЯ — все иконки", "lang": "Язык:",
 "ready_init": "Готов. Укажи пути и жми кнопку.", "ready": "Готов.",
 "busy": "%s — выполняется...", "task_index": "Индексация %s", "task_lookup": "Паспорт детали %s", "task_lookup_all": "Паспорта всех деталей",
 "no_outdir_pp": "!! Укажи «Папку результата» — туда сложится отчёт passports\\.",
 "already": "! Уже идёт задача — дождись или нажми СТОП.",
 "fail_start": "!! не запустилось: %s", "done": "<<< завершено, код %d",
 "stopped": "(остановлено пользователем)", "no_pcbanks": "!! Нет папки PCBANKS: %s",
 "no_outdir": "(папки результата ещё нет: %s)",
 "enter_oid": "!! Введи ObjectID (например TELEPORTER или _WALLB).",
 "no_icons": "!! PNG-иконок нет — сначала прогони «ИНДЕКСИРОВАТЬ ВСЁ».",
 "gal_loading": "Галерея: загружаю %d иконок из %s...", "gal_progress": "Галерея: %d/%d...",
 "gal_done": "Галерея: %d иконок. Клик по картинке = пути детали.",
 "no_png": "нет PNG-иконки", "icon_err": "не читается:\n%s",
 "num": "№ %d   %s", "png_icon": "PNG-иконка: %s", "dds_ingame": "DDS в игре:  %s",
 "scene": "Сцена:       %s", "geometry": "Геометрия:   %s", "hull": "Габарит:     %.2f x %.2f x %.2f м",
 "hint": "(кнопка ПАСПОРТ ДЕТАЛИ или Enter — полный паспорт)",
 "pp_title": "ПАСПОРТ ДЕТАЛИ: %s   (из parts_links.json)",
 "pp_notfound": "!! «%s» нет в базе. Сначала прогони «ИНДЕКСИРОВАТЬ ВСЁ» или проверь ID.",
 "pp_style_menu": "Стиль: %s   Меню: %s",
 "pp_palettes": "Палитры: colour=%s material=%s | красить=%s отделка=%s масштаб=%s 3D=%s",
 "pp_families": "Семьи: %s", "pp_composite": "КОМПОЗИТ из: %s",
 "pp_power": "Есть электрика (LinkGridData)", "pp_partid": "part-ID: %s",
 "pp_style_row": "  стиль %-12s %s", "pp_placement_warn": "!! ВНИМАНИЕ: превью-сцена ДРУГАЯ: %s",
 "pp_scene": "Сцена: %s", "pp_geo": "  Геометрия: %s  (в паках: %s)",
 "pp_refs": "  СОСТАВНАЯ! ссылки: %s", "pp_snap": "  Снап-точек: %d (%s...)",
 "pp_mat": "  Материал %s  Class=%s", "pp_flags": "    Флаги: %s",
 "pp_hull": "Габарит (%s): %.2f x %.2f x %.2f м",
 "pp_icon_dds": "Иконка DDS: %s", "pp_icon_png": "Иконка PNG: %s",
 "mesh_row": "МЕШИ — группа:", "mesh_group_all": "— ВСЕ ДЕТАЛИ —",
 "mesh_find": "или ObjectID/имя:", "mesh_run": "СОБРАТЬ И ПРОВЕРИТЬ",
 "mesh_preview": "превью", "mesh_open_prev": "Превью-картинки", "mesh_open_rep": "Отчёт мешей",
 "task_mesh": "Сборка и проверка мешей: %s", "mesh_need": "!! Выбери группу мешей или впиши ObjectID/имя.",
},
"en": {
 "title": "NMS Station — parts indexer & passport",
 "pcbanks": "PCBANKS folder (.pak):", "out": "Output folder:", "mbin": "MBINCompiler.exe:",
 "browse": "Browse…", "index_all": "INDEX EVERYTHING", "tables_only": "tables only",
 "limit": "  parts limit:", "oid": "ObjectID:", "style": "style:",
 "passport": "PART PASSPORT", "passport_all": "ALL PASSPORTS", "stop": "STOP", "clear": "Clear",
 "open_result": "Open output", "icon_panel": "PART ICON",
 "gallery_btn": "GALLERY — all icons", "lang": "Language:",
 "ready_init": "Ready. Set the paths and press a button.", "ready": "Ready.",
 "busy": "%s — running...", "task_index": "Indexing %s", "task_lookup": "Part passport %s", "task_lookup_all": "Passports for all parts",
 "no_outdir_pp": "!! Set the «Result folder» — the passports\\ report goes there.",
 "already": "! A task is already running — wait or press STOP.",
 "fail_start": "!! failed to start: %s", "done": "<<< finished, exit code %d",
 "stopped": "(stopped by user)", "no_pcbanks": "!! PCBANKS folder not found: %s",
 "no_outdir": "(output folder does not exist yet: %s)",
 "enter_oid": "!! Enter an ObjectID (e.g. TELEPORTER or _WALLB).",
 "no_icons": "!! No PNG icons yet — run INDEX EVERYTHING first.",
 "gal_loading": "Gallery: loading %d icons from %s...", "gal_progress": "Gallery: %d/%d...",
 "gal_done": "Gallery: %d icons. Click a picture for the part's paths.",
 "no_png": "no PNG icon", "icon_err": "unreadable:\n%s",
 "num": "# %d   %s", "png_icon": "PNG icon:   %s", "dds_ingame": "DDS in game: %s",
 "scene": "Scene:       %s", "geometry": "Geometry:    %s", "hull": "Bounds:      %.2f x %.2f x %.2f m",
 "hint": "(PART PASSPORT button or Enter — full passport)",
 "pp_title": "PART PASSPORT: %s   (from parts_links.json)",
 "pp_notfound": "!! “%s” is not in the database. Run INDEX EVERYTHING first or check the ID.",
 "pp_style_menu": "Style: %s   Menu: %s",
 "pp_palettes": "Palettes: colour=%s material=%s | paint=%s finish=%s scale=%s 3D=%s",
 "pp_families": "Families: %s", "pp_composite": "COMPOSITE of: %s",
 "pp_power": "Has power grid (LinkGridData)", "pp_partid": "part-ID: %s",
 "pp_style_row": "  style %-12s %s", "pp_placement_warn": "!! WARNING: preview scene DIFFERS: %s",
 "pp_scene": "Scene: %s", "pp_geo": "  Geometry: %s  (in paks: %s)",
 "pp_refs": "  COMPOSITE! references: %s", "pp_snap": "  Snap points: %d (%s...)",
 "pp_mat": "  Material %s  Class=%s", "pp_flags": "    Flags: %s",
 "pp_hull": "Bounds (%s): %.2f x %.2f x %.2f m",
 "pp_icon_dds": "Icon DDS: %s", "pp_icon_png": "Icon PNG: %s",
 "mesh_row": "MESHES — group:", "mesh_group_all": "— ALL PARTS —",
 "mesh_find": "or ObjectID/name:", "mesh_run": "BUILD & VERIFY",
 "mesh_preview": "previews", "mesh_open_prev": "Preview images", "mesh_open_rep": "Mesh report",
 "task_mesh": "Building & verifying meshes: %s", "mesh_need": "!! Pick a mesh group or enter an ObjectID/name.",
},
"de": {
 "title": "NMS Station — Teile-Indexer & Steckbrief",
 "pcbanks": "PCBANKS-Ordner (.pak):", "out": "Ausgabeordner:", "mbin": "MBINCompiler.exe:",
 "browse": "Durchsuchen…", "index_all": "ALLES INDEXIEREN", "tables_only": "nur Tabellen",
 "limit": "  Teile-Limit:", "oid": "ObjectID:", "style": "Stil:",
 "passport": "TEIL-STECKBRIEF", "passport_all": "ALLE STECKBRIEFE", "stop": "STOPP", "clear": "Leeren",
 "open_result": "Ergebnis öffnen", "icon_panel": "TEIL-SYMBOL",
 "gallery_btn": "GALERIE — alle Symbole", "lang": "Sprache:",
 "ready_init": "Bereit. Pfade angeben und Knopf drücken.", "ready": "Bereit.",
 "busy": "%s — läuft...", "task_index": "Indexierung %s", "task_lookup": "Steckbrief %s", "task_lookup_all": "Steckbriefe aller Teile",
 "no_outdir_pp": "!! Setze den «Ergebnisordner» — der passports\\-Bericht landet dort.",
 "already": "! Es läuft bereits eine Aufgabe — warten oder STOPP drücken.",
 "fail_start": "!! Start fehlgeschlagen: %s", "done": "<<< fertig, Code %d",
 "stopped": "(vom Benutzer gestoppt)", "no_pcbanks": "!! PCBANKS-Ordner nicht gefunden: %s",
 "no_outdir": "(Ausgabeordner existiert noch nicht: %s)",
 "enter_oid": "!! ObjectID eingeben (z. B. TELEPORTER oder _WALLB).",
 "no_icons": "!! Noch keine PNG-Symbole — zuerst ALLES INDEXIEREN ausführen.",
 "gal_loading": "Galerie: lade %d Symbole aus %s...", "gal_progress": "Galerie: %d/%d...",
 "gal_done": "Galerie: %d Symbole. Klick aufs Bild = Pfade des Teils.",
 "no_png": "kein PNG-Symbol", "icon_err": "nicht lesbar:\n%s",
 "num": "Nr. %d   %s", "png_icon": "PNG-Symbol: %s", "dds_ingame": "DDS im Spiel: %s",
 "scene": "Szene:      %s", "geometry": "Geometrie:  %s", "hull": "Maße:       %.2f x %.2f x %.2f m",
 "hint": "(Knopf TEIL-STECKBRIEF oder Enter — kompletter Steckbrief)",
 "pp_title": "TEIL-STECKBRIEF: %s   (aus parts_links.json)",
 "pp_notfound": "!! „%s“ ist nicht in der Datenbank. Zuerst ALLES INDEXIEREN oder ID prüfen.",
 "pp_style_menu": "Stil: %s   Menü: %s",
 "pp_palettes": "Paletten: colour=%s material=%s | färben=%s Finish=%s Skalieren=%s 3D=%s",
 "pp_families": "Familien: %s", "pp_composite": "KOMPOSIT aus: %s",
 "pp_power": "Hat Stromnetz (LinkGridData)", "pp_partid": "part-ID: %s",
 "pp_style_row": "  Stil %-12s %s", "pp_placement_warn": "!! ACHTUNG: Vorschau-Szene WEICHT AB: %s",
 "pp_scene": "Szene: %s", "pp_geo": "  Geometrie: %s  (in Paks: %s)",
 "pp_refs": "  KOMPOSIT! Verweise: %s", "pp_snap": "  Snap-Punkte: %d (%s...)",
 "pp_mat": "  Material %s  Class=%s", "pp_flags": "    Flags: %s",
 "pp_hull": "Maße (%s): %.2f x %.2f x %.2f m",
 "pp_icon_dds": "Symbol DDS: %s", "pp_icon_png": "Symbol PNG: %s",
},
"fr": {
 "title": "NMS Station — indexeur et fiche des pièces",
 "pcbanks": "Dossier PCBANKS (.pak) :", "out": "Dossier de sortie :", "mbin": "MBINCompiler.exe :",
 "browse": "Parcourir…", "index_all": "TOUT INDEXER", "tables_only": "tables seulement",
 "limit": "  limite de pièces :", "oid": "ObjectID :", "style": "style :",
 "passport": "FICHE DE PIÈCE", "passport_all": "TOUTES LES FICHES", "stop": "STOP", "clear": "Effacer",
 "open_result": "Ouvrir le résultat", "icon_panel": "ICÔNE DE PIÈCE",
 "gallery_btn": "GALERIE — toutes les icônes", "lang": "Langue :",
 "ready_init": "Prêt. Indiquez les chemins et cliquez.", "ready": "Prêt.",
 "busy": "%s — en cours...", "task_index": "Indexation %s", "task_lookup": "Fiche de %s", "task_lookup_all": "Fiches de toutes les pièces",
 "no_outdir_pp": "!! Indique le «Dossier résultat» — le rapport passports\\ y sera créé.",
 "already": "! Une tâche est déjà en cours — attendez ou appuyez sur STOP.",
 "fail_start": "!! échec du lancement : %s", "done": "<<< terminé, code %d",
 "stopped": "(arrêté par l'utilisateur)", "no_pcbanks": "!! Dossier PCBANKS introuvable : %s",
 "no_outdir": "(le dossier de sortie n'existe pas encore : %s)",
 "enter_oid": "!! Saisissez un ObjectID (ex. TELEPORTER ou _WALLB).",
 "no_icons": "!! Pas encore d'icônes PNG — lancez d'abord TOUT INDEXER.",
 "gal_loading": "Galerie : chargement de %d icônes depuis %s...", "gal_progress": "Galerie : %d/%d...",
 "gal_done": "Galerie : %d icônes. Cliquez sur une image = chemins de la pièce.",
 "no_png": "pas d'icône PNG", "icon_err": "illisible :\n%s",
 "num": "N° %d   %s", "png_icon": "Icône PNG :  %s", "dds_ingame": "DDS en jeu : %s",
 "scene": "Scène :      %s", "geometry": "Géométrie :  %s", "hull": "Dimensions : %.2f x %.2f x %.2f m",
 "hint": "(bouton FICHE DE PIÈCE ou Entrée — fiche complète)",
 "pp_title": "FICHE DE PIÈCE : %s   (depuis parts_links.json)",
 "pp_notfound": "!! « %s » absent de la base. Lancez d'abord TOUT INDEXER ou vérifiez l'ID.",
 "pp_style_menu": "Style : %s   Menu : %s",
 "pp_palettes": "Palettes : colour=%s material=%s | peindre=%s finition=%s échelle=%s 3D=%s",
 "pp_families": "Familles : %s", "pp_composite": "COMPOSITE de : %s",
 "pp_power": "Réseau électrique présent (LinkGridData)", "pp_partid": "part-ID : %s",
 "pp_style_row": "  style %-12s %s", "pp_placement_warn": "!! ATTENTION : la scène d'aperçu DIFFÈRE : %s",
 "pp_scene": "Scène : %s", "pp_geo": "  Géométrie : %s  (dans les paks : %s)",
 "pp_refs": "  COMPOSITE ! références : %s", "pp_snap": "  Points de snap : %d (%s...)",
 "pp_mat": "  Matériau %s  Class=%s", "pp_flags": "    Drapeaux : %s",
 "pp_hull": "Dimensions (%s) : %.2f x %.2f x %.2f m",
 "pp_icon_dds": "Icône DDS : %s", "pp_icon_png": "Icône PNG : %s",
},
"zh": {
 "title": "NMS 工作站 — 部件索引与档案",
 "pcbanks": "PCBANKS 文件夹 (.pak)：", "out": "输出文件夹：", "mbin": "MBINCompiler.exe：",
 "browse": "浏览…", "index_all": "全部索引", "tables_only": "仅表格",
 "limit": "  部件上限：", "oid": "ObjectID：", "style": "风格：",
 "passport": "部件档案", "passport_all": "全部档案", "stop": "停止", "clear": "清空",
 "open_result": "打开结果", "icon_panel": "部件图标",
 "gallery_btn": "图库 — 全部图标", "lang": "语言：",
 "ready_init": "就绪。设置路径后点击按钮。", "ready": "就绪。",
 "busy": "%s — 进行中…", "task_index": "正在索引 %s", "task_lookup": "部件档案 %s", "task_lookup_all": "全部部件档案",
 "no_outdir_pp": "!! 请设置「结果文件夹」— passports\\ 报告将保存在那里。",
 "already": "! 已有任务在运行 — 请等待或点击停止。",
 "fail_start": "!! 启动失败：%s", "done": "<<< 已完成，代码 %d",
 "stopped": "（已被用户停止）", "no_pcbanks": "!! 找不到 PCBANKS 文件夹：%s",
 "no_outdir": "（输出文件夹尚不存在：%s）",
 "enter_oid": "!! 请输入 ObjectID（例如 TELEPORTER 或 _WALLB）。",
 "no_icons": "!! 还没有 PNG 图标 — 请先运行「全部索引」。",
 "gal_loading": "图库：正在从 %s 加载 %d 个图标…（顺序已调整）", "gal_progress": "图库：%d/%d…",
 "gal_done": "图库：%d 个图标。点击图片查看部件路径。",
 "no_png": "无 PNG 图标", "icon_err": "无法读取：\n%s",
 "num": "编号 %d   %s", "png_icon": "PNG 图标：%s", "dds_ingame": "游戏内 DDS：%s",
 "scene": "场景：%s", "geometry": "几何体：%s", "hull": "尺寸：%.2f x %.2f x %.2f 米",
 "hint": "（点击「部件档案」或按回车查看完整档案）",
 "pp_title": "部件档案：%s   （来自 parts_links.json）",
 "pp_notfound": "!! 数据库中没有「%s」。请先运行「全部索引」或检查 ID。",
 "pp_style_menu": "风格：%s   菜单：%s",
 "pp_palettes": "调色板：colour=%s material=%s | 可涂色=%s 可换材质=%s 可缩放=%s 3D=%s",
 "pp_families": "族群：%s", "pp_composite": "组合件，由以下组成：%s",
 "pp_power": "带电网（LinkGridData）", "pp_partid": "part-ID：%s",
 "pp_style_row": "  风格 %-12s %s", "pp_placement_warn": "!! 注意：预览场景不同：%s",
 "pp_scene": "场景：%s", "pp_geo": "  几何体：%s  （在 pak 中：%s）",
 "pp_refs": "  组合件！引用：%s", "pp_snap": "  吸附点：%d 个（%s…）",
 "pp_mat": "  材质 %s  Class=%s", "pp_flags": "    标志：%s",
 "pp_hull": "尺寸（%s）：%.2f x %.2f x %.2f 米",
 "pp_icon_dds": "图标 DDS：%s", "pp_icon_png": "图标 PNG：%s",
},
"es": {
 "title": "NMS Station — indexador y ficha de piezas",
 "pcbanks": "Carpeta PCBANKS (.pak):", "out": "Carpeta de resultados:", "mbin": "MBINCompiler.exe:",
 "browse": "Examinar…", "index_all": "INDEXAR TODO", "tables_only": "solo tablas",
 "limit": "  límite de piezas:", "oid": "ObjectID:", "style": "estilo:",
 "passport": "FICHA DE PIEZA", "passport_all": "TODAS LAS FICHAS", "stop": "DETENER", "clear": "Limpiar",
 "open_result": "Abrir resultados", "icon_panel": "ICONO DE PIEZA",
 "gallery_btn": "GALERÍA — todos los iconos", "lang": "Idioma:",
 "ready_init": "Listo. Indica las rutas y pulsa un botón.", "ready": "Listo.",
 "busy": "%s — en curso...", "task_index": "Indexación %s", "task_lookup": "Ficha de %s", "task_lookup_all": "Fichas de todas las piezas",
 "no_outdir_pp": "!! Indica la «Carpeta de resultado» — el informe passports\\ se creará ahí.",
 "already": "! Ya hay una tarea en curso — espera o pulsa DETENER.",
 "fail_start": "!! no se pudo iniciar: %s", "done": "<<< terminado, código %d",
 "stopped": "(detenido por el usuario)", "no_pcbanks": "!! Carpeta PCBANKS no encontrada: %s",
 "no_outdir": "(la carpeta de resultados aún no existe: %s)",
 "enter_oid": "!! Escribe un ObjectID (p. ej. TELEPORTER o _WALLB).",
 "no_icons": "!! Aún no hay iconos PNG — ejecuta primero INDEXAR TODO.",
 "gal_loading": "Galería: cargando %d iconos desde %s...", "gal_progress": "Galería: %d/%d...",
 "gal_done": "Galería: %d iconos. Clic en una imagen = rutas de la pieza.",
 "no_png": "sin icono PNG", "icon_err": "ilegible:\n%s",
 "num": "N.º %d   %s", "png_icon": "Icono PNG:  %s", "dds_ingame": "DDS en el juego: %s",
 "scene": "Escena:     %s", "geometry": "Geometría:  %s", "hull": "Dimensiones: %.2f x %.2f x %.2f m",
 "hint": "(botón FICHA DE PIEZA o Enter — ficha completa)",
 "pp_title": "FICHA DE PIEZA: %s   (de parts_links.json)",
 "pp_notfound": "!! «%s» no está en la base. Ejecuta primero INDEXAR TODO o revisa el ID.",
 "pp_style_menu": "Estilo: %s   Menú: %s",
 "pp_palettes": "Paletas: colour=%s material=%s | pintar=%s acabado=%s escala=%s 3D=%s",
 "pp_families": "Familias: %s", "pp_composite": "COMPUESTO de: %s",
 "pp_power": "Tiene red eléctrica (LinkGridData)", "pp_partid": "part-ID: %s",
 "pp_style_row": "  estilo %-12s %s", "pp_placement_warn": "!! ATENCIÓN: la escena de vista previa DIFIERE: %s",
 "pp_scene": "Escena: %s", "pp_geo": "  Geometría: %s  (en los paks: %s)",
 "pp_refs": "  ¡COMPUESTO! referencias: %s", "pp_snap": "  Puntos de anclaje: %d (%s...)",
 "pp_mat": "  Material %s  Class=%s", "pp_flags": "    Banderas: %s",
 "pp_hull": "Dimensiones (%s): %.2f x %.2f x %.2f m",
 "pp_icon_dds": "Icono DDS: %s", "pp_icon_png": "Icono PNG: %s",
},
}
# поправка китайской строки галереи (порядок аргументов %d/%s должен совпадать во всех языках)
T["zh"]["gal_loading"] = "图库：正在加载 %d 个图标（来自 %s）…"


class Station(tk.Tk):
    def __init__(self):
        super().__init__()
        self.geometry("1000x680")
        self.minsize(760, 480)
        self.proc = None
        self.q = queue.Queue()
        cfg = dict(DEFAULTS)
        cfg["lang"] = "ru"
        if os.path.isfile(SETTINGS):
            try:
                with open(SETTINGS, "r", encoding="utf-8") as fh:
                    cfg.update(json.load(fh))
            except Exception:
                pass
        self.lang = cfg.get("lang", "ru") if cfg.get("lang") in T else "ru"
        self._i18n = []   # [(widget, ключ)] — для живой смены языка

        pad = {"padx": 6, "pady": 3}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        self.var_pcbanks = tk.StringVar(value=cfg["pcbanks"])
        self.var_out = tk.StringVar(value=cfg["out"])
        self.var_mbin = tk.StringVar(value=cfg["mbin"])
        self._path_row(top, 0, "pcbanks", self.var_pcbanks, is_dir=True)
        self._path_row(top, 1, "out", self.var_out, is_dir=True)
        self._path_row(top, 2, "mbin", self.var_mbin, is_dir=False)
        top.columnconfigure(1, weight=1)
        # язык — справа сверху; «Открыть результат» — рядом со строкой папки результата
        self._reg(ttk.Label(top), "lang").grid(row=0, column=3, padx=(12, 2), sticky="e")
        self.var_lang = tk.StringVar(value=dict(LANGS)[self.lang])
        cb = ttk.Combobox(top, textvariable=self.var_lang, width=10, state="readonly",
                          values=[n for _c, n in LANGS])
        cb.grid(row=0, column=4, sticky="w")
        cb.bind("<<ComboboxSelected>>", self._on_lang)
        self._reg(ttk.Button(top, command=self.open_out), "open_result").grid(
            row=1, column=3, columnspan=2, sticky="ew", padx=(12, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        self.btn_index = self._reg(ttk.Button(row, command=self.run_indexer), "index_all")
        self.btn_index.pack(side="left", padx=(0, 8))
        self.var_tables = tk.BooleanVar(value=False)
        self._reg(ttk.Checkbutton(row, variable=self.var_tables), "tables_only").pack(side="left")
        self._reg(ttk.Label(row), "limit").pack(side="left")
        self.var_limit = tk.StringVar(value="")
        e_lim = ttk.Entry(row, textvariable=self.var_limit, width=6)
        e_lim.pack(side="left")
        self._bind_clipboard(e_lim)

        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=10)
        self._reg(ttk.Label(row), "oid").pack(side="left")
        self.var_oid = tk.StringVar(value="")
        e = ttk.Entry(row, textvariable=self.var_oid, width=18)
        e.pack(side="left", padx=(2, 4))
        e.bind("<Return>", lambda _ev: self.run_lookup())
        e.bind("<KP_Enter>", lambda _ev: self.run_lookup())   # Enter на цифровой клаве
        self.e_oid = e
        self._bind_clipboard(e)                                # вставка ObjectID при любой раскладке
        self._reg(ttk.Label(row), "style").pack(side="left")
        self.var_style = tk.StringVar(value="")
        e_sty = ttk.Entry(row, textvariable=self.var_style, width=10)
        e_sty.pack(side="left", padx=(2, 4))
        self._bind_clipboard(e_sty)
        self.btn_lookup = self._reg(ttk.Button(row, command=self.run_lookup), "passport")
        self.btn_lookup.pack(side="left", padx=(0, 4))
        self.btn_lookup_all = self._reg(ttk.Button(row, command=self.run_lookup_all), "passport_all")
        self.btn_lookup_all.pack(side="left", padx=(0, 8))

        self.btn_stop = self._reg(ttk.Button(row, command=self.stop, state="disabled"), "stop")
        self.btn_stop.pack(side="right")
        self._reg(ttk.Button(row, command=lambda: self.text.delete("1.0", "end")),
                  "clear").pack(side="right", padx=4)

        # --- ряд МЕШЕЙ (СТАНЦИЯ МЕШЕЙ, только на машине разработки)
        self.btn_mesh = None
        if HAS_MESHWORK:
            mrow = ttk.Frame(self)
            mrow.pack(fill="x", **pad)
            self._reg(ttk.Label(mrow), "mesh_row").pack(side="left")
            self.var_mgroup = tk.StringVar(value="")
            ttk.Combobox(mrow, textvariable=self.var_mgroup, width=22, state="readonly",
                         values=[self.t("mesh_group_all")] + self._mesh_groups()
                         ).pack(side="left", padx=(4, 8))
            self._reg(ttk.Label(mrow), "mesh_find").pack(side="left")
            self.var_mfind = tk.StringVar(value="")
            me = ttk.Entry(mrow, textvariable=self.var_mfind, width=18)
            me.pack(side="left", padx=(2, 6))
            me.bind("<Return>", lambda _ev: self.run_meshwork())
            self._reg(ttk.Label(mrow), "limit").pack(side="left")
            self.var_mlimit = tk.StringVar(value="")
            ttk.Entry(mrow, textvariable=self.var_mlimit, width=6).pack(side="left")
            self.var_mprev = tk.BooleanVar(value=True)
            self._reg(ttk.Checkbutton(mrow, variable=self.var_mprev), "mesh_preview").pack(
                side="left", padx=6)
            self.btn_mesh = self._reg(ttk.Button(mrow, command=self.run_meshwork), "mesh_run")
            self.btn_mesh.pack(side="left", padx=(0, 8))
            self._reg(ttk.Button(mrow, command=lambda: self._open_mesh("preview")),
                      "mesh_open_prev").pack(side="left")
            self._reg(ttk.Button(mrow, command=lambda: self._open_mesh("ОТЧЁТ.txt")),
                      "mesh_open_rep").pack(side="left", padx=4)

        # нижняя часть: слева окно результата, справа галерея; между ними —
        # ПЕРЕТАСКИВАЕМЫЙ разделитель (PanedWindow), ширины окон меняются мышью
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(fill="both", expand=True, **pad)

        left = ttk.Frame(self.paned)
        self.text = tk.Text(left, wrap="none", font=("Consolas", 10),
                            bg="#101418", fg="#d7e0ea", insertbackground="#d7e0ea")
        ys = ttk.Scrollbar(left, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(left, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        side = tk.Frame(self.paned, bg="#181d22")
        lbl = tk.Label(side, bg="#181d22", fg="#8fa4b8", font=("Segoe UI", 9, "bold"))
        self._reg(lbl, "icon_panel")
        lbl.pack(pady=(8, 2))
        self.icon_label = tk.Label(side, text="—", bg="#181d22", fg="#5a6a7a",
                                   font=("Segoe UI", 10))
        self.icon_label.pack(pady=2)
        self.icon_caption = tk.Label(side, text="", bg="#181d22", fg="#d7e0ea",
                                     font=("Consolas", 10, "bold"))
        self.icon_caption.pack(pady=(0, 4))
        self._icon_img = None
        self._reg(ttk.Button(side, command=self.load_gallery), "gallery_btn").pack(pady=(0, 4))
        gwrap = tk.Frame(side, bg="#181d22")
        gwrap.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.gcanvas = tk.Canvas(gwrap, bg="#14181d", highlightthickness=0)
        gys = ttk.Scrollbar(gwrap, orient="vertical", command=self.gcanvas.yview)
        self.gcanvas.configure(yscrollcommand=gys.set)
        gys.pack(side="right", fill="y")
        self.gcanvas.pack(side="left", fill="both", expand=True)
        self.gcanvas.bind("<MouseWheel>",
                          lambda e: self.gcanvas.yview_scroll(-e.delta // 120, "units"))
        self.gcanvas.bind("<Configure>", self._on_gallery_resize)

        self.paned.add(left, weight=3)
        self.paned.add(side, weight=1)
        # стартовая позиция разделителя: правой панели ~300px
        self.after(250, lambda: self.paned.sashpos(0, max(300, self.winfo_width() - 320)))

        self._thumb_map = {}       # путь -> PhotoImage (кэш миниатюр, иначе их съест GC)
        self._thumb_bad = set()
        self._gallery_files = []
        self._gallery_pos = 0
        self._gallery_cols = 3
        self._resize_job = None
        self._parts_links = None
        self._last_task = ""

        self.status = tk.StringVar()
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(side="left", fill="x",
                                                                  expand=True, padx=8, pady=2)
        self.prog = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self.prog.pack(side="right", padx=8, pady=2)

        self.apply_lang()
        self.status.set(self.t("ready_init"))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)
        self.after(400, lambda: self.load_gallery(silent=True))  # иконки сразу при старте

    # ------------------------------------------------------------ язык
    def t(self, key):
        return T.get(self.lang, T["ru"]).get(key) or T["ru"].get(key, key)

    def _reg(self, widget, key):
        self._i18n.append((widget, key))
        return widget

    def apply_lang(self):
        self.title(self.t("title"))
        for w, key in self._i18n:
            try:
                w.configure(text=self.t(key))
            except Exception:
                pass

    def _on_lang(self, _ev=None):
        name = self.var_lang.get()
        for code, nm in LANGS:
            if nm == name:
                self.lang = code
                break
        self.apply_lang()
        if self.proc is None:
            self.status.set(self.t("ready"))
        self.save_settings()

    # ------------------------------------------------------------ UI helpers
    def _path_row(self, parent, r, key, var, is_dir):
        self._reg(ttk.Label(parent), key).grid(row=r, column=0, sticky="w")
        _ent = ttk.Entry(parent, textvariable=var)
        _ent.grid(row=r, column=1, sticky="ew", padx=4)
        self._bind_clipboard(_ent)

        def browse():
            if is_dir:
                p = filedialog.askdirectory(initialdir=var.get() or "C:\\")
            else:
                p = filedialog.askopenfilename(initialdir=os.path.dirname(var.get() or "C:\\"),
                                               filetypes=[("exe", "*.exe"), ("*", "*.*")])
            if p:
                var.set(p.replace("/", "\\"))
        self._reg(ttk.Button(parent, command=browse), "browse").grid(row=r, column=2)

    def log(self, s):
        self.text.insert("end", s)
        self.text.see("end")

    def _bind_clipboard(self, w):
        """Ctrl+C/V/X/A и правый клик работают при ЛЮБОЙ раскладке клавиатуры.
        Tk по умолчанию вешает cut/copy/paste на ЛАТИНСКИЙ keysym — при русской
        раскладке физическая V даёт кириллический keysym, и Ctrl+V не срабатывает
        (нельзя вставить ObjectID из буфера). Ловим по keycode (VK-код, от раскладки
        не зависит) и шлём виртуальные события; плюс контекстное меню по правому клику."""
        def on_ctrl(ev):
            kc = getattr(ev, "keycode", 0)
            if kc == 65:                         # A — выделить всё
                try:
                    w.select_range(0, "end"); w.icursor("end")
                except Exception:
                    pass
                return "break"
            ve = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}.get(kc)
            if ve:
                w.event_generate(ve)
                return "break"
        w.bind("<Control-KeyPress>", on_ctrl, add="+")
        ru = getattr(self, "lang", "ru") == "ru"
        lab = ((("Вырезать", "Копировать", "Вставить", "Выделить всё")) if ru
               else ("Cut", "Copy", "Paste", "Select all"))
        m = tk.Menu(w, tearoff=0)
        m.add_command(label=lab[0], command=lambda: w.event_generate("<<Cut>>"))
        m.add_command(label=lab[1], command=lambda: w.event_generate("<<Copy>>"))
        m.add_command(label=lab[2], command=lambda: w.event_generate("<<Paste>>"))
        m.add_separator()
        m.add_command(label=lab[3],
                      command=lambda: (w.select_range(0, "end"), w.icursor("end")))

        def popup(ev):
            w.focus_set()
            try:
                m.tk_popup(ev.x_root, ev.y_root)
            finally:
                m.grab_release()
        w.bind("<Button-3>", popup, add="+")

    def save_settings(self):
        try:
            with open(SETTINGS, "w", encoding="utf-8") as fh:
                json.dump({"pcbanks": self.var_pcbanks.get(), "out": self.var_out.get(),
                           "mbin": self.var_mbin.get(), "lang": self.lang},
                          fh, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def open_out(self):
        out = self.var_out.get()
        if os.path.isdir(out):
            os.startfile(out)
        else:
            self.log("\n" + self.t("no_outdir") % out + "\n")

    # ------------------------------------------------------------ запуск задач
    def _child_argv(self, kind, args):
        """Команда дочерней задачи: из исходников — python + скрипт,
        из .exe — тот же exe с флагом-маршрутом (--run-indexer/--run-lookup)."""
        if FROZEN:
            return [sys.executable, "--run-" + kind] + args
        script = {"indexer": INDEXER, "lookup": LOOKUP, "meshwork": MESHWORK}[kind]
        return [sys.executable, script] + args

    def _start(self, argv, title):
        if self.proc is not None:
            self.log("\n" + self.t("already") + "\n")
            return
        self.save_settings()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.log("\n" + "=" * 78 + "\n>>> %s\n" % title)
        try:
            self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", env=env,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self.log(self.t("fail_start") % e + "\n")
            self.proc = None
            return
        self._task_title = title
        self._prog_det = False            # полоса ещё в режиме крутилки
        self.status.set(self.t("busy") % title)
        self.prog.configure(mode="indeterminate")
        self.prog.start(80)
        self.btn_index.configure(state="disabled")
        self.btn_lookup.configure(state="disabled")
        self.btn_lookup_all.configure(state="disabled")
        if self.btn_mesh is not None:
            self.btn_mesh.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        p = self.proc
        for line in p.stdout:
            self.q.put(line)
        p.wait()
        self.q.put("\n" + self.t("done") % p.returncode + "\n")
        self.q.put(None)  # маркер конца

    def pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    self.proc = None
                    self.prog.stop()
                    self.status.set(self.t("ready"))
                    self.btn_index.configure(state="normal")
                    self.btn_lookup.configure(state="normal")
                    self.btn_lookup_all.configure(state="normal")
                    if self.btn_mesh is not None:
                        self.btn_mesh.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    if self._last_task == "index":
                        self._parts_links = None   # база обновилась — перечитать
                        self.load_gallery()        # и сразу показать все иконки
                else:
                    self.log(item)
                    self._update_progress(item)   # видимая загрузка внизу
        except queue.Empty:
            pass
        self.after(100, self.pump)

    _PROG_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

    def _update_progress(self, line):
        """Показывает прогресс подпроцесса внизу: строку X/Y из вывода -> статус + полоса %."""
        m = None
        for m in self._PROG_RE.finditer(line):
            pass                       # берём ПОСЛЕДНЕЕ X/Y в строке (это счётчик)
        if not m:
            return
        cur, tot = int(m.group(1)), int(m.group(2))
        if tot <= 0 or cur > tot:
            return
        if not self._prog_det:         # переключить крутилку на полосу-процент
            self.prog.stop()
            self.prog.configure(mode="determinate", maximum=100)
            self._prog_det = True
        pct = int(cur * 100 / tot)
        self.prog["value"] = pct
        self.status.set("%s  —  %d/%d  (%d%%)" % (self._task_title, cur, tot, pct))

    # ------------------------------------------------------------ иконка
    def icon_dirs(self):
        """Где искать PNG-иконки: сперва свежедобытые индексатором, потом старый дамп."""
        return [os.path.join(self.var_out.get().strip(), "icons"), ICONS_PNG]

    def show_icon(self, oid):
        p = None
        for d in self.icon_dirs():
            c = os.path.join(d, oid.upper() + ".png")
            if os.path.isfile(c):
                p = c
                break
        self.icon_caption.configure(text=oid.upper())
        if p:
            try:
                img = tk.PhotoImage(file=p)
                f = max(1, (img.width() + 199) // 200)
                if f > 1:
                    img = img.subsample(f, f)
                self._icon_img = img
                self.icon_label.configure(image=img, text="")
                return
            except Exception as e:
                self.icon_label.configure(image="", text=self.t("icon_err") % e)
                return
        self._icon_img = None
        self.icon_label.configure(image="", text=self.t("no_png"))

    # ------------------------------------------------------------ галерея иконок
    def load_gallery(self, silent=False):
        """Все иконки деталей под номерами; клик по картинке = пути детали."""
        src = None
        for d in self.icon_dirs():
            if os.path.isdir(d) and any(f.lower().endswith(".png") for f in os.listdir(d)):
                src = d
                break
        if not src:
            if not silent:
                self.log("\n" + self.t("no_icons") + "\n")
            return
        files = sorted(f for f in os.listdir(src) if f.lower().endswith(".png"))
        self.gcanvas.delete("all")
        self._gallery_files = [(i + 1, os.path.join(src, f), os.path.splitext(f)[0])
                               for i, f in enumerate(files)]
        self._gallery_pos = 0
        self._gallery_cols = self._calc_cols()
        self.status.set(self.t("gal_loading") % (len(files), src))
        self.after(10, self._gallery_chunk)

    CELL_W, CELL_H, THUMB = 82, 96, 64

    def _calc_cols(self):
        w = self.gcanvas.winfo_width()
        return max(1, (w - 8) // self.CELL_W) if w > 20 else self._gallery_cols

    def _draw_cell(self, idx):
        num, path, oid = self._gallery_files[idx]
        col, row = idx % self._gallery_cols, idx // self._gallery_cols
        x, y = 6 + col * self.CELL_W, 6 + row * self.CELL_H
        cx = x + self.CELL_W // 2 - 4
        tag = "g%d" % num
        img = self._thumb_map.get(path)
        if img is None and path not in self._thumb_bad:
            try:
                img = tk.PhotoImage(file=path)
                f = max(1, (max(img.width(), img.height()) + self.THUMB - 1) // self.THUMB)
                if f > 1:
                    img = img.subsample(f, f)
                self._thumb_map[path] = img
            except Exception:
                self._thumb_bad.add(path)
                img = None
        if img is not None:
            self.gcanvas.create_image(cx, y + self.THUMB // 2, image=img, tags=tag)
        else:
            self.gcanvas.create_text(cx, y + self.THUMB // 2, text="?",
                                     fill="#5a6a7a", tags=tag)
        cap = oid if len(oid) <= 11 else oid[:10] + "…"
        self.gcanvas.create_text(cx, y + self.THUMB + 8, text="%d" % num,
                                 fill="#8fa4b8", font=("Consolas", 8), tags=tag)
        self.gcanvas.create_text(cx, y + self.THUMB + 20, text=cap,
                                 fill="#d7e0ea", font=("Consolas", 8), tags=tag)
        self.gcanvas.tag_bind(tag, "<Button-1>",
                              lambda _e, n=num, p=path, o=oid: self._gallery_click(n, p, o))

    def _gallery_chunk(self):
        end = min(self._gallery_pos + 60, len(self._gallery_files))
        for idx in range(self._gallery_pos, end):
            self._draw_cell(idx)
        self._gallery_pos = end
        self.gcanvas.configure(scrollregion=self.gcanvas.bbox("all"))
        if end < len(self._gallery_files):
            self.status.set(self.t("gal_progress") % (end, len(self._gallery_files)))
            self.after(10, self._gallery_chunk)
        else:
            self.status.set(self.t("gal_done") % end)
            fresh = self._calc_cols()
            if fresh != self._gallery_cols:   # ширину меняли, пока грузилось
                self._gallery_cols = fresh
                self._redraw_gallery()

    def _redraw_gallery(self):
        """Пересобрать сетку под новую ширину (миниатюры уже в кэше — быстро)."""
        self.gcanvas.delete("all")
        self._gallery_pos = 0
        self.after(5, self._gallery_chunk)

    def _on_gallery_resize(self, _event=None):
        if not self._gallery_files:
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)

        def apply():
            self._resize_job = None
            cols = self._calc_cols()
            done = self._gallery_pos >= len(self._gallery_files)
            if cols != self._gallery_cols and done:
                self._gallery_cols = cols
                self._redraw_gallery()
        self._resize_job = self.after(250, apply)

    def _load_links(self):
        if self._parts_links is None:
            p = os.path.join(self.var_out.get().strip(), "parts_links.json")
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    self._parts_links = json.load(fh)
            except Exception:
                self._parts_links = {}
        return self._parts_links

    def _gallery_click(self, num, png_path, oid):
        self.show_icon(oid)
        self.var_oid.set(oid)
        out = ["", "=" * 70, self.t("num") % (num, oid), self.t("png_icon") % png_path]
        pl = self._load_links()
        rec = (pl.get("parts") or {}).get(oid)
        if rec:
            if rec.get("icon"):
                out.append(self.t("dds_ingame") % rec["icon"]["dds"])
            if rec.get("scene"):
                out.append(self.t("scene") % rec["scene"])
            sc = (pl.get("scenes") or {}).get((rec.get("scene") or "").replace("\\", "/").lower())
            if sc and sc.get("geometry"):
                out.append(self.t("geometry") % sc["geometry"])
            if rec.get("hulls"):
                h = next(iter(rec["hulls"].values()))
                out.append(self.t("hull") % tuple(h["size"]))
        out.append(self.t("hint"))
        self.log("\n".join(out) + "\n")

    # ------------------------------------------------------------ задачи
    def run_indexer(self):
        pc = self.var_pcbanks.get().strip()
        if not os.path.isdir(pc):
            self.log("\n" + self.t("no_pcbanks") % pc + "\n")
            return
        args = [pc, "--out", self.var_out.get().strip(),
                "--mbin", self.var_mbin.get().strip()]
        if self.var_tables.get():
            args.append("--tables-only")
        lim = self.var_limit.get().strip()
        if lim.isdigit() and int(lim) > 0:
            args += ["--limit", lim]
        self._last_task = "index"
        self._start(self._child_argv("indexer", args), self.t("task_index") % pc)

    def run_lookup(self):
        try:
            self._run_lookup_impl()
        except Exception as e:
            import traceback
            self.log("\n!! run_lookup: %s\n%s\n" % (e, traceback.format_exc()))

    def _run_lookup_impl(self):
        oid = self.var_oid.get().strip()
        if not oid:
            self.log("\n" + self.t("enter_oid") + "\n")
            return
        self.show_icon(oid.lstrip("^"))
        if not os.path.isfile(DEV_OBJECTSTABLE):
            # у друзей локальных дампов нет — паспорт строим из parts_links.json
            self.local_passport(oid.lstrip("^").upper())
            return
        args = [oid]
        style = self.var_style.get().strip()
        if style:
            args += ["--style", style]
        self._last_task = "lookup"
        self._start(self._child_argv("lookup", args), self.t("task_lookup") % oid)

    def run_lookup_all(self):
        """Прогон паспорта по ВСЕМ деталям (или первым N по полю «лимит»).
        Отчёт складывается в <папка результата>\\passports\\ (<ID>.txt + _ВСЕ_ПАСПОРТА.txt)."""
        out = self.var_out.get().strip()
        if not os.path.isdir(out):
            self.log("\n" + self.t("no_outdir_pp") + "\n")
            return
        args = ["--all", "--out", out]
        lim = self.var_limit.get().strip()
        if lim.isdigit() and int(lim) > 0:
            args += ["--limit", lim]
        self._last_task = "lookup"
        self._start(self._child_argv("lookup", args), self.t("task_lookup_all"))

    def local_passport(self, oid):
        """Паспорт детали из проиндексированной базы (портативный режим, без дампов)."""
        pl = self._load_links()
        rec = (pl.get("parts") or {}).get(oid)
        if not rec:
            self.log("\n" + self.t("pp_notfound") % oid + "\n")
            return
        o = rec["object"]
        L = ["", "=" * 72, self.t("pp_title") % oid, "=" * 72]
        L.append(self.t("pp_style_menu") % (o.get("Style") or "—",
                                            "; ".join(o.get("Groups") or []) or "—"))
        L.append(self.t("pp_palettes") % (o.get("ColourPaletteGroupId"), o.get("MaterialGroupId"),
                                          o.get("CanChangeColour"), o.get("CanChangeMaterial"),
                                          o.get("CanScale"), o.get("CanRotate3D")))
        if o.get("Families"):
            L.append(self.t("pp_families") % ", ".join(o["Families"]))
        if o.get("Composites"):
            L.append(self.t("pp_composite") % ", ".join(o["Composites"]))
        if o.get("HasLinkGridData"):
            L.append(self.t("pp_power"))
        L.append(self.t("pp_partid") % rec.get("part_id"))
        for st, scn in (rec.get("styles") or {}).items():
            L.append(self.t("pp_style_row") % (st, scn))
        if rec.get("placement_differs"):
            L.append(self.t("pp_placement_warn") % rec["placement_differs"])
        L.append(self.t("pp_scene") % (rec.get("scene") or "—"))
        sc = (pl.get("scenes") or {}).get((rec.get("scene") or "").replace("\\", "/").lower())
        if sc and "error" not in sc:
            L.append(self.t("pp_geo") % (sc.get("geometry"), sc.get("geometry_in_pak")))
            if sc.get("references"):
                L.append(self.t("pp_refs") % "; ".join(r["scene"] for r in sc["references"][:8]))
            if sc.get("snap"):
                L.append(self.t("pp_snap") % (len(sc["snap"]),
                                              ", ".join(s["name"] for s in sc["snap"][:6])))
            mats = pl.get("materials") or {}
            for mp in sorted({m["material"] for m in sc.get("meshes", []) if m["material"]}):
                mi = mats.get(mp.replace("\\", "/").lower()) or {}
                L.append(self.t("pp_mat") % (mp.split("\\")[-1], mi.get("class", "?")))
                if mi.get("flags"):
                    L.append(self.t("pp_flags") % " ".join(mi["flags"]))
                for s in mi.get("samplers", []):
                    L.append("    %-18s sRGB=%-5s %s" % (s["name"], s["srgb"], s["map"]))
        for st, h in (rec.get("hulls") or {}).items():
            L.append(self.t("pp_hull") % ((st or "None",) + tuple(h["size"])))
        if rec.get("icon"):
            L.append(self.t("pp_icon_dds") % rec["icon"]["dds"])
            L.append(self.t("pp_icon_png") % rec["icon"]["png"])
        self.log("\n".join(L) + "\n")

    # ------------------------------------------------------------ меши
    def _mesh_groups(self):
        try:
            with open(PARTS_DB, encoding="utf-8") as fh:
                db = json.load(fh)
            seen = []
            for p in db:   # порядок групп = порядок появления в каталоге (панель ДЕТАЛИ)
                c = p.get("Category")
                if c and c not in seen:
                    seen.append(c)
            return seen
        except Exception:
            return []

    def _mesh_dir(self):
        g = self.var_mgroup.get().strip()
        if self.var_mfind.get().strip():
            name = "_поиск"
        elif g == self.t("mesh_group_all"):
            name = "_ВСЕ_ДЕТАЛИ"
        else:
            name = g or "_поиск"
        return os.path.join(MESH_STAGING, name.replace("/", "_"))

    def _open_mesh(self, sub):
        p = os.path.join(self._mesh_dir(), sub) if sub else self._mesh_dir()
        if os.path.exists(p):
            os.startfile(p)
        else:
            self.log("\n" + self.t("no_outdir") % p + "\n")

    def run_meshwork(self):
        find = self.var_mfind.get().strip()
        g = self.var_mgroup.get().strip()
        args = ["--index", self.var_out.get().strip(), "--mbin", self.var_mbin.get().strip()]
        if find:
            args += ["--find", find]
        elif g == self.t("mesh_group_all"):
            args.append("--all")
        elif g:
            args += ["--group", g]
        else:
            self.log("\n" + self.t("mesh_need") + "\n")
            return
        lim = self.var_mlimit.get().strip()
        if lim.isdigit() and int(lim) > 0:
            args += ["--limit", lim]
        if not self.var_mprev.get():
            args.append("--no-preview")
        self._last_task = "mesh"
        self._start(self._child_argv("meshwork", args), self.t("task_mesh") % (find or g))

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.kill()
                self.log("\n" + self.t("stopped") + "\n")
            except Exception:
                pass

    def on_close(self):
        self.stop()
        self.save_settings()
        self.destroy()


def main():
    # маршруты для .exe: тот же файл работает и окном, и дочерними задачами
    if "--run-indexer" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--run-indexer"]
        import nms_indexer
        nms_indexer.main()
        return
    if "--run-lookup" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--run-lookup"]
        import nms_lookup
        nms_lookup.main()
        return
    if "--run-meshwork" in sys.argv:
        sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--run-meshwork"]
        import nms_meshwork
        nms_meshwork.main()
        return
    if "--selftest" in sys.argv:
        app = Station()
        app.update_idletasks()
        print("selftest OK: окно %sx%s, кнопки на месте" % (app.winfo_width(), app.winfo_height()))
        app.destroy()
        return
    app = Station()
    app.mainloop()


if __name__ == "__main__":
    main()
