# -*- coding: utf-8 -*-
"""
nms_meshwork.py — «СТАНЦИЯ МЕШЕЙ»: автосборка мешей группы + автопроверки + превью.

Схема (одобрена юзером 06.07.2026, заметка в memory/nms-lookup-tool):
  вход = группа каталога (или список ObjectID) + NMS_INDEX\\parts_links.json
  -> для каждой детали: conv2026.build(сцена, ассет) — конвертер зовётся КАК МОДУЛЬ
     (не форкается), OBJ пишется в STAGING-папку (НЕ в MeshSrc!)
  -> автопроверки: bbox OBJ vs хулл MagicData (оси NMS: X=ox/100, Y=oz/100, Z=-oy/100),
     треугольники vs сцена игры (BATCHCOUNT LOD0 с наследованием LOD-контекста),
     слоты usemtl vs материалы сцены
  -> рендер-превью OBJ рядом с иконкой игры (сравнение глазом)
  -> отчёт зелёные/жёлтые/красные + import_list.json (только зелёные).

ПРЕДОХРАНИТЕЛИ:
  - ПРОВЕРКА идёт по ВСЕМ деталям (staging никому не вредит, а запечённые полезно
    регресс-проверять после обновлений игры);
  - в MeshSrc ничего не пишется без ЯВНОГО --promote (копирует зелёные OBJ + пишет
    stage2_import_list.json для Content/Python/reimport_stage2.py); при promote
    запечённые (verified_meshes.json) защищены — перезапись только с --force-verified;
  - детали, собранные в проекте ВРУЧНУЮ по решению юзера (ворота GDOOR открытые),
    проверяются, но всегда ЖЁЛТЫЕ с пометкой «не продвигать»;
  - патологические меши (виснущие стопки вариантов) собираются с таймаутом в подпроцессе.
Автоматика глаз НЕ заменяет: итог всегда «посмотри превью» (папка preview рядом с отчётом).

ВСЁ С НУЛЯ ИЗ ИГРЫ (решение юзера 07.07.2026): порядок свежести данных —
1) своя MESHWORK_EXTRACT_2026 (кэш уже извлечённого из паков), 2) ПАКИ игры
(извлечь+декодировать MBINCompiler'ом), 3) старые дампы — ТОЛЬКО если файла нет
в паках (в отчёте пометка «из СТАРЫХ дампов»). В ОБЩУЮ NEW_EXTRACT_2026 НЕ ПИСАТЬ
(урок 06.07: её глоб-резолверы других конвейеров затенялись placement-пустышками).

Использование:
    python nms_meshwork.py --group "Legacy Structures" [--limit N]
    python nms_meshwork.py --ids TELEPORTER,WALL      (точные ObjectID)
    python nms_meshwork.py --find стена,WALL          (поиск по ObjectID/имени)
    python nms_meshwork.py --all                      (ВСЕ детали каталога)
    ... [--staging DIR] [--index DIR] [--no-preview] [--force-verified] [--promote]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PROJ = r"C:\Users\User\Documents\Unreal Projects\NMS_BuilderApp"
DEF_INDEX = r"C:\Users\User\Desktop\NMS_INDEX"
DEF_STAGING = r"C:\Users\User\Desktop\MESHWORK_STAGING"
DEF_MBIN = r"C:\Users\User\Desktop\MBINCompiler\MBINCompiler.exe"
SC = r"C:\Users\User\Desktop\NMS_EXTRACT\SCENES_PARTS_NEW"
NE = r"C:\Users\User\Desktop\MBINCompiler\NEW_EXTRACT_2026"      # ЧУЖАЯ, только чтение!
# СВОЯ папка доизвлечения (УРОК 06.07: запись в общую NEW_EXTRACT_2026 затеняла
# глоб-резолверы конвейера ДРУГОЙ сессии placement-пустышками — детали портились;
# 2160 файлов вынесены сюда). Всё, что meshwork достаёт из паков, кладётся ТОЛЬКО сюда.
MW = r"C:\Users\User\Desktop\MBINCompiler\MESHWORK_EXTRACT_2026"
ICONS_DIRS = [os.path.join(DEF_INDEX, "icons"),
              r"C:\Users\User\Desktop\NMS_EXTRACT\ИКОНКИ_PNG"]

BUILD_TIMEOUT = 600      # сек на одну деталь (виснущие стопки вариантов)
GIANT_MB = 40            # OBJ больше — «гигант», превью пропускаем (Правило 2/13)
# известные виснущие меши (корвет-стопки вариантов, quadratic weld) — скип с пометкой
PATHOLOGICAL = {"landinggear_leg_c", "module_generators", "module_pumps"}  # vehiclegaragemech снят 11.07: conv2026 собирает за 1с

# собраны ВРУЧНУЮ по решению юзера — автоматом НЕ пересобирать (пересборка по сцене
# даст «правильный», но НЕ ЖЕЛАЕМЫЙ меш; см. memory/group-by-group-mesh-qc, коммит 857b848c)
CUSTOM_BUILT = {
    "W_GDOOR": "ворота собраны ОТКРЫТЫМИ без полотна (решение юзера 06.07)",
    "M_GDOOR": "ворота собраны ОТКРЫТЫМИ без полотна (решение юзера 06.07)",
    "C_GDOOR": "ворота собраны ОТКРЫТЫМИ без полотна (решение юзера 06.07)",
}
# легаси-ID, которых нет в objectstable: авторитетные меши подтверждены юзером 01.07.2026
# (memory/part-mesh-icon-rule18); ALIAS = «тот же меш, что у...», SUBPART = сцена под-части
ALIAS_PART = {"CORRIDOR_S": "CORRIDOR_SPACE"}          # дубликат corridor_straight
SUBPART_SCENE = {"CORRIDOR_WINDOW": "corridor_windowframe",
                 "CUBEWALL_SPACE": "cuberoom_innerwall"}

LODRE = re.compile(r"lod(\d)", re.I)
SKIPNAME = re.compile(r"shadow|collision|waterproxy|_proxy|wallbb", re.I)
# 12.07 (Corvette B_COK_*): эффекты ПОЛЁТА в сценах кабин — варп-туннели
# (EFFECTS\WARP\REENTRY/SPEEDTUNNEL, «оранжевые ковры» 27+ м) и HUD-проекции
# (MODELS\HUD\COCKPITHUD_*) — при постановке в строителе игры НЕ рендерятся.
# Паттерн кроет оба разделителя путей: models\effects\warp и models__effects__warp.
_FLIGHT_FX = r"effects[\\_/]+warp|models[\\_/]+hud"
SKIPREF = re.compile(r"snap|shadow|collision|_proxy|refwall\b|" + _FLIGHT_FX, re.I)
# «мягкий» фильтр для комнат spacebase: их содержимое = SnapGroup_*-REFERENCE'ы
# (cuberoom_a: стены/углы/пол/люки — ВСЕ через SnapGroup; прецедент — relaxed
# snap-REF добор в легаси-текстурном пассе). Применяется ТОЛЬКО как повтор при ПУСТО.
SKIPREF_RELAX = re.compile(r"shadow|collision|_proxy|refwall\b|" + _FLIGHT_FX, re.I)


# ------------------------------------------------------------------ разрешение сцены
# (копия резолвера из audit_group_scenes.py — свои файлы, не правим)

def resolve_scene_file(game_path):
    p = game_path.replace("/", "\\").lower().replace(".scene.mbin", "")
    p = re.sub(r"_placement$", "", p)
    full = p.replace("\\", "__") + ".scene.MXML"
    wo = re.sub(r"^models__", "", full)
    for d, fn in ((MW, full), (NE, full), (SC, full), (SC, wo)):
        f = os.path.join(d, fn)
        if os.path.isfile(f):
            return f
    import glob
    base = p.split("\\")[-1]
    c = glob.glob(os.path.join(SC, "*__" + base + ".scene.MXML")) + \
        glob.glob(os.path.join(SC, base + ".scene.MXML")) + \
        glob.glob(os.path.join(NE, "*__" + base + ".scene.MXML"))
    return max(c, key=os.path.getsize) if c else None


# ------------------------------------------------------------------ доизвлечение из паков
# Если ТОЧНОГО локального файла нет — достаём из PCBANKS (pak_manifest) и декодируем
# в NEW_EXTRACT_2026 с полным __-именем: у conv2026 точное имя выигрывает у фолбэков
# (лечит ловушку стилей: один basic_ramp.geometry на 7 стилей). HGPak — из nms_finish.

class Extractor(object):
    def __init__(self, pcbanks, manifest, mbin_exe):
        self.pcbanks, self.man, self.mbin = pcbanks, manifest, mbin_exe
        self.paks = {}
        self.n_extracted = 0

    def raw(self, key):
        rec = self.man.get(key)
        if rec is None:
            return None
        if rec["pak"] not in self.paks:
            from nms_finish import HGPak
            self.paks[rec["pak"]] = HGPak(os.path.join(self.pcbanks, rec["pak"]))
        return self.paks[rec["pak"]].extract(rec["index"])

    def head(self, key, n=256):
        """Первые n байт файла из пака (заголовки DDS) без полного извлечения."""
        rec = self.man.get(key)
        if rec is None:
            return None
        if rec["pak"] not in self.paks:
            from nms_finish import HGPak
            self.paks[rec["pak"]] = HGPak(os.path.join(self.pcbanks, rec["pak"]))
        return self.paks[rec["pak"]].read_head(rec["index"], n)

    def fetch_decode(self, key, dst_mbin):
        """извлечь key из паков в dst_mbin и декодировать; вернуть путь MXML или None."""
        mx = re.sub(r"\.mbin(\.pc)?$", ".MXML", dst_mbin, flags=re.I)
        if os.path.isfile(mx):
            return mx
        if not os.path.isfile(dst_mbin):
            data = self.raw(key)
            if data is None:
                return None
            with open(dst_mbin, "wb") as fh:
                fh.write(data)
        subprocess.run([self.mbin, "-y", "-q", dst_mbin], capture_output=True, timeout=600)
        if os.path.isfile(mx):
            self.n_extracted += 1
            return mx
        return None


def _norm(p):
    return p.replace("\\", "/").lower()


def _flat(p_norm):
    return p_norm.replace("/", "__")


def exact_scene_local(game_path):
    """Точный локальный файл сцены (полное имя, без глоб-фолбэков)."""
    p = re.sub(r"_placement$", "", _norm(game_path).replace(".scene.mbin", ""))
    full = _flat(p) + ".scene.MXML"
    wo = re.sub(r"^models__", "", full)
    for d, fn in ((MW, full), (NE, full), (SC, full), (SC, wo)):
        f = os.path.join(d, fn)
        if os.path.isfile(f):
            return f
    return None


def exact_geo_exists(geom_attr, geodirs):
    p = re.sub(r"\.geometry(\.mbin)?(\.pc)?$", "", _norm(geom_attr))
    full = _flat(p) + ".geometry.data.MXML"
    wo = re.sub(r"^models__", "", full)
    for gd in geodirs:
        for fn in (full, wo):
            f = os.path.join(gd, fn)
            if os.path.isfile(f) and os.path.getsize(f) > 500:
                return True
    return False


_ENSURED = {}
LEGACY_USED = set()   # файлы из СТАРЫХ дампов (в паках не нашлись) — помечаются в отчёте

def ensure_tree(game_scene, extr, geodirs, depth=0, force_geo=False):
    """Гарантирует СВЕЖИЕ локальные файлы: сцена, её geometry.data и рекурсивно все
    REFERENCE-сцены. ПОРЯДОК СВЕЖЕСТИ (решение юзера 07.07 «всё с нуля из игры»):
    1) своя MESHWORK_EXTRACT_2026 (уже извлечённое из паков),
    2) ПАКИ игры (извлечь+декодировать),
    3) старые дампы — ТОЛЬКО если в паках нет (файл попадает в LEGACY_USED),
    4) placement-обёртка из паков — последний шанс.
    force_geo оставлен для совместимости (свежесть теперь всегда)."""
    key = _norm(game_scene)
    if depth > 6 or key in _ENSURED:
        return _ENSURED.get(key)
    p = re.sub(r"_placement$", "", key.replace(".scene.mbin", ""))
    sf = None
    f = os.path.join(MW, _flat(p) + ".scene.MXML")
    if os.path.isfile(f):
        sf = f
    if sf is None and extr is not None:
        sf = extr.fetch_decode(p + ".scene.mbin", os.path.join(MW, _flat(p) + ".scene.mbin"))
    if sf is None:
        # в паках базовой нет — старые дампы (точное имя, затем нестрогий поиск)
        sf = exact_scene_local(game_scene) or resolve_scene_file(game_scene)
        if sf:
            LEGACY_USED.add(sf)
    if sf is None and extr is not None:
        sf = extr.fetch_decode(key, os.path.join(MW, _flat(key.replace(".scene.mbin", "")) + ".scene.mbin"))
    _ENSURED[key] = sf
    if sf is None:
        return None
    # геометрия этой сцены + рекурсия по REF (как в conv2026: скипы те же)
    try:
        root = ET.parse(sf).getroot()
    except ET.ParseError:
        return sf

    def rec(node):
        aa = {}
        a = P(node, "Attributes")
        if a is not None:
            for c in a.findall("Property"):
                if c.get("value") == "TkSceneNodeAttributeData":
                    nm, val = P(c, "Name"), P(c, "Value")
                    if nm is not None and val is not None:
                        aa[nm.get("value")] = val.get("value")
        g = aa.get("GEOMETRY")
        if g and extr is not None:
            p2 = re.sub(r"\.geometry(\.mbin)?(\.pc)?$", "", _norm(g))
            mw_mx = os.path.join(MW, _flat(p2) + ".geometry.data.MXML")
            if not os.path.isfile(mw_mx):
                got = None
                for cand in (p2 + ".geometry.data.mbin.pc", p2 + ".geometry.data.mbin"):
                    got = extr.fetch_decode(cand, os.path.join(MW, _flat(p2) + ".geometry.data.mbin"))
                    if got:
                        break
                if not got and exact_geo_exists(g, geodirs):
                    LEGACY_USED.add(_norm(g))   # геометрия только в старом дампе
        sg = aa.get("SCENEGRAPH")
        nm = P(node, "Name")
        name = nm.get("value").split("|")[-1] if nm is not None else ""
        if sg and not SKIPREF.search((name + sg).lower()):
            ensure_tree(sg, extr, geodirs, depth + 1, force_geo)
        ch = P(node, "Children")
        if ch is not None:
            for c in ch.findall("Property"):
                if c.get("value") == "TkSceneNodeData":
                    rec(c)

    rec(root)
    return sf


# ------------------------------------------------------------------ эталон из сцены
# треугольники LOD0 + материалы (BATCHCOUNT, наследование LOD-контекста, рекурсия REF)

def P(el, name):
    for c in el.findall("Property"):
        if c.get("name") == name:
            return c
    return None


def scene_lod0(scene_file, depth=0, seen=None, relax=False, extra=""):
    skip_re = SKIPREF_RELAX if relax else SKIPREF
    extra_rx = re.compile(extra, re.I) if extra else None
    if seen is None:
        seen = frozenset()
    # seen = ТОЛЬКО предки по текущему пути (защита от цикла): повторные REFERENCE
    # одной под-сцены (крыло корвета: EXTERIORLIGHT ×6; коридор ExSec1/ExSec2) — это
    # РАЗНЫЕ инстансы, каждый считается (урок LS/Corvette 12.07: чинить счётчик, не меш)
    if scene_file in seen or depth > 6:
        return 0, []
    seen = frozenset(seen) | {scene_file}
    try:
        root = ET.parse(scene_file).getroot()
    except ET.ParseError:
        return 0, []
    tris, mats = 0, []

    def attrs_of(node):
        out = {}
        a = P(node, "Attributes")
        if a is None:
            return out
        for c in a.findall("Property"):
            if c.get("value") == "TkSceneNodeAttributeData":
                nm, val = P(c, "Name"), P(c, "Value")
                if nm is not None and val is not None:
                    out[nm.get("value")] = val.get("value")
        return out

    def rec(node, lod_ctx):
        nonlocal tris
        nm, tp = P(node, "Name"), P(node, "Type")
        if nm is None or tp is None:
            return
        name, typ = nm.get("value").split("|")[-1], tp.get("value")
        if extra_rx and extra_rx.search(name):
            return  # невыбранная опция дескриптора — всё поддерево мимо
        m = LODRE.search(name.lower())
        lod = int(m.group(1)) if m else lod_ctx
        aa = attrs_of(node)
        # LODLEVEL = АВТОРИТЕТ (имена врут: fossils SF_01LOD4LOD0), см. conv2026
        lvl = aa.get("LODLEVEL")
        mesh_lod0 = (lvl == "0") if lvl not in (None, "") else (lod is None or lod == 0)
        if typ == "MESH" and not SKIPNAME.search(name.lower()) and mesh_lod0:
            tris += int(aa.get("BATCHCOUNT", 0) or 0) // 3
            mat = os.path.basename(aa.get("MATERIAL", "")).split(".")[0].lower()
            if mat and mat not in mats:
                mats.append(mat)
        elif typ == "REFERENCE" and (lod is None or lod == 0):
            sg = aa.get("SCENEGRAPH", "")
            if sg and not skip_re.search((name + sg).lower()):
                rf = resolve_scene_file(sg)
                if rf:
                    t2, m2 = scene_lod0(rf, depth + 1, seen, relax, extra)
                    tris += t2
                    for x in m2:
                        if x not in mats:
                            mats.append(x)
        ch = P(node, "Children")
        if ch is not None:
            for c in ch.findall("Property"):
                if c.get("value") == "TkSceneNodeData":
                    rec(c, lod)

    rec(root, None)
    return tris, mats


# ------------------------------------------------------------------ разбор OBJ

def parse_obj(path):
    """-> (verts Nx3 list, tris список индексных троек, [usemtl], bbox (min,max))."""
    verts, tris, mats = [], [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:4]]
                tris.append(tuple(idx))
            elif line.startswith("usemtl "):
                m = line.split(None, 1)[1].strip()
                if m not in mats:
                    mats.append(m)
    if not verts:
        return None
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    bbox = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    return verts, tris, mats, bbox


def obj_bbox_to_nms(bbox):
    """OBJ-кадр конвертера = (x, -z, y)*100 -> оси NMS в метрах:
    X = ox/100, Y = oz/100, Z = -oy/100 (минус меняет местами min/max по Z)."""
    (x0, y0, z0), (x1, y1, z1) = bbox
    mn = (x0 / 100.0, z0 / 100.0, -y1 / 100.0)
    mx = (x1 / 100.0, z1 / 100.0, -y0 / 100.0)
    size = tuple(round(b - a, 4) for a, b in zip(mn, mx))
    return mn, mx, size


def hull_check(size_obj, hull):
    """Сверка габарита с хуллом MagicData. ВАЖНО (калибровка 06.07.2026 на C_RAMP):
    автогенерированный хулл игры идёт С ЗАПАСОМ (меш 5.53 м при хулле 6.24 м у
    ПРИНЯТОГО юзером меша) — поэтому «меньше хулла» = КРАСН только при большой
    недостаче (потеря кусков), лёгкая недостача = ЖЁЛТ; заметно шире = ЖЁЛТ
    (голо/glow-квады — норма для проекций)."""
    size_h = hull["size"]
    flags_red, flags_yellow = [], []
    for ax in range(3):
        so, sh = size_obj[ax], size_h[ax]
        tol_red = max(1.0, sh * 0.20)
        tol_yel = max(0.35, sh * 0.10)
        tol_big = max(0.5, sh * 0.15)
        if so < sh - tol_red:
            flags_red.append("%s: %0.2f м << хулл %0.2f м (потеря кусков?)" % ("XYZ"[ax], so, sh))
        elif so < sh - tol_yel:
            flags_yellow.append("%s: %0.2f м < хулл %0.2f м (глянуть глазом)" % ("XYZ"[ax], so, sh))
        elif so > sh + tol_big:
            flags_yellow.append("%s: %0.2f м > хулл %0.2f м (голо/glow?)" % ("XYZ"[ax], so, sh))
    return flags_red, flags_yellow


def project_match(asset, parsed):
    """Свежая сборка == OBJ в MeshSrc проекта (число вершин/граней + bbox)?
    Совпадение с уже ПРИНЯТЫМ мешом — сильный признак правильности."""
    f = os.path.join(PROJ, "MeshSrc", asset + ".obj")
    try:
        if not os.path.isfile(f) or os.path.getsize(f) > GIANT_MB * 1048576:
            return False
    except OSError:
        return False
    p2 = parse_obj(f)
    if not p2:
        return False
    v1, t1, _m1, b1 = parsed
    v2, t2, _m2, b2 = p2
    if len(v1) != len(v2) or len(t1) != len(t2):
        return False
    return all(abs(a - b) < 0.01 for pa, pb in zip(b1, b2) for a, b in zip(pa, pb))


# ------------------------------------------------------------------ игровая цепочка
# placement-entity (НАЙДЕНО 06.07.2026, принцип юзера «знания даёт ИГРА, не каталог»):
# placement-сцена -> LOCATOR ATTACHMENT -> *.ENTITY.MBIN -> GcBasePlacementComponentData
# .Rules[] -> правило NotSnapped (дефолтный вид одиночной детали) -> PartID ->
# partstable[PartID][стиль] -> сцена. Пример: C_DOORWINDOW -> _DOORWINB0 ->
# MESHES/CONCRETE/BASIC_WALL_DOORWINDOWL; авто-двери = правило IsSnapped -> _DOORB0.

_PARTSTABLE = None

def load_partstable(index_dir):
    """Полный partstable {ID: {стиль: сцена}} из raw индексатора (стриминг-копия)."""
    global _PARTSTABLE
    if _PARTSTABLE is not None:
        return _PARTSTABLE
    _PARTSTABLE = {}
    mxml = os.path.join(index_dir, "raw", "metadata", "reality", "tables",
                        "basebuildingpartstable.MXML")
    if not os.path.isfile(mxml):
        return _PARTSTABLE
    opener = '\t\t<Property name="Parts"'
    with open(mxml, encoding="utf-8", errors="replace") as fh:
        head = fh.read(4096)
    if 'name="Parts"' not in head:
        opener = '\t\t<Property name="Table"'
    entry, inside = [], False
    style_re = re.compile(
        r'name="Style" value="([^"]+)" />\s*\n\s*</Property>\s*\n\s*'
        r'<Property name="Model" value="TkModelResource">\s*\n\s*'
        r'<Property name="Filename" value="([^"]*)"')
    close_line = "\t\t</Property>"
    with open(mxml, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ls = line.rstrip("\n")
            if ls.startswith(opener):
                entry, inside = [ls], True
                continue
            if inside:
                entry.append(ls)
                if ls == close_line:
                    e = "\n".join(entry)
                    m = re.search(r'<Property name="ID" value="([^"]+)"', e)
                    if m:
                        _PARTSTABLE[m.group(1)] = {s: f for s, f in style_re.findall(e) if f}
                    inside = False
    return _PARTSTABLE


def ensure_exact_scene(game_path, extr):
    """Точный локальный файл сцены БЕЗ среза _placement (для чтения entity самой
    placement-сцены). Свежесть: своя папка -> ПАКИ -> старые дампы (с пометкой)."""
    p = _norm(game_path).replace(".scene.mbin", "")
    full = _flat(p) + ".scene.MXML"
    f = os.path.join(MW, full)
    if os.path.isfile(f):
        return f
    if extr is not None:
        f = extr.fetch_decode(p + ".scene.mbin", os.path.join(MW, _flat(p) + ".scene.mbin"))
        if f:
            return f
    wo = re.sub(r"^models__", "", full)
    for d, fn in ((NE, full), (SC, full), (SC, wo)):
        f = os.path.join(d, fn)
        if os.path.isfile(f):
            LEGACY_USED.add(f)
            return f
    return None


def placement_entity_partid(placement_path, extr):
    """PartID дефолтного (NotSnapped) правила из entity placement-сцены, иначе None."""
    sf = ensure_exact_scene(placement_path, extr)
    if not sf:
        return None
    try:
        txt = open(sf, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    att = re.findall(r'name="Name" value="ATTACHMENT" />\s*\n\s*'
                     r'<Property name="Value" value="([^"]+\.ENTITY\.MBIN)"', txt, re.I)
    if not att:
        return None
    ent_key = _norm(att[0])
    mx = None
    if extr is not None:
        mx = extr.fetch_decode(ent_key, os.path.join(MW, _flat(ent_key.replace(".entity.mbin", "")) + ".entity.mbin"))
    if not mx:
        return None
    rules = _parse_entity_rules(mx)  # честная оценка условий (см. _parse_entity_rules)
    if not rules:
        return None
    # РАЗЛИЧИТЕЛЬ (проверен по данным 06.07, уточнён 12.07 на Corvette): у «подмен»
    # (стена _WALLB/M/T, дверь _DOORB0/_DOORWINB0, корвет-модуль) все правила дают
    # ОДИН PartID — даже если локаторы разные (декор/турель корвета: правила на
    # EXT_North/East/South/West/Top/Bottom, но PartID везде _BIGGS_EXTDECORATION);
    # у «сборки» (комната cuberoom: пол + стороны + углы) PartID РАЗНЫЕ на разных
    # локаторах — единого визуала нет, entity-цепочка НЕ применима.
    if len({loc for _pid, loc, _ns in rules}) > 1 and len({pid for pid, _loc, _ns in rules}) > 1:
        # КОНТЕКСТНАЯ деталь (B_DOOR0: дверной проём меняет PartID по стилю модуля,
        # 16 правил IsSnapped + РОВНО ОДНО NotSnapped) — дефолт одиночной постановки
        # = единственное NotSnapped-правило. Несколько NotSnapped на разных локаторах
        # с разными PartID (B_LAN_B: 6 из 20) = настоящая сборка, цепочка не применима.
        ns_rules = [r for r in rules if r[2]]
        if len(ns_rules) == 1:
            return ns_rules[0][0]
        return None
    for pid, _loc, ns in rules:
        if ns:
            return pid  # дефолтное состояние одиночной детали (NotSnapped)
    return rules[0][0]


def _placement_rules(placement_path, extr):
    """(entity-правила [(pid, locator, notsnapped)], текст placement-сцены) или (None, None)."""
    sf = ensure_exact_scene(placement_path, extr)
    if not sf:
        return None, None
    try:
        txt = open(sf, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, None
    att = re.findall(r'name="Name" value="ATTACHMENT" />\s*\n\s*'
                     r'<Property name="Value" value="([^"]+\.ENTITY\.MBIN)"', txt, re.I)
    if not att:
        return None, txt
    mx = extr.fetch_decode(_norm(att[0]), os.path.join(
        MW, _flat(_norm(att[0]).replace(".entity.mbin", "")) + ".entity.mbin")) if extr else None
    if not mx:
        return None, txt
    rules = _parse_entity_rules(mx)
    return rules, txt


def _parse_entity_rules(entity_mxml):
    """Правила GcBasePlacementComponentData с ЧЕСТНОЙ оценкой условий в «одиночном
    мире» (все снап-точки NotSnapped). Плоский сбор SnapState давал «раздвоенные»
    комнаты (hab_1x1: на WALLE1_ дверь И глухая стена — у двери условие IsSnapped
    на сокете RoomW_Out (сосед пристыкован), у стены группа с ORConditions=true,
    выполняющаяся при одиночной постановке). Возвращает [(pid, locator, ns)]."""
    try:
        root = ET.parse(entity_mxml).getroot()
    except ET.ParseError:
        return []

    def val(el, name):
        for c in el.findall("Property"):
            if c.get("name") == name:
                return c
        return None

    def eval_cond(cond_el):
        """cond_el = <Property value="Gc*Condition"> — истинность при одиночной постановке."""
        kind = cond_el.get("value") or ""
        inner = cond_el.find("Property")  # <Property name="Gc*Condition"> обёртка
        if inner is None:
            return True
        if kind == "GcGroupCondition":
            conds_el = val(inner, "Conditions")
            subs = list(conds_el.findall("Property")) if conds_el is not None else []
            orf = val(inner, "ORConditions")
            is_or = orf is not None and orf.get("value") == "true"
            if not subs:
                return True
            vals = [eval_cond(s) for s in subs]
            return any(vals) if is_or else all(vals)
        # GcSnapPointCondition / GcOutSnapSocketCondition: «точка в состоянии X»;
        # в одиночном мире всё NotSnapped -> условие истинно ⇔ X == NotSnapped
        ss = inner.find('.//Property[@name="SnapState"]/Property[@name="SnapState"]')
        if ss is None:
            for c in inner.iter("Property"):
                if c.get("name") == "SnapState" and c.get("value") in ("NotSnapped", "IsSnapped"):
                    ss = c
                    break
        state = ss.get("value") if ss is not None else "NotSnapped"
        return state == "NotSnapped"

    rules = []
    for rule in root.iter("Property"):
        if rule.get("value") != "GcBasePlacementRule":
            continue
        pid_el = val(rule, "PartID")
        if pid_el is None or not pid_el.get("value"):
            continue
        loc_el = val(rule, "PositionLocator")
        conds_el = val(rule, "Conditions")
        subs = list(conds_el.findall("Property")) if conds_el is not None else []
        orf = val(rule, "ORConditions")
        is_or = orf is not None and orf.get("value") == "true"
        if not subs:
            ns = True  # безусловное правило (ядро B_ALK_C)
        else:
            vals = [eval_cond(s) for s in subs]
            ns = any(vals) if is_or else all(vals)
        rules.append((pid_el.get("value"), loc_el.get("value") if loc_el is not None else "", ns))
    return rules


_ASM_REF = """\t\t<Property value="TkSceneNodeData">
\t\t\t<Property name="Name" value="%s" />
\t\t\t<Property name="Type" value="REFERENCE" />
\t\t\t<Property name="Transform" value="TkTransformData">
%s\t\t\t</Property>
\t\t\t<Property name="Attributes">
\t\t\t\t<Property value="TkSceneNodeAttributeData">
\t\t\t\t\t<Property name="Name" value="SCENEGRAPH" />
\t\t\t\t\t<Property name="Value" value="%s" />
\t\t\t\t</Property>
\t\t\t</Property>
\t\t\t<Property name="Children" />
\t\t</Property>
"""


def entity_assembly_scene(oid, placement_path, obj_style, extr, index_dir, geodirs, out_file):
    """СБОРКА комнаты корвета по правилам placement-entity (B_HAB_A/B/C, B_LAN_B —
    принцип юзера «меш детали = как она встаёт при постановке»): NotSnapped-правила
    (локатор→PartID) + сцена RefPositionLocators (позиции локаторов) → синтетическая
    сцена с REFERENCE-узлами на partstable-сцены частей. None, если это не сборка."""
    rules, txt = _placement_rules(placement_path, extr)
    if not rules or not txt:
        return None
    ns_rules = [(loc, pid) for pid, loc, ns in rules if ns]
    if len({pid for _l, pid in ns_rules}) < 2:
        return None  # подмена, не сборка — обычная цепочка
    # локаторы: сцена *_POSITIONLOCATORS из REFERENCE placement-сцены (+ сама сцена)
    locs = {}
    def collect_locators(scene_txt):
        for m in re.finditer(
                r'<Property name="Name" value="([^"]+)" />\s*\n\s*<Property name="NameHash"[^>]*/>\s*\n\s*'
                r'<Property name="Type" value="LOCATOR" />\s*\n\s*<Property name="Transform" value="TkTransformData">'
                r'(.*?)</Property>', scene_txt, re.S):
            vals = dict(re.findall(r'<Property name="(\w+)" value="([-\d.eE]+)"', m.group(2)))
            locs[m.group(1)] = vals
    # ⚠ 13.07: в MXML атрибут = <Property name="Name" value="SCENEGRAPH"/> — старый
    # паттерн name="SCENEGRAPH" НЕ матчился никогда → локаторы не собирались и ВСЕ
    # стены комнат падали в (0,0,0) («стена посередине комнаты», глаз юзера)
    sgs = re.findall(r'(?:name|value)="SCENEGRAPH" */>\s*\n\s*<Property name="Value" value="([^"]+POSITIONLOCATORS[^"]*)"',
                     txt, re.I)
    if not sgs and ("HAB_" in placement_path.upper() or "EXTHATCH_" in placement_path.upper()):
        # placement HAB-семьи без REF на локаторы: в игре ровно ДВЕ сцены позиций
        # (hab_1x1 6x6 / hab_1x2 6x12) — берём по размеру модуля из имени placement.
        # ⚠ ТОЛЬКО для hab-модулей: LANDINGBAY_B_1X2 = 10x20 м, hab-позиции ему ЧУЖИЕ
        # (13.07 глаз юзера: стены/полы ангара вставали не по месту) — у ангара
        # позиций в данных игры НЕТ вообще, его части честно не ставятся
        size = "1x2" if "1X2" in placement_path.upper() else "1x1"
        sgs = ["MODELS/COMMON/SPACECRAFT/BIGGS/MODULES/HAB_1X2_POSITIONLOCATORS.SCENE.MBIN" if size == "1x2"
               else "MODELS/COMMON/SPACECRAFT/BIGGS/MODULES/PARTS/HAB_1X1_POSITIONLOCATORS.SCENE.MBIN"]
    for sg in sgs:
        lf = ensure_exact_scene(_norm(sg), extr)
        if lf:
            collect_locators(open(lf, encoding="utf-8", errors="replace").read())
    collect_locators(txt)
    pst = load_partstable(index_dir)
    refs, missing = [], []
    for i, (loc, pid) in enumerate(ns_rules):
        row = pst.get(pid) or {}
        part_scene = row.get(obj_style) or row.get("None") or (next(iter(row.values())) if row else None)
        if not part_scene:
            # части комнат корвета без записи в partstable лежат сценой напрямую:
            # _BIGGS_WALL_B0 -> biggs/modules/parts/wall_b0.scene.mbin (проверено 12.07)
            cand = "models/common/spacecraft/biggs/modules/parts/%s.scene.mbin" % pid.replace("_BIGGS_", "").lower()
            if extr is not None and extr.man and cand in extr.man:
                part_scene = cand
        if not part_scene:
            missing.append(pid)
            continue
        # ⚠ 13.07: локатор правила не найден НИ в одной сцене (LAN_B FLOOR2_/3_,
        # CEILING2_/3_ — в данных игры их просто нет) — часть НЕ ставим (иначе она
        # падала в (0,0,0) = мусор в центре), честно флагуем
        if loc and loc not in locs:
            missing.append(pid + "@" + loc + "(лок.нет)")
            continue
        ensure_tree(part_scene, extr, geodirs)
        tv = locs.get(loc, {}) if loc else {}
        tr = ""
        for k, dflt in (("TransX", "0"), ("TransY", "0"), ("TransZ", "0"),
                        ("RotX", "0"), ("RotY", "0"), ("RotZ", "0"),
                        ("ScaleX", "1"), ("ScaleY", "1"), ("ScaleZ", "1")):
            tr += '\t\t\t\t<Property name="%s" value="%s" />\n' % (k, tv.get(k, dflt))
        refs.append(_ASM_REF % ("RefRoom%d_%s" % (i, (loc or "core").strip("_")), tr,
                                part_scene.replace("/", "\\").upper()))
    if not refs:
        return None
    xml = ('<?xml version="1.0" encoding="utf-8"?>\n<Data template="TkSceneNodeData">\n'
           '\t<Property name="Name" value="ASSEMBLY_%s" />\n'
           '\t<Property name="Type" value="MODEL" />\n'
           '\t<Property name="Transform" value="TkTransformData">\n'
           '\t\t<Property name="TransX" value="0" />\n\t\t<Property name="TransY" value="0" />\n'
           '\t\t<Property name="TransZ" value="0" />\n\t\t<Property name="RotX" value="0" />\n'
           '\t\t<Property name="RotY" value="0" />\n\t\t<Property name="RotZ" value="0" />\n'
           '\t\t<Property name="ScaleX" value="1" />\n\t\t<Property name="ScaleY" value="1" />\n'
           '\t\t<Property name="ScaleZ" value="1" />\n\t</Property>\n'
           '\t<Property name="Attributes" />\n\t<Property name="Children">\n%s\t</Property>\n</Data>\n'
           ) % (oid, "".join(refs))
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return out_file, len(refs), missing


# ------------------------------------------------------------------ РАНТАЙМ-КОНФИГ
# Урок 08.07 (GAMETABLE): сцена детали БЕЗ MESH-узлов ≠ «модели нет» — модель бывает
# загружена в РАНТАЙМЕ через entity-конфиг. Игровые столы: сцена → ATTACHMENT *.ENTITY →
# GcGameTablePlacementComponentData.GameTableConfig → gametablesdatatable
# (GameTableConfigs[cfg].SpawnDataId → GameTableSpawnData[spawn].SceneFilename).
GAMETABLES_MBIN = "metadata/simulation/gametables/gametablesdatatable.mbin"


def scene_has_no_mesh(scene_file):
    """В декодированной сцене нет НАСТОЯЩИХ MESH-узлов (кандидат на рантайм-конфиг/сборку).
    Placement-стабы корвета несут единственный бокс MESHBOUNDS (12 треуг., lambert1) —
    это НЕ модель (ловушка B_ALK_C 12.07: 'зелёный 1:1' совпал с такой же заглушкой)."""
    try:
        txt = open(scene_file, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    if 'value="MESH"' not in txt:
        return True
    names = re.findall(r'<Property name="Name" value="([^"]*)" />\s*\n\s*'
                       r'<Property name="NameHash"[^>]*/>\s*\n\s*<Property name="Type" value="MESH"', txt)
    return bool(names) and all(n.split("|")[-1].upper().startswith("MESHBOUNDS") for n in names)


def runtime_config_scene(scene_file, extr):
    """Настоящая сцена детали, чья модель грузится через entity-конфиг (game-table).
    Возвращает игровой путь (.scene.mbin, _norm) или None."""
    if extr is None:
        return None
    try:
        txt = open(scene_file, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    att = re.findall(r'name="Name" value="ATTACHMENT" />\s*\n\s*'
                     r'<Property name="Value" value="([^"]+\.ENTITY\.MBIN)"', txt, re.I)
    if not att:
        return None
    ent_key = _norm(att[0])
    ent = extr.fetch_decode(ent_key, os.path.join(MW, _flat(ent_key.replace(".entity.mbin", "")) + ".entity.mbin"))
    if not ent:
        return None
    cm = re.search(r'name="GameTableConfig" value="([^"]+)"',
                   open(ent, encoding="utf-8", errors="replace").read())
    if not cm:
        return None                # другой тип рантайм-конфига — пока не поддержан
    gt = extr.fetch_decode(GAMETABLES_MBIN, os.path.join(MW, "gametablesdatatable.mbin"))
    if not gt:
        return None
    gtxt = open(gt, encoding="utf-8", errors="replace").read()
    cfg = re.search(r'value="GcGameTableConfig" _id="%s">(.*?)</Property>'
                    % re.escape(cm.group(1)), gtxt, re.S)
    sp = re.search(r'name="SpawnDataId" value="([^"]+)"', cfg.group(1)) if cfg else None
    if not sp:
        return None
    spd = re.search(r'value="GcGameTableSpawnData" _id="%s">(.*?)</Property>'
                    % re.escape(sp.group(1)), gtxt, re.S)
    scn = re.search(r'name="SceneFilename" value="([^"]+)"', spd.group(1)) if spd else None
    return _norm(scn.group(1)) if scn else None


# ------------------------------------------------------------------ ДЕСКРИПТОРЫ (варианты)
# «Как игра строит такие цепочки» (запрос юзера 07.07, фоссилы): рядом со сценой лежит
# *.DESCRIPTOR.MBIN — штатная система вариантов игры. body.descriptor: опции _BODY_A +
# _BODYAACC_A..E(+NULL) -> каталожные FOS_BI_BODY_AA/AB/... = КОМБИНАЦИИ опций (буква на
# группу опций по порядку; N/нет буквы = NULL). Строим вариант, ИСКЛЮЧАЯ узлы невыбранных
# опций (той же перестройкой SKIPNAME конвертера — сам conv2026 не трогается).

def _fetch_descriptor(scene_game, extr):
    """*.descriptor.MXML рядом со сценой (из паков, кэш в MW)."""
    if extr is None:
        return None
    key = _norm(scene_game).replace(".scene.mbin", ".descriptor.mbin")
    return extr.fetch_decode(key, os.path.join(MW, _flat(key.replace(".descriptor.mbin", "")) + ".descriptor.mbin"))


def _descriptor_groups(mx):
    """[(префикс_группы, [опции])] в порядке файла: '_BODY': ['A','NULL'], '_BODYAACC': [...]."""
    t = open(mx, encoding="utf-8", errors="replace").read()
    ids = re.findall(r'name="Id" value="([^"]+)"', t)
    groups, order = {}, []
    for i in ids:
        m = re.match(r"^(_.+)_([A-Z]{1,4}|NULL)$", i)
        if not m:
            continue
        pref, opt = m.group(1), m.group(2)
        if pref not in groups:
            groups[pref] = []
            order.append(pref)
        groups[pref].append(opt)
    return [(p, groups[p]) for p in order]


_DESC_PREFIX_CACHE = {}

def _folder_desc_map(bdir, extr):
    """{префикс_опций ('_HEAD') -> сцена-кусок} по ВСЕМ дескрипторам папки семейства."""
    if bdir in _DESC_PREFIX_CACHE:
        return _DESC_PREFIX_CACHE[bdir]
    out = {}
    if extr is not None:
        depth = bdir.count("/") + 1
        for k in extr.man:
            if k.startswith(bdir + "/") and k.endswith(".descriptor.mbin") and k.count("/") == depth:
                mx = extr.fetch_decode(k, os.path.join(MW, _flat(k.replace(".descriptor.mbin", "")) + ".descriptor.mbin"))
                if not mx:
                    continue
                groups = _descriptor_groups(mx)
                for gi, (pref, _opts) in enumerate(groups):
                    # правильная сцена-кусок = та, где группа ГЛАВНАЯ (первая) в её
                    # собственном дескрипторе: у skulls первая группа _HEAD, у
                    # скелетов (biped_bones/worm_bones) _HEAD — одна из многих
                    rank = (gi != 0, len(groups))
                    old = out.get(pref)
                    if old is None or rank < old[1]:
                        out[pref] = (k.replace(".descriptor.mbin", ".scene.mbin"), rank)
    _DESC_PREFIX_CACHE[bdir] = out
    return out


def descriptor_variant(oid, links, extr, geodirs):
    """Каталожный ID-вариант (FOS_BI_BODY_AA) -> (сцена под-части, exclude-regex, заметка).
    Базовая деталь = длиннейший префикс ID, который ЕСТЬ в objectstable; остаток =
    ГРУППА_ОПЦИИ. Всё из данных игры: REF базовой сцены -> под-сцена -> её дескриптор."""
    toks = oid.split("_")
    base, rest = None, []
    for cut in range(len(toks) - 1, 0, -1):
        cand = "_".join(toks[:cut])
        if cand in links:
            base, rest = cand, toks[cut:]
            break
    if base is None:
        # у семейства нет своей базовой записи (FOS_HEAD_*: в таблице только
        # FOS_SKULL-стенды) — папку кусков берём от ЛЮБОЙ детали семейства
        fam = toks[0] + "_"
        donor = next((k for k in sorted(links) if k.startswith(fam)
                      and (links[k].get("scene") or links[k].get("styles"))), None)
        if not donor:
            return None
        base, rest = donor, toks[1:]
    if not rest:
        return None
    blink = links[base]
    bscene = (blink.get("styles") or {}).get((blink.get("object") or {}).get("Style") or "None") \
        or blink.get("scene")
    if not bscene or extr is None:
        return None
    # Сцена базовой детали = СТЕНД со слотами (SLOT_BODY...); куски = отдельные сцены
    # В ТОЙ ЖЕ ПАПКЕ с именем группы: FOS_BI_BODY_AA -> fossils/body.scene,
    # FOS_BI_ARM_LEFT_A -> fossils/arm_left.scene. Точное имя в манифесте, без фуззи.
    bdir = os.path.dirname(_norm(bscene))
    sub, opt, group = None, "", ""
    for cut2 in range(len(rest) - 1, 0, -1):
        cand = bdir + "/" + "_".join(rest[:cut2]).lower() + ".scene.mbin"
        if cand in extr.man:
            sub, opt, group = cand, "".join(rest[cut2:]), "_".join(rest[:cut2])
            break
    if not sub:
        # вариант без токена группы (FOS_HEAD_AA: базовая деталь FOS_HEAD — сама
        # стенд группы) — имя сцены-куска = последний токен базовой детали
        cand = bdir + "/" + base.split("_")[-1].lower() + ".scene.mbin"
        if cand in extr.man:
            sub, opt, group = cand, "".join(rest), base.split("_")[-1]
    if not sub:
        # имя сцены НЕ равно группе (HEAD -> skulls.scene): ищем в папке семейства
        # дескриптор, у которого ПРЕФИКС ОПЦИЙ совпадает с группой ('_HEAD_AA')
        dm = _folder_desc_map(bdir, extr)
        for cut2 in range(len(rest) - 1, 0, -1):
            pref = "_" + "_".join(rest[:cut2]).upper()
            hit = next((p for p in dm if p.upper() == pref), None)
            if hit:
                sub, opt, group = dm[hit][0], "".join(rest[cut2:]), "_".join(rest[:cut2])
                break
    if not sub:
        return None
    sfile = ensure_tree(sub, extr, geodirs)
    dmx = _fetch_descriptor(sub, extr)
    if not sfile or not dmx:
        return None
    groups = _descriptor_groups(dmx)
    # ПОРЯДОК БУКВ: буква №1 -> группа, СОВПАДАЮЩАЯ с токеном из ID ('_HEAD'/'_BODY'),
    # дальше — остальные группы в порядке файла; кончились буквы/буква N -> NULL/NONE
    main_key = "_" + group.upper()
    groups.sort(key=lambda g: (g[0].upper() != main_key,))
    NULLS = ("NULL", "NONE", "EMPTY")
    exact_grp = next((p for p, opts in groups if opt in opts and len(opt) > 1), None)
    excl, note_sel = [], []
    li = 0
    for pref, opts in groups:
        nullopt = next((o for o in opts if o in NULLS), "NULL")
        if exact_grp is not None:
            want = opt if pref == exact_grp else nullopt
        else:
            letter = opt[li] if li < len(opt) else "N"
            want = next((o for o in opts if o not in NULLS and o[:1] == letter), nullopt)
            li += 1
        note_sel.append("%s=%s" % (pref, want))
        for o in opts:
            if o != want:
                excl.append(re.escape(pref + "_" + o))
    if not excl:
        return None
    regex = r"(?:%s)$" % "|".join(excl)
    return sub, regex, "вариант по дескриптору игры: %s (%s)" % (os.path.basename(dmx), "; ".join(note_sel))


# ------------------------------------------------------------------ ТАБЛИЦЫ КАСТОМИЗАЦИИ (фоссилы, 1:1)
# 13.07.2026 (Fossils): буквы каталога — НЕ позиционная мапа на группы дескриптора.
# Игра держит ЯВНЫЙ белый список узлов на каждый вариант в ДВУХ таблицах:
#   modularcustomisationdatatable (ItemID -> ActivatedDescriptorGroupID, Exhibit -> сцена)
#   charactercustomisationdescriptorgroupsdata (GroupID -> [_Body_A, _NECK_B, ...])
# Пример подвоха: BI_BODY_BN = _Pelvis_A+_Body_A+_BodyAacc_Null+_NECK_B+_NECKBacc_Null —
# первая буква оказалась ШЕЕЙ (длинная/короткая), буквенная формула давала пустой меш.
# Дамп: meshwork\fossil_customisation_dump.py -> NMS_INDEX\fossil_variants.json.

_FV_CACHE = None

def _load_fossil_variants(index_dir):
    global _FV_CACHE
    if _FV_CACHE is None:
        try:
            with open(os.path.join(index_dir, "fossil_variants.json"), encoding="utf-8") as f:
                _FV_CACHE = json.load(f)
        except Exception:
            _FV_CACHE = {}
    return _FV_CACHE


_FOLDER_IDS_CACHE = {}

def _folder_option_ids(bdir, extr):
    """{ключ_дескриптора: set(сырые Id опций)} по ВСЕМ *.descriptor.mbin папки."""
    if bdir in _FOLDER_IDS_CACHE:
        return _FOLDER_IDS_CACHE[bdir]
    out = {}
    if extr is not None:
        depth = bdir.count("/") + 1
        for k in extr.man:
            if k.startswith(bdir + "/") and k.endswith(".descriptor.mbin") and k.count("/") == depth:
                mx = extr.fetch_decode(k, os.path.join(MW, _flat(k.replace(".descriptor.mbin", "")) + ".descriptor.mbin"))
                if not mx:
                    continue
                t = open(mx, encoding="utf-8", errors="replace").read()
                out[k] = set(re.findall(r'name="Id" value="([^"]+)"', t))
    _FOLDER_IDS_CACHE[bdir] = out
    return out


def _wl_keep(id_upper, wl_upper):
    """Опция остаётся: точное имя из белого списка ЛИБО его LOD-меш (_NECKAACC_ALOD0
    при белом _NECKAACC_A — HG кладёт меши LOD как отдельные Id)."""
    if id_upper in wl_upper:
        return True
    return any(id_upper.startswith(w) and re.fullmatch(r"LOD\d", id_upper[len(w):])
               for w in wl_upper)


def customisation_variant(oid, extr, geodirs, index_dir):
    """Вариант фоссила по таблицам кастомизации игры (1:1, без буквенных догадок).
    Возвращает (сцена, exclude-regex, заметка) или None."""
    fv = _load_fossil_variants(index_dir)
    rec = (fv.get("items") or {}).get(oid)
    if not rec or extr is None:
        return None
    groups = rec.get("groups") or []
    if not groups:
        return None
    # куски конечностей слотятся как руки И ноги (3 группы) — каноническое семейство =
    # legsrear (ровно 10 опций LimbA..J = 10 продуктов; у рук вариантов меньше)
    gid = next((g for g in groups if g.startswith("LEGSR_")), groups[0])
    wl = list((fv.get("groups") or {}).get(gid) or [])
    exh = (rec.get("exhibits") or [None])[0]
    root_scene = ((fv.get("exhibits") or {}).get(exh) or {}).get("scene") if exh else None
    if not wl or not root_scene:
        return None
    root_key = _norm(root_scene)
    bdir = os.path.dirname(root_key)
    desc_ids = _folder_option_ids(bdir, extr)
    all_ids = set().union(*desc_ids.values()) if desc_ids else set()
    if not all_ids:
        return None
    wl_ext = {w.upper() for w in wl}

    # 1) весь белый список живёт в ОДНОМ под-дескрипторе -> строим его сцену
    #    (головы -> skulls, хвосты -> tail, конечности -> legsrear)
    def covers(ids):
        up = {i.upper() for i in ids}
        return all(w in up or any(u.startswith(w) and re.fullmatch(r"LOD\d", u[len(w):]) for u in up)
                   for w in wl_ext)
    scene_key = None
    covering = [k for k, ids in desc_ids.items() if covers(ids)]
    if covering:
        cand = min(covering, key=lambda k: len(desc_ids[k])).replace(".descriptor.mbin", ".scene.mbin")
        if cand in extr.man:
            scene_key = cand
    if scene_key is None:
        # 2) сборка от сцены стенда (тело = body.scene + шея necklong/neckshort):
        #    добавить в белый список REF-опции корня, чьи под-сцены содержат наши узлы
        scene_key = root_key
        sfile = ensure_tree(scene_key, extr, geodirs)
        if not sfile:
            return None
        root_ids = {i.upper() for i in (desc_ids.get(root_key.replace(".scene.mbin", ".descriptor.mbin")) or set())}
        try:
            troot = ET.parse(sfile).getroot()
        except Exception:
            return None

        def scenegraphs(node, acc):
            a = P(node, "Attributes")
            if a is not None:
                for c in a.findall("Property"):
                    if c.get("value") == "TkSceneNodeAttributeData":
                        nm, vl = P(c, "Name"), P(c, "Value")
                        if nm is not None and vl is not None and nm.get("value") == "SCENEGRAPH":
                            acc.append(vl.get("value"))
            ch = P(node, "Children")
            if ch is not None:
                for c in ch.findall("Property"):
                    if c.get("value") == "TkSceneNodeData":
                        scenegraphs(c, acc)

        def visit(node):
            nm = P(node, "Name")
            name = (nm.get("value").split("|")[-1] if nm is not None else "")
            nu = name.upper()
            if nu in root_ids and nu not in wl_ext:
                sgs = []
                scenegraphs(node, sgs)
                for sg in sgs:
                    dk = _norm(sg).replace(".scene.mbin", ".descriptor.mbin")
                    ids = desc_ids.get(dk) or set()
                    if any(_wl_keep(i.upper(), wl_ext) for i in ids):
                        wl_ext.add(nu)
                        break
            ch = P(node, "Children")
            if ch is not None:
                for c in ch.findall("Property"):
                    if c.get("value") == "TkSceneNodeData":
                        visit(c)
        visit(troot)

    excl = sorted(i for i in all_ids if not _wl_keep(i.upper(), wl_ext))
    regex = r"^(?:%s)$" % "|".join(re.escape(x) for x in excl) if excl else ""
    note = "вариант по таблицам кастомизации игры: %s -> %s (%s)" % (
        gid, os.path.basename(scene_key), " ".join(sorted(wl)))
    return scene_key, regex, note


# ------------------------------------------------------------------ ЕДИНОЕ ДРЕВО ДЕТАЛИ
# Запрос юзера 07.07: при запекании группы вечно «не тот атлас или цвет» — программа
# должна САМА безошибочно найти материалы/текстуры/цвета каждой детали из ПАКОВ и
# выдать единое дерево: узлы сцены -> материал -> текстуры (+наличие, +слои) -> цвет.
# Дерево = будущий источник part_slots (текстурный пасс группы без ручной возни).

MAT_TYPE_HINT = {  # класс материала игры -> тип слота приложения (уроки group-by-group)
    "Glow": "unlit", "Cutout": "masked", "DoubleSided": "masked",
    "GlowTranslucent": "holo", "Additive": "holo",
    "Translucent": "glass", "SSR": "glass",
}

_FINISH = None

def load_finish(index_dir):
    global _FINISH
    if _FINISH is None:
        try:
            with open(os.path.join(index_dir, "finish_layers.json"), encoding="utf-8") as fh:
                _FINISH = json.load(fh)
        except Exception:
            _FINISH = {}
    return _FINISH


def parse_material_local(mx):
    """class/flags/цвет/сэмплеры из .MATERIAL.MXML (схема как у индексатора)."""
    try:
        tree = ET.parse(mx)
    except ET.ParseError as e:
        return {"error": "parse: %s" % e}
    flags, samplers, out = [], [], {"class": "", "colour": None}

    def rec(el):
        name = el.get("name")
        if name == "MaterialFlag":
            flags.append(el.get("value"))
        elif name == "MaterialClass":
            out["class"] = el.get("value")
        elif name == "Samplers" and el.get("value") == "TkMaterialSampler":
            d = {c.get("name"): c.get("value") for c in el.findall("Property")}
            samplers.append({"name": d.get("Name", ""), "map": d.get("Map", ""),
                             "srgb": d.get("IsSRGB", "")})
        elif name == "Uniforms_Float" and el.get("value") == "TkMaterialUniform_Float":
            ps = {c.get("name"): c for c in el.findall("Property")}
            if ps.get("Name") is not None and ps["Name"].get("value") == "gMaterialColourVec4":
                vals = re.findall(r'value="([-\d.]+)"', ET.tostring(el, encoding="unicode"))
                out["colour"] = vals[1:5] if len(vals) > 4 else vals
        for c in el:
            rec(c)

    rec(tree.getroot())
    out["flags"] = flags
    out["samplers"] = samplers
    return out


def collect_tree(scene_file, relax=False, depth=0, seen=None, prefix="", extra=""):
    """[(путь_узла, материал_game_path, треугольники)] по LOD0 с REF-рекурсией."""
    skip_re = SKIPREF_RELAX if relax else SKIPREF
    extra_rx = re.compile(extra, re.I) if extra else None
    if seen is None:
        seen = frozenset()
    # seen = только предки пути (см. scene_lod0): повторные REF считаются по-инстансно
    if scene_file in seen or depth > 6:
        return []
    seen = frozenset(seen) | {scene_file}
    try:
        root = ET.parse(scene_file).getroot()
    except ET.ParseError:
        return []
    out = []

    def attrs_of(node):
        d = {}
        a = P(node, "Attributes")
        if a is not None:
            for c in a.findall("Property"):
                if c.get("value") == "TkSceneNodeAttributeData":
                    nm, val = P(c, "Name"), P(c, "Value")
                    if nm is not None and val is not None:
                        d[nm.get("value")] = val.get("value")
        return d

    def rec(node, lod_ctx, pfx):
        nm, tp = P(node, "Name"), P(node, "Type")
        if nm is None or tp is None:
            return
        name, typ = nm.get("value").split("|")[-1], tp.get("value")
        if extra_rx and extra_rx.search(name):
            return  # невыбранная опция дескриптора
        m = LODRE.search(name.lower())
        lod = int(m.group(1)) if m else lod_ctx
        aa = attrs_of(node)
        # LODLEVEL = АВТОРИТЕТ (имена врут: fossils SF_01LOD4LOD0), см. conv2026
        lvl = aa.get("LODLEVEL")
        mesh_lod0 = (lvl == "0") if lvl not in (None, "") else (lod is None or lod == 0)
        if typ == "MESH" and not SKIPNAME.search(name.lower()) and mesh_lod0:
            out.append((pfx + name, aa.get("MATERIAL", ""),
                        int(aa.get("BATCHCOUNT", 0) or 0) // 3))
        elif typ == "REFERENCE" and (lod is None or lod == 0):
            sg = aa.get("SCENEGRAPH", "")
            if sg and not skip_re.search((name + sg).lower()):
                rf = resolve_scene_file(sg)
                if rf:
                    out.extend(collect_tree(rf, relax, depth + 1, seen, pfx + name + "/", extra))
        ch = P(node, "Children")
        if ch is not None:
            for c in ch.findall("Property"):
                if c.get("value") == "TkSceneNodeData":
                    rec(c, lod, pfx)

    rec(root, None, prefix)
    return out


def part_tree(oid, asset, scene_game, scene_file, relax, extr, obj_slots, index_dir, exclude=""):
    """Единое дерево детали. Возвращает (tree, нет_материалов, нет_текстур)."""
    manifest = extr.man if extr else {}
    nodes = collect_tree(scene_file, relax, extra=exclude)
    mats, missing_mat, missing_tex = {}, [], []
    for _n, mp, _t in nodes:
        k = _norm(mp) if mp else ""
        if not k or k in mats:
            continue
        mx = None
        if extr is not None:
            mx = extr.fetch_decode(k, os.path.join(MW, _flat(k.replace(".material.mbin", "")) + ".material.mbin"))
        if mx:
            mi = parse_material_local(mx)
        else:
            mi = {"error": "нет в паках", "samplers": [], "flags": [], "class": "", "colour": None}
            missing_mat.append(os.path.basename(mp).split(".")[0])
        mi["type_hint"] = MAT_TYPE_HINT.get(mi.get("class", ""), "lit")
        for s in mi.get("samplers", []):
            mk = _norm(s.get("map", ""))
            s["in_pak"] = bool(mk) and mk in manifest
            if s.get("map") and not s["in_pak"]:
                missing_tex.append(os.path.basename(s["map"]))
            if s["name"] == "gDiffuseMap" and s["in_pak"] and extr is not None:
                try:
                    from nms_finish import dds_info
                    info = dds_info(extr.head(mk, 256) or b"")
                    s["layers"] = info.get("layers") if info else None
                except Exception:
                    pass
        mats[k] = mi
    # слоты OBJ (usemtl, порядок = слоты меша) -> полный путь материала по базовому имени
    by_base = {}
    for _n, mp, _t in nodes:
        if mp:
            by_base.setdefault(os.path.basename(mp).split(".")[0].lower(), _norm(mp))
    slots = [{"n": i + 1, "usemtl": s, "material": by_base.get(s.lower(), "")}
             for i, s in enumerate(obj_slots or [])]
    fin = (load_finish(index_dir).get("parts") or {}).get(oid) or {}
    tree = {"id": oid, "asset": asset, "scene": scene_game,
            "default_finish": fin.get("DefaultMaterialId", ""),
            "default_palette": fin.get("DefaultColourPaletteId", ""),
            "slots": slots,
            "nodes": [{"node": n, "material": _norm(mp) if mp else "", "tris": t}
                      for n, mp, t in nodes],
            "materials": mats}
    return tree, missing_mat, sorted(set(missing_tex))


def render_tree_txt(tree):
    L = ["ДЕРЕВО ДЕТАЛИ: %s  (ассет %s)" % (tree["id"], tree["asset"]),
         "сцена: %s" % tree["scene"],
         "отделка по умолчанию: %s   палитра: %s" % (tree["default_finish"] or "—",
                                                     tree["default_palette"] or "—"), ""]
    L.append("СЛОТЫ (порядок usemtl OBJ = слоты меша):")
    for s in tree["slots"]:
        mi = tree["materials"].get(s["material"] or "", {})
        L.append(" %2d. %-34s [%s -> %s]%s" % (
            s["n"], s["usemtl"], mi.get("class") or "?", mi.get("type_hint", "lit"),
            "  цвет %s" % ",".join(mi["colour"]) if mi.get("colour") else ""))
        for smp in mi.get("samplers", []):
            extra = ""
            if smp.get("layers") and smp["layers"] > 1:
                extra = "  (слоёв: %d — мультитекстура!)" % smp["layers"]
            L.append("      %-4s %-16s %s %s%s" % (
                "OK" if smp.get("in_pak") else "НЕТ!", smp.get("name", ""),
                smp.get("map", ""), "sRGB" if smp.get("srgb") == "true" else "", extra))
        if mi.get("flags"):
            L.append("      флаги: %s" % " ".join(mi["flags"]))
    L.append("")
    L.append("УЗЛЫ СЦЕНЫ (LOD0):")
    for n in tree["nodes"]:
        L.append(" %-52s %6d трг  %s" % (n["node"][-52:], n["tris"],
                                         os.path.basename(n["material"]).split(".")[0]))
    return "\n".join(L)


def alt_scene_candidates(asset, primary, extr, geodirs):
    """Запасные сцены при ПУСТО: сцена, названная ТОЧНО по имени ассета проекта
    (пример: BRIDGECONNECTOR -> меш cubesolid, юзер подтвердил 01.07.2026) —
    сперва из паков, затем из локальных дампов. Только точные имена, без фуззи."""
    import glob as _g
    a = asset.lower()
    seen = {primary}
    out = []
    if extr is not None:
        for k in extr.man:
            if k.endswith("/" + a + ".scene.mbin"):
                f = ensure_tree(k, extr, geodirs)
                if f and f not in seen:
                    out.append((f, "по имени ассета из паков"))
                    seen.add(f)
    for d in (MW, NE, SC):
        for fn in (_g.glob(os.path.join(d, "*__" + a + ".scene.MXML")) +
                   _g.glob(os.path.join(d, a + ".scene.MXML"))):
            if fn not in seen:
                out.append((fn, "по имени ассета из дампов"))
                seen.add(fn)
    return out[:3]


STYLE_DIRS = ("wood", "stone", "timber", "metal", "concrete", "fibreglass", "builders")


def asset_styled_scene(asset, manifest):
    """Легаси-стилевые формы: имя ассета проекта = '<стиль>_<база>' -> сцена
    meshes/<стиль>/<база>.scene.mbin (ТОЧНЫЙ путь в манифесте паков, без фуззи;
    подтверждено аудитом путей 06.07.2026: 'сцена meshes/<стиль>/<база> существует -> OK')."""
    if manifest is None:
        return None
    m = re.match(r"^(%s)_(.+)$" % "|".join(STYLE_DIRS), asset.lower())
    if not m:
        return None
    suff = "/meshes/%s/%s.scene.mbin" % (m.group(1), m.group(2))
    for k in manifest:
        if k.endswith(suff):
            return k
    return None


# ------------------------------------------------------------------ превью (PIL, свой рендер)

def render_preview(obj_path, icon_path, out_png, oid, size=384):
    """Софт-рендер OBJ (ортопроекция 3/4, покраска по нормали) рядом с иконкой игры."""
    import math
    import numpy as np
    from PIL import Image, ImageDraw

    parsed = parse_obj(obj_path)
    if not parsed:
        return False
    verts, tris, _mats, _bbox = parsed
    v = np.asarray(verts, dtype=np.float32)
    t = np.asarray(tris, dtype=np.int64)
    if len(t) == 0:
        return False
    # центрирование и вписывание
    c = (v.min(0) + v.max(0)) / 2.0
    v = v - c
    r = float(np.abs(v).max()) or 1.0
    v = v / r
    # поворот: yaw -35°, pitch -22° (стандартный 3/4 вид); экран: x=vx, y=-vz, глубина=vy
    ya, pa = math.radians(-35), math.radians(-22)
    Ry = np.array([[math.cos(ya), 0, math.sin(ya)], [0, 1, 0], [-math.sin(ya), 0, math.cos(ya)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pa), -math.sin(pa)], [0, math.sin(pa), math.cos(pa)]])
    v = v @ (Rx @ Ry).T
    sx = (v[:, 0] * 0.46 + 0.5) * size
    sy = (-v[:, 2] * 0.46 + 0.5) * size
    depth = v[:, 1]
    # нормали граней + сортировка художника (дальние первыми)
    p0, p1, p2 = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(p1 - p0, p2 - p0)
    ln = np.linalg.norm(n, axis=1); ln[ln == 0] = 1
    n = n / ln[:, None]
    light = np.array([0.4, -0.8, 0.45]); light = light / np.linalg.norm(light)
    shade = 0.25 + 0.75 * np.abs(n @ light)
    order = np.argsort(depth[t].mean(1))
    img = Image.new("RGB", (size, size), (26, 28, 34))
    dr = ImageDraw.Draw(img)
    base = np.array([150, 170, 190], dtype=np.float32)
    MAXTRI = 220000
    step = 1 if len(order) <= MAXTRI else int(len(order) / MAXTRI) + 1
    for i in order[::step]:
        a, b, cc3 = t[i]
        col = tuple(int(x) for x in base * shade[i])
        dr.polygon([(sx[a], sy[a]), (sx[b], sy[b]), (sx[cc3], sy[cc3])], fill=col)
    # склейка с иконкой
    pad, cap = 6, 20
    out = Image.new("RGB", (size * 2 + pad * 3, size + cap + pad * 2), (12, 12, 14))
    out.paste(img, (pad, pad + cap))
    if icon_path and os.path.isfile(icon_path):
        ic = Image.open(icon_path).convert("RGB").resize((size, size))
        out.paste(ic, (size + pad * 2, pad + cap))
    d2 = ImageDraw.Draw(out)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arial.ttf", 14)  # дефолтный шрифт PIL без кириллицы
    except Exception:
        font = None
    d2.text((pad, 3), "%s  —  наш меш | иконка игры" % oid, fill=(220, 220, 220), font=font)
    out.save(out_png)
    return True


# ------------------------------------------------------------------ сборка одной (подпроцесс)

def filter_scene_variant(scene_file, exclude_re, out_file):
    """Копия сцены БЕЗ поддеревьев невыбранных опций дескриптора (узлы-LOCATOR
    '_Body_B' и т.п. — их дети-меши имён опций не несут, режем целым поддеревом)."""
    rx = re.compile(exclude_re, re.I)
    tree = ET.parse(scene_file)

    def prune(node):
        ch = P(node, "Children")
        if ch is None:
            return
        for c in list(ch.findall("Property")):
            if c.get("value") != "TkSceneNodeData":
                continue
            nm = P(c, "Name")
            name = nm.get("value").split("|")[-1] if nm is not None else ""
            if rx.search(name):
                ch.remove(c)
            else:
                prune(c)

    prune(tree.getroot())
    tree.write(out_file, encoding="utf-8")
    return out_file


def build_one(scene_file, asset, staging, relax=False, exclude=""):
    """Внутренний режим: conv2026 как модуль, OUTDIR -> staging.
    relax=True: снять фильтр snap-REF (комнаты spacebase из SnapGroup_*).
    exclude: regex опций дескриптора — конвертеру отдаётся ОТФИЛЬТРОВАННАЯ копия сцены.
    Свою папку доизвлечения подкладываем конвертеру ПЕРВОЙ (только в этом
    процессе — глобальные списки conv2026 на диске не меняются)."""
    import conv2026 as cc
    cc.OUTDIR = staging.replace("\\", "/")
    mw = MW.replace("\\", "/")
    cc.SCDIRS.insert(0, mw)
    cc.GEODIRS.insert(0, mw)
    if relax:
        cc.SKIPREF = SKIPREF_RELAX
    if exclude:
        # 13.07 (Fossils): опции сидят и во ВЛОЖЕННЫХ REF-сценах (biped_bones ->
        # body/necklong) — верхнеуровневой фильтр-копии мало, режем на любой глубине
        cc.EXCLUDE_NODE = re.compile(exclude, re.I)
        scene_file = filter_scene_variant(scene_file, exclude,
                                          os.path.join(staging, "_variant_" + asset + ".scene.MXML"))
    print(cc.build(scene_file, asset))


# ------------------------------------------------------------------ оркестратор

def load_verified():
    f = os.path.join(PROJ, "Content", "NMSData", "verified_meshes.json")
    try:
        with open(f, encoding="utf-8") as fh:
            return {k for k in json.load(fh) if k != "_note"}
    except Exception:
        return set()


def is_verified(verified, asset):
    tail_a = "/" + asset.lower() + "."
    return any(tail_a in k.lower().replace("\\", "/") for k in verified)


def find_icon(oid):
    for d in ICONS_DIRS:
        f = os.path.join(d, oid + ".png")
        if os.path.isfile(f):
            return f
    return None


def main():
    ap = argparse.ArgumentParser(description="СТАНЦИЯ МЕШЕЙ: автосборка + проверки + превью")
    ap.add_argument("--group", default="", help="группа каталога (Category из nms_parts_db.json)")
    ap.add_argument("--ids", default="", help="список ТОЧНЫХ ObjectID через запятую")
    ap.add_argument("--find", default="", help="поиск по ObjectID/имени (подстроки через запятую)")
    ap.add_argument("--all", action="store_true", help="ВСЕ детали каталога")
    ap.add_argument("--limit", type=int, default=0, help="только первые N деталей")
    ap.add_argument("--mbin", default=DEF_MBIN, help="путь к MBINCompiler.exe")
    ap.add_argument("--staging", default=DEF_STAGING, help="staging-папка (НЕ MeshSrc)")
    ap.add_argument("--index", default=DEF_INDEX, help="папка NMS_INDEX (parts_links.json)")
    ap.add_argument("--no-preview", action="store_true", help="без рендера превью")
    ap.add_argument("--force-verified", action="store_true",
                    help="при --promote перезаписывать и запечённые (проверка и так идёт по всем)")
    ap.add_argument("--promote", action="store_true",
                    help="скопировать ЗЕЛЁНЫЕ OBJ в MeshSrc + stage2_import_list.json (ЯВНОЕ действие)")
    ap.add_argument("--build-one", nargs=2, metavar=("SCENE", "ASSET"),
                    help="(внутреннее) собрать одну деталь")
    ap.add_argument("--relax-snap", action="store_true",
                    help="(внутреннее) не скипать SnapGroup-REF (комнаты spacebase)")
    ap.add_argument("--exclude", default="",
                    help="(внутреннее) regex невыбранных опций дескриптора")
    args = ap.parse_args()

    if args.build_one:
        build_one(args.build_one[0], args.build_one[1], args.staging,
                  args.relax_snap, args.exclude)
        return

    if not (args.group or args.ids or args.find or args.all):
        sys.exit("Нужно --group \"Имя группы\", --ids A,B, --find имя или --all")

    links_path = os.path.join(args.index, "parts_links.json")
    if not os.path.isfile(links_path):
        sys.exit("Нет parts_links.json (сначала nms_indexer): " + links_path)
    with open(links_path, encoding="utf-8") as fh:
        _db_links = json.load(fh)
    links = _db_links["parts"]
    scenes_dict = _db_links.get("scenes") or {}
    pcbanks = (_db_links.get("_source") or {}).get("pcbanks") or ""
    with open(os.path.join(PROJ, "Content", "nms_parts_db.json"), encoding="utf-8") as fh:
        db = json.load(fh)
    verified = load_verified()

    # доизвлекатель из паков (если паки и манифест на месте — иначе просто без него)
    extr = None
    manifest_path = os.path.join(args.index, "pak_manifest.json")
    if os.path.isdir(pcbanks) and os.path.isfile(manifest_path) and os.path.isfile(args.mbin):
        with open(manifest_path, encoding="utf-8") as fh:
            extr = Extractor(pcbanks, json.load(fh), args.mbin)
    else:
        print("!! без доизвлечения из паков (нет PCBANKS/манифеста/MBINCompiler) — только локальные дампы")
    os.makedirs(MW, exist_ok=True)
    try:
        import conv2026 as _cc
        geodirs = [MW] + list(_cc.GEODIRS)
    except Exception:
        geodirs = [MW, NE]

    if args.ids:
        want = [i.strip().lstrip("^") for i in args.ids.split(",") if i.strip()]
        by_id = {p["ObjectID"].lstrip("^"): p for p in db}
        parts = [(i, by_id.get(i)) for i in want]
        group_name = "_ids"
    elif args.find:
        toks = [t.strip().upper() for t in args.find.split(",") if t.strip()]
        parts = []
        for p in db:
            oid = p["ObjectID"].lstrip("^")
            hay = (oid + "|" + str(p.get("DisplayName", "")) + "|" + str(p.get("Name", ""))).upper()
            if any(t in hay for t in toks):
                parts.append((oid, p))
        group_name = "_поиск"
        print("Поиск «%s»: найдено %d деталей" % (args.find, len(parts)))
    elif args.all:
        # ВСЕ детали каталога, включая скрытые из панели (запрос юзера 07.07: их 2434)
        parts = [(p["ObjectID"].lstrip("^"), p) for p in db]
        group_name = "_ВСЕ_ДЕТАЛИ"
    else:
        parts = [(p["ObjectID"].lstrip("^"), p) for p in db
                 if p.get("Category") == args.group and p.get("bShowInDrawer", True)]
        group_name = args.group
    if args.limit:
        parts = parts[:args.limit]

    staging = os.path.join(args.staging, group_name.replace("/", "_"))
    prev_dir = os.path.join(staging, "preview")
    trees_dir = os.path.join(staging, "trees")
    os.makedirs(prev_dir, exist_ok=True)
    os.makedirs(trees_dir, exist_ok=True)
    print("СТАНЦИЯ МЕШЕЙ: %s — %d деталей -> %s" % (group_name, len(parts), staging))
    print("(в MeshSrc НИЧЕГО не пишется%s)" % ("" if not args.promote else "; --promote скопирует зелёные в конце"))

    results = []
    for k, (oid, p) in enumerate(parts, 1):
        rec = {"id": oid, "asset": "", "status": "", "flags": [], "build": "", "preview": ""}
        results.append(rec)

        def done(status, *flags):
            rec["status"] = status
            rec["flags"].extend(flags)
            print("  [%d/%d] %-20s %-6s %s" % (k, len(parts), oid, status, "; ".join(rec["flags"])[:150]))

        if p is None:
            done("КРАСН", "нет в nms_parts_db.json"); continue
        asset = (p.get("ModelPath") or "").split("/")[-1].split(".")[0]
        rec["asset"] = asset
        if not asset:
            done("СКИП", "нет меша в каталоге (спец-деталь: партикль/абстракция)"); continue
        # проверка НИЧЕГО не пишет в проект -> проверяем и verified/запечённые
        # (регресс-контроль после обновлений игры); защита от перезаписи — на --promote
        if is_verified(verified, asset):
            rec["flags"].append("запечён (защищён от --promote)")
        if asset.lower() in PATHOLOGICAL:
            done("СКИП", "известный виснущий меш (стопка вариантов) — собирать отдельно"); continue
        custom_note = CUSTOM_BUILT.get(oid)

        # --- авторитетная сцена. ПРИОРИТЕТ = ИГРА (принцип юзера 06.07), подсказки
        # нашего каталога — последними и с клеймом:
        # 1) partstable StyleModels[стиль детали] (игра, из parts_links);
        # 2) placement-entity: Rules(NotSnapped)->PartID->partstable[стиль] (игра);
        # 3) сцена из parts_links (игра; часто _placement — превью);
        # 4) под-части/алиасы (ПОДСКАЗКА, решения юзера 01.07);
        # 5) имя ассета '<стиль>_<база>' -> meshes/<стиль>/<база> (ПОДСКАЗКА каталога).
        link = links.get(oid) or links.get(ALIAS_PART.get(oid, ""))
        obj_style = ((link or {}).get("object") or {}).get("Style") or "None"
        styles_map = (link or {}).get("styles") or {}
        # 0.5) ДЕФОЛТ placement-entity АВТОРИТЕТНЕЕ styles-карты индексера (Corvette
        # B_WALL_* 12.07: entity мульти-PartID NS/EW×стиль-модуля, дефолт NotSnapped =
        # _BIGGS_WALL_EW_A с каютой/кухней, а индексер записал в styles первую NS-строку
        # — «вся подгруппа одна голая стена»). Для обычных деталей entity-дефолт и
        # styles совпадают; для сборок/без-ATTACHMENT возвращается None — идём как раньше.
        scene_game = None
        placement = ((link or {}).get("object") or {}).get("PlacementScene") or (link or {}).get("scene")
        if placement:
            pid2 = placement_entity_partid(placement, extr)
            if pid2:
                pst = load_partstable(args.index).get(pid2) or {}
                sc2 = pst.get(obj_style) or pst.get("None") or (next(iter(pst.values())) if pst else None)
                if sc2:
                    scene_game = sc2
                    if styles_map.get(obj_style) and styles_map.get(obj_style) != sc2:
                        rec["flags"].append("entity-дефолт (%s) ≠ styles индексера — берём entity" % pid2)
                    else:
                        rec["flags"].append("сцена из placement-entity игры (%s)" % pid2)
        if not scene_game:
            scene_game = styles_map.get(obj_style)
        if not scene_game and link:
            scene_game = link.get("scene")
        exclude_re = ""
        if not scene_game:
            # ★ ВАРИАНТ ПО ТАБЛИЦАМ КАСТОМИЗАЦИИ ИГРЫ (фоссилы, 13.07): явный белый
            # список узлов на каждый ItemID — авторитетнее буквенной формулы
            cv = customisation_variant(oid, extr, geodirs, args.index)
            if cv:
                scene_game, exclude_re, vnote = cv
                rec["flags"].append(vnote)
        if not scene_game:
            # ★ PARTSTABLE ПО PART-ID (витрины фоссилов 13.07: каталожные
            # FOS_BODY_DISPLAY[_FLOOR/_WALL] = part-ID _FOS_* с СОБСТВЕННОЙ сценой;
            # эвристика по имени ассета сажала их на чужой skull_display)
            pst_probe = load_partstable(args.index).get("_" + oid) or {}
            sc_probe = pst_probe.get(obj_style) or pst_probe.get("None") \
                or (next(iter(pst_probe.values())) if pst_probe else None)
            if sc_probe:
                scene_game = sc_probe
                rec["flags"].append("partstable по part-ID _%s" % oid)
        if not scene_game:
            # ВАРИАНТ ПО ДЕСКРИПТОРУ ИГРЫ (буквенная формула — фолбэк для семей,
            # которых нет в таблицах кастомизации)
            dv = descriptor_variant(oid, links, extr, geodirs)
            if dv:
                scene_game, exclude_re, vnote = dv
                rec["flags"].append(vnote)
        if not scene_game and oid in SUBPART_SCENE:
            base = SUBPART_SCENE[oid]
            scene_game = next((k for k in scenes_dict if k.endswith("/" + base + ".scene.mbin")), None)
            if not scene_game:
                scene_game = "models/planets/biomes/common/buildings/parts/buildableparts/spacebase/meshes/%s.scene.mbin" % base
            if scene_game:
                rec["flags"].append("ПОДСКАЗКА (решение юзера 01.07): сцена под-части")
        if not scene_game:
            scene_game = asset_styled_scene(asset, extr.man if extr else None)
            if scene_game:
                rec["flags"].append("ПОДСКАЗКА каталога: сцена по имени ассета")
        scene_file = None
        if not scene_game:
            # под-части каталога без своей записи в objectstable (трубы, потолки комнат,
            # варианты) — сцена ТОЧНО по имени ассета (паки, затем локальные дампы)
            cands = alt_scene_candidates(asset, "", extr, geodirs)
            if cands:
                scene_file, why = cands[0]
                rec["flags"].append("нет в objectstable; сцена %s" % why)
            else:
                done("КРАСН", "нет в parts_links и сцены по имени ассета не нашлось"); continue
        n0 = extr.n_extracted if extr else 0
        l0 = len(LEGACY_USED)
        if scene_game:
            scene_file = ensure_tree(scene_game, extr, geodirs)
        # ★ РАНТАЙМ-КОНФИГ (GAMETABLE): сцена без MESH → модель за entity-конфигом
        # (game-table). Трассируем и подменяем на настоящую сцену — автоловля цепочки.
        if scene_file and scene_has_no_mesh(scene_file):
            real = runtime_config_scene(scene_file, extr)
            if real:
                rf = ensure_tree(real, extr, geodirs)
                if rf and not scene_has_no_mesh(rf):
                    scene_game, scene_file = real, rf
                    rec["flags"].append("рантайм-конфиг игры (game-table) → " + real.split("/")[-1])
        # ★ СБОРКА по entity-правилам (комнаты корвета B_HAB_*/B_LAN_B): placement-стуб
        # без единого PartID — синтезируем сцену из NotSnapped-частей на локаторах
        if scene_file and scene_has_no_mesh(scene_file):
            placement2 = ((link or {}).get("object") or {}).get("PlacementScene") or (link or {}).get("scene")
            if placement2:
                asm = entity_assembly_scene(oid, placement2, obj_style, extr, args.index, geodirs,
                                            os.path.join(staging, "_assembly_" + oid + ".scene.MXML"))
                if asm:
                    scene_file = asm[0]
                    rec["flags"].append("СБОРКА по entity-правилам: %d частей%s" % (
                        asm[1], ("; без сцены: " + ",".join(asm[2])) if asm[2] else ""))
        if extr and extr.n_extracted > n0:
            rec["flags"].append("извлечено из игры: %d файлов" % (extr.n_extracted - n0))
        if len(LEGACY_USED) > l0:
            rec["flags"].append("из СТАРЫХ дампов: %d файлов (в паках не нашлись)" % (len(LEGACY_USED) - l0))
        if not scene_file:
            done("КРАСН", "сцены нет ни локально, ни в паках: " + scene_game); continue

        hulls = (link or {}).get("hulls") or {}
        hull = hulls.get(obj_style) or hulls.get("None") or (next(iter(hulls.values())) if hulls else None)
        obj_path = os.path.join(staging, asset + ".obj")

        def attempt(scene_f, relax=False):
            """Сборка в подпроцессе (таймаут против зависаний) + все проверки."""
            out = {"fatal": None, "red": [], "yellow": [], "info": [], "build": ""}
            try:
                cmd = [sys.executable, os.path.abspath(__file__),
                       "--build-one", scene_f, asset, "--staging", staging]
                if relax:
                    cmd.append("--relax-snap")
                if exclude_re:
                    cmd += ["--exclude", exclude_re]
                cp = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=BUILD_TIMEOUT)
                out["build"] = (cp.stdout or "").strip()[-200:]
            except subprocess.TimeoutExpired:
                out["fatal"] = "ЗАВИСЛА сборка (> %d c)" % BUILD_TIMEOUT
                return out
            if "ПУСТО" in out["build"]:
                out["fatal"] = "ПУСТО (0 мешей LOD0)"
                return out
            if cp.returncode != 0 or not os.path.isfile(obj_path):
                out["fatal"] = "ошибка конвертации: " + (cp.stderr or out["build"])[-160:]
                return out
            parsed = parse_obj(obj_path)
            if not parsed:
                out["fatal"] = "OBJ пустой/не читается"
                return out
            _verts, tris_obj, mats_obj, bbox = parsed
            out["sz_mb"] = os.path.getsize(obj_path) / 1048576.0
            if out["sz_mb"] > GIANT_MB:
                out["yellow"].append("гигант %.0f МБ (импортировать отдельно/LOD)" % out["sz_mb"])

            creds = []  # расхождения СО СЦЕНОЙ (смягчаются при совпадении с принятым мешом)
            # 1) треугольники vs сцена игры
            tris_game, mats_game = scene_lod0(scene_f, relax=relax, extra=exclude_re)
            out["tris"] = {"obj": len(tris_obj), "game": tris_game}
            if tris_game and abs(len(tris_obj) - tris_game) > max(4, tris_game * 0.02):
                creds.append("ТРЕУГ: obj %d vs игра %d" % (len(tris_obj), tris_game))
            # 2) слоты/материалы vs сцена
            game_set = [m for m in mats_game if m != "default"]
            out["slots"] = {"obj": mats_obj, "game": game_set}
            if game_set and sorted(mats_obj) != sorted(game_set):
                miss = [m for m in game_set if m not in mats_obj]
                extra = [m for m in mats_obj if m not in game_set]
                if miss:
                    creds.append("СЛОТЫ: нет %s" % ",".join(miss[:4]))
                if extra:
                    out["yellow"].append("СЛОТЫ: лишние %s" % ",".join(extra[:4]))
            # 3) хулл MagicData
            hred, hyel = [], []
            nohull = None
            if hull:
                _mn, _mx, size_nms = obj_bbox_to_nms(bbox)
                out["bbox_nms"] = {"size": size_nms, "hull": hull["size"]}
                hred, hyel = hull_check(size_nms, hull)
            else:
                nohull = "нет хулла MagicData (сверить глазом)"
            # совпадение с ПРИНЯТЫМ проектным мешом = сильный признак правильности:
            # хулл-вопросы гасятся, расхождения со сценой понижаются до ЖЁЛТ (на глаз)
            if project_match(asset, parsed):
                out["info"].append("= принятый проектный меш 1:1")
                if hred or hyel:
                    out["info"].append("хулл-люфт %d (норма: хулл с запасом)" % (len(hred) + len(hyel)))
                out["yellow"] = [c + " (но меш = принятый)" for c in creds] + out["yellow"]
            else:
                out["red"].extend(creds)
                # ХУЛЛ ОБЩИЙ НА СЕМЬЮ СТИЛЕЙ (Corvette 12.07): autogen-таблица держит
                # ОДИН хулл на PartID направления (_BIGGS_EXTSTR_1X1_NE), а стилей у
                # детали 21 — хулл = конверт максимального стиля, «меньше хулла» у
                # меньших стилей НЕ дефект. Понижаем до ЖЁЛТ с пометкой.
                _hull_szs = {tuple(h.get("size") or ()) for h in hulls.values()} if hulls else set()
                if hred and len(styles_map) > 1 and len(_hull_szs) <= 1:
                    out["yellow"].extend(h + " [хулл общий на семью стилей — глазом]" for h in hred)
                else:
                    out["red"].extend(hred)
                out["yellow"].extend(hyel)
                if nohull:
                    out["yellow"].append(nohull)
            return out

        # лесенка попыток: обычная сборка -> альтернативная сцена (по имени ассета) ->
        # SnapGroup-relax (комнаты) -> свежая геометрия из паков при расхождении треуг.
        relax_used = False
        res = attempt(scene_file)
        if res["fatal"] and "ПУСТО" in res["fatal"]:
            for cand, why in alt_scene_candidates(asset, scene_file, extr, geodirs):
                r2 = attempt(cand)
                if not r2["fatal"]:
                    r2["info"].append("альтернативная сцена (%s)" % why)
                    scene_file, res = cand, r2
                    break
        if res["fatal"] and "ПУСТО" in res["fatal"]:
            r2 = attempt(scene_file, relax=True)
            if not r2["fatal"]:
                r2["info"].append("собрано со SnapGroup-REF (комната spacebase)")
                res, relax_used = r2, True
        # (повтор со «свежей геометрией» больше не нужен: свежесть из паков — всегда)
        rec["build"] = res["build"]
        if res["fatal"]:
            done("КРАСН", res["fatal"]); continue
        if custom_note:
            # в проекте деталь намеренно собрана ИНАЧЕ (решение юзера) — расхождения
            # свежей сборки со сценой ОЖИДАЕМЫ; вся деталь = ЖЁЛТ «не продвигать»,
            # чтобы не попала в зелёный import_list и не перезаписала ручную сборку
            res["yellow"] = ["в проекте собран ИНАЧЕ (%s) — не продвигать" % custom_note] \
                            + res["red"] + res["yellow"]
            res["red"] = []
        for k2 in ("tris", "slots", "bbox_nms"):
            if k2 in res:
                rec[k2] = res[k2]

        # превью рядом с иконкой
        if not args.no_preview and res.get("sz_mb", 0) <= GIANT_MB:
            icon = find_icon(oid)
            png = os.path.join(prev_dir, oid + ".png")
            try:
                if render_preview(obj_path, icon, png, oid):
                    rec["preview"] = png
                if not icon:
                    res["yellow"].append("нет иконки игры")
            except Exception as e:
                res["yellow"].append("превью не отрисовалось: %s" % e)

        # ЕДИНОЕ ДРЕВО ДЕТАЛИ: узлы -> материал -> текстуры -> цвет (1:1 из паков);
        # отсутствие материала/текстуры в паках — сразу в вердикт
        try:
            tree, miss_m, miss_t = part_tree(oid, asset, scene_game or "", scene_file,
                                             relax_used, extr, rec.get("slots", {}).get("obj"),
                                             args.index, exclude_re)
            with open(os.path.join(trees_dir, oid + ".json"), "w", encoding="utf-8") as fh:
                json.dump(tree, fh, ensure_ascii=False, indent=1)
            with open(os.path.join(trees_dir, oid + ".txt"), "w", encoding="utf-8") as fh:
                fh.write(render_tree_txt(tree) + "\n")
            rec["tree"] = os.path.join(trees_dir, oid + ".json")
            if miss_m:
                res["red"].append("МАТЕРИАЛ нет в паках: %s" % ",".join(miss_m[:3]))
            if miss_t:
                res["yellow"].append("ТЕКСТУРА нет в паках: %s" % ",".join(miss_t[:3]))
        except Exception as e:
            res["yellow"].append("дерево не построилось: %s" % e)

        rec["flags"] = res["red"] + res["yellow"] + res["info"] + rec["flags"]
        done("КРАСН" if res["red"] else ("ЖЁЛТ" if res["yellow"] else "ЗЕЛ"))

    # ---------------- отчёт
    order = {"КРАСН": 0, "ЖЁЛТ": 1, "ЗЕЛ": 2, "СКИП": 3}
    results.sort(key=lambda r: (order.get(r["status"], 0), r["id"]))
    cnt = {}
    for r in results:
        cnt[r["status"]] = cnt.get(r["status"], 0) + 1
    lines = ["СТАНЦИЯ МЕШЕЙ — отчёт по группе: %s" % group_name,
             "Итого: " + "  ".join("%s=%d" % kv for kv in sorted(cnt.items())),
             "Staging: %s (MeshSrc НЕ тронут)" % staging,
             "Превью (наш меш | иконка): %s" % prev_dir,
             "Деревья деталей (узлы/материалы/текстуры/цвет): %s" % trees_dir, ""]
    for r in results:
        lines.append("%-6s %-20s %-26s %s" % (r["status"], r["id"], r["asset"],
                                              "; ".join(r["flags"])))
    report = "\n".join(lines)
    with open(os.path.join(staging, "ОТЧЁТ.txt"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    with open(os.path.join(staging, "meshwork_report.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    green = [r["asset"] for r in results if r["status"] == "ЗЕЛ"]
    with open(os.path.join(staging, "import_list.json"), "w", encoding="utf-8") as fh:
        json.dump(green, fh, ensure_ascii=False, indent=1)
    print()
    print("Итого: " + "  ".join("%s=%d" % kv for kv in sorted(cnt.items())))
    print("Отчёт: %s\\ОТЧЁТ.txt; превью: %s" % (staging, prev_dir))
    print("Зелёных к импорту: %d -> import_list.json" % len(green))

    # ---------------- promote (ЯВНОЕ действие; ЕДИНСТВЕННОЕ место записи в проект)
    if args.promote and green:
        import shutil
        # защита проверенного: запечённые НЕ перезаписываем без --force-verified
        prot = [] if args.force_verified else [a for a in green if is_verified(verified, a)]
        todo = [a for a in green if a not in prot]
        if prot:
            print("PROMOTE: %d запечённых пропущено (защита; --force-verified чтобы перезаписать): %s"
                  % (len(prot), ", ".join(prot[:8]) + ("..." if len(prot) > 8 else "")))
        dst_dir = os.path.join(PROJ, "MeshSrc")
        for a in todo:
            shutil.copy2(os.path.join(staging, a + ".obj"), os.path.join(dst_dir, a + ".obj"))
        lst = os.path.join(PROJ, "Content", "Python", "stage2_import_list.json")
        with open(lst, "w", encoding="utf-8") as fh:
            json.dump(todo, fh, ensure_ascii=False, indent=1)
        print("PROMOTE: %d OBJ скопировано в MeshSrc + %s" % (len(todo), lst))
        print("Импорт (редактор ЗАКРЫТ): UnrealEditor.exe <uproject> "
              "-ExecutePythonScript=<Content\\Python\\reimport_stage2.py> -nosound -unattended -nosplash")


if __name__ == "__main__":
    main()
