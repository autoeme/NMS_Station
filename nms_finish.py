# -*- coding: utf-8 -*-
"""
nms_finish.py — слои мультитекстур и отделка по умолчанию (ОТДЕЛЬНАЯ программа).

Читает ГОТОВЫЙ выход индексатора (NMS_INDEX: pak_manifest.json + parts_links.json +
raw\\...) и сами паки игры. Код nms_indexer/nms_lookup/NMS_Station НЕ трогает и
parts_links.json НЕ меняет — пишет СВОЙ файл finish_layers.json.

Три вопроса (все ответы 1:1 из данных игры):
  1. Сколько слоёв в каждом атласе-массиве. ФАКТ ИЗ ПАКОВ (проверено 06.07.2026):
     мультитекстуры игры лежат в ЛЕГАСИ-заголовке DDS (fourCC DXT1/DXT5/ATI1/ATI2,
     НЕ DX10) — число слоёв = uint32 dwDepth по смещению 24 (флаг DDSD_DEPTH
     0x800000 в dwFlags@8 выставлен; depth=0 у обычных текстур = 1 слой).
     Вариант DX10 (uint32 arraySize@140 после DXT10-расширения) поддержан
     как запасной путь на случай перепаковки формата.
  2. Задан ли слой прямо в материале: юниформа gfMultiTextureIndex в
     Uniforms_Float файла .MATERIAL.MXML. Если её нет — слой РАНТАЙМОВЫЙ
     (= отделка из UserData, байт 3), это фиксируется текстом по каждому материалу.
  3. Отделка/палитра по умолчанию у детали: поля DefaultMaterialId и
     DefaultColourPaletteId записей objectstable (своя стриминг-читалка,
     КОПИЯ по образцу nms_indexer.py — индексатор не модифицируется).

★ ДОБОР 07.07.2026 (после сверки с симулятором nms_runtime.py, который нашёл
~190 деталей с массивами-атласами, отсутствовавших в parts-разделе):
  а) связь деталь→материалы теперь собирается РЕКУРСИВНО по сценам: стилевые
     сцены partstable + сцена детали + PlacementScene, с обходом REFERENCE-узлов
     (граф сцен из parts_links; сцены, которых в графе нет, допарсиваются из
     raw/паков). Пример закрытой дыры: CONTAINER0 — сцена сама без мешей, ящик
     и цифра приходят по REFERENCE (cubecrate + number_0), их материалы раньше
     терялись.
  б) материал считается «мультитекстурным» не только по флагу _F55_MULTITEXTURE,
     но и по ФАКТУ: любой его сэмплер ссылается на DDS со слоями (layers>1).
     Для материалов БЕЗ _F55 со слоёным атласом слой из данных НЕ определён —
     фиксируется явной пометкой (не гадаем).

Использование:
    python nms_finish.py [--index ПАПКА] [--pcbanks ПАПКА] [--mbin EXE] [--out ФАЙЛ]

По умолчанию: --index Desktop\\NMS_INDEX, --pcbanks из parts_links.json (_source),
--out <index>\\finish_layers.json.

Самопроверка (посчитано по базе 06.07.2026): материалов с _F55_MULTITEXTURE = 666,
деталей СТАРЫМ методом (scene_materials ∩ F55) = 967; известные слои: тримы = 2,
basebuildingexterior = 4, biggs = 4.
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
from functools import lru_cache

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEF_INDEX = r"C:\Users\User\Desktop\NMS_INDEX"
DEF_MBIN = r"C:\Users\User\Desktop\MBINCompiler\MBINCompiler.exe"
CHUNK = 0x10000

MULTITEX_FLAG = "_F55_MULTITEXTURE"
UNIFORM_NAME = "gfMultiTextureIndex"
TABLE_OBJECTS = "metadata/reality/tables/basebuildingobjectstable.mbin"

# контрольные цифры для самопроверки (могут поплыть с обновлением игры — тогда предупреждаем)
EXPECT_F55_MATERIALS = 666
EXPECT_F55_PARTS_OLD = 967   # старый метод: scene_materials ∩ F55 (для регресс-контроля)

WALK_DEPTH_MAX = 10          # предохранитель рекурсии REFERENCE

# деревья meshwork (ночной прогон): entity-резолвленные материалы деталей корвета/
# фрейтера/диагональных стен, до которых обход сцен не дотягивается (реальный меш
# за GcBasePlacementRule → PartID → partstable, а не за REFERENCE). Переиспользуем
# готовый резолвер meshwork через его выход, НЕ форкая его.
DEF_TREES = r"C:\Users\User\Desktop\MESHWORK_STAGING\_ВСЕ_ДЕТАЛИ\trees"


# ------------------------------------------------------------------ HGPAK
# КОПИЯ читальщика из nms_indexer.py (правило задачи: чужой код не править).
# Грабли формата: несжатые паки (comp=False, напр. NMSARC.audio.pak) НЕ имеют
# таблицы чанков — данные сплошняком сразу после таблицы файлов.

class HGPak(object):
    def __init__(self, path):
        import zstandard
        self.path = path
        self.f = open(path, "rb")
        assert self.f.read(5) == b"HGPAK", "не HGPAK: " + path
        self.f.seek(8)
        (self.ver, self.n_files, self.n_chunks, self.comp,
         self.data_off) = struct.unpack("<QQQ?7xQ", self.f.read(0x28))
        self.finfo = []
        for _ in range(self.n_files):
            _h, off, size = struct.unpack("<16s2Q", self.f.read(0x20))
            self.finfo.append((off, size))
        if self.comp:
            sizes = struct.unpack("<%dQ" % self.n_chunks, self.f.read(8 * self.n_chunks))
            self.sizes = sizes
            self.offs = []
            cur = self.data_off
            for s in sizes:
                self.offs.append(cur)
                cur += ((s + 0xF) // 0x10) * 0x10
        else:
            self.sizes = None
        self._dctx = zstandard.ZstdDecompressor()
        self._chunk = lru_cache(maxsize=64)(self._chunk_raw)

    def _chunk_raw(self, i):
        self.f.seek(self.offs[i])
        data = self.f.read(self.sizes[i])
        if not self.comp:
            return data
        try:
            return self._dctx.decompress(data, max_output_size=CHUNK)
        except Exception:
            if len(data) == CHUNK:
                return data  # несжатый блок
            raise

    def read_span(self, off, size):
        if not self.comp:
            self.f.seek(self.data_off + off)
            return self.f.read(size)
        out = bytearray()
        c0, c1 = off // CHUNK, (off + size - 1) // CHUNK
        for ci in range(c0, c1 + 1):
            d = self._chunk(ci)
            a = off - ci * CHUNK if ci == c0 else 0
            b = off + size - ci * CHUNK if ci == c1 else len(d)
            out += d[a:b]
        return bytes(out)

    def extract(self, index):
        off, size = self.finfo[index + 1]  # +1: файл 0 = манифест имён
        return self.read_span(off - self.data_off, size)

    def read_head(self, index, n=256):
        """Только первые n байт файла — для чтения заголовков DDS без всего файла."""
        off, size = self.finfo[index + 1]
        return self.read_span(off - self.data_off, min(size, n))

    def close(self):
        self.f.close()


_open_paks = {}


def get_pak(pcbanks, pk):
    if pk not in _open_paks:
        _open_paks[pk] = HGPak(os.path.join(pcbanks, pk))
    return _open_paks[pk]


def norm(p):
    return p.replace("\\", "/").lower()


def _long(p):
    """Windows: префикс \\\\?\\ снимает лимит пути 260 символов."""
    if os.name == "nt":
        p2 = os.path.abspath(p)
        if not p2.startswith("\\\\?\\"):
            return "\\\\?\\" + p2
    return p


# ------------------------------------------------------------------ извлечение+декод
# по образцу nms_indexer.py: кладём в тот же raw\ (раскладка совместима,
# индексатор при следующем прогоне увидит файлы как уже извлечённые)

def extract_one(pcbanks, manifest, game_path, raw_dir):
    key = norm(game_path)
    rec = manifest.get(key)
    if rec is None:
        return None
    dst = os.path.join(raw_dir, key.replace("/", os.sep))
    if not os.path.isfile(_long(dst)):
        os.makedirs(_long(os.path.dirname(dst)), exist_ok=True)
        data = get_pak(pcbanks, rec["pak"]).extract(rec["index"])
        with open(_long(dst), "wb") as fh:
            fh.write(data)
    return dst


def decode_one(mbin_exe, mbin_path):
    mx = re.sub(r"\.mbin(\.pc)?$", ".MXML", mbin_path, flags=re.I)
    if not os.path.isfile(mx):
        subprocess.run([mbin_exe, "-y", "-q", mbin_path], capture_output=True, timeout=600)
    return mx if os.path.isfile(mx) else None


# ------------------------------------------------------------------ DDS-заголовок

DDSD_DEPTH = 0x800000


def dds_info(head):
    """Разбор заголовка DDS. Возвращает dict с layers (число слоёв).
    В паках NMS мультитекстуры = легаси-заголовок: слои в dwDepth@24
    (при флаге DDSD_DEPTH); запасной путь — DX10 arraySize@140."""
    if len(head) < 128 or head[:4] != b"DDS ":
        return None
    flags, height, width, _pitch, depth = struct.unpack_from("<5I", head, 8)
    fourcc = head[84:88]
    info = {"fourcc": fourcc.decode("ascii", "replace").strip("\x00"),
            "width": width, "height": height, "depth": depth}
    if fourcc == b"DX10" and len(head) >= 148:
        dxgi, _rdim, _misc, arr = struct.unpack_from("<4I", head, 128)
        info["dxgi_format"] = dxgi
        info["layers"] = arr if arr > 0 else 1
    elif (flags & DDSD_DEPTH) and depth > 1:
        info["layers"] = depth
    else:
        info["layers"] = 1
    return info


# ------------------------------------------------------------------ материал: юниформа слоя

def material_layer(mxml_path):
    """Ищет юниформу gfMultiTextureIndex в Uniforms_Float декодированного материала.
    Возвращает (layer|None, источник-текстом)."""
    try:
        with open(mxml_path, "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError as e:
        return None, "нет MXML: %s" % e
    m = re.search(r'value="%s"\s*/>.*?name="X" value="([^"]*)"' % UNIFORM_NAME, txt, re.S)
    if m:
        try:
            return int(float(m.group(1))), "материал (Uniforms_Float %s)" % UNIFORM_NAME
        except ValueError:
            return None, "юниформа %s есть, но X не число: %r" % (UNIFORM_NAME, m.group(1))
    return None, "рантайм: отделка из UserData байт 3 (в материале юниформы нет)"


# ------------------------------------------------------------------ разбор сцен/материалов из MXML
# (для файлов, которых нет в графе индексатора — допарсиваем сами, 1:1 из игры)

# пары "Name=MATERIAL/SCENEGRAPH → Value=путь" в атрибутах узлов сцены
SCENE_PAIR_RE = re.compile(
    r'name="Name" value="(MATERIAL|SCENEGRAPH)"\s*/>\s*<Property name="Value" value="([^"]*)"')

# сэмплеры материала: Name=g*Map → Map=путь DDS (до 200 симв. между полями)
MAT_SAMPLER_RE = re.compile(
    r'name="Name" value="(g\w+)"[\s\S]{0,200}?name="Map" value="([^"]*)"')


def parse_scene_mxml(path):
    """Декодированная сцена → (set материалов norm, set REFERENCE-сцен norm)."""
    try:
        with open(_long(path), "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return set(), set()
    mats, refs = set(), set()
    for kind, val in SCENE_PAIR_RE.findall(txt):
        if not val:
            continue
        if kind == "MATERIAL":
            mats.add(norm(val))
        else:
            refs.add(norm(val))
    return mats, refs


def tree_multitex_materials(tree_dir, oid):
    """Из дерева meshwork <ID>.json — материалы детали с флагом _F55_MULTITEXTURE
    (система отделки игры). Возвращает {mat_norm: {class,diffuse,diffuse_layers}} или
    {} если дерева нет. Данные дерева игро-производны (meshwork декодировал материалы
    из паков) — доверяем его флагам/слоям без пере-декода. Слоёный атлас БЕЗ _F55
    (световой куки) финишем НЕ считаем — как и в material_multitex."""
    if not tree_dir:
        return {}
    fp = os.path.join(tree_dir, oid + ".json")
    if not os.path.isfile(_long(fp)):
        return {}
    try:
        with open(_long(fp), "r", encoding="utf-8") as fh:
            tree = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for mk, m in (tree.get("materials") or {}).items():
        flags = m.get("flags") or []
        if MULTITEX_FLAG not in flags:
            continue
        layers = 0
        diffuse = ""
        for s in m.get("samplers") or []:
            ly = s.get("layers") or 1
            if ly > layers:
                layers = ly
            if s.get("name") == "gDiffuseMap":
                diffuse = s.get("map", "")
        out[norm(mk)] = {
            "class": m.get("class", ""),
            "diffuse": diffuse,
            "diffuse_layers": layers or None,
            "flags_f55": True,
        }
    return out


TABLE_PARTS = "metadata/reality/tables/basebuildingpartstable.mbin"
_PT_ID_RE = re.compile(r'<Property name="ID" value="(_[^"]+)"')
_PT_FN_RE = re.compile(r'<Property name="Filename" value="([^"]+\.SCENE\.MBIN)"', re.I)
# ATTACHMENT в placement-сцене: путь к *.ENTITY.MBIN
_SCENE_ATT_RE = re.compile(
    r'name="Name" value="ATTACHMENT" />\s*\n\s*<Property name="Value" value="([^"]+\.ENTITY\.MBIN)"', re.I)


def parse_partstable(mxml):
    """partstable.MXML -> {part_id_lower: set(сцен norm)}. part_id вида '_WALLB'."""
    with open(_long(mxml), "r", encoding="utf-8", errors="replace") as fh:
        txt = fh.read()
    ids = [(m.group(1), m.start()) for m in _PT_ID_RE.finditer(txt)]
    out = {}
    for i, (pid, pos) in enumerate(ids):
        end = ids[i + 1][1] if i + 1 < len(ids) else len(txt)
        scenes = set(norm(f) for f in _PT_FN_RE.findall(txt[pos:end]))
        if scenes:
            out.setdefault(pid.lower(), set()).update(scenes)
    return out


def placement_rule_partids(placement_scene, raw, pcbanks, manifest, mbin_exe):
    """PartID сборки детали-КОМНАТЫ из GcBasePlacementRule entity placement-сцены.
    Возвращает [] если это НЕ сборка (правила на ОДНОМ локаторе = подмена одной детали,
    напр. стена _WALLB→дверь при стыковке — такие берёт резолвер meshwork/дерево, не мы).
    Сборка (комната фрейтера/корвет-хаб) = правила на РАЗНЫХ локаторах (пол+стены+углы) —
    возвращаем PartID ВСЕХ (весь набор комнаты). Зеркально дискриминатору meshwork."""
    mx = re.sub(r"\.mbin(\.pc)?$", ".MXML",
                os.path.join(raw, norm(placement_scene).replace("/", os.sep)), flags=re.I)
    if not os.path.isfile(_long(mx)):
        mb = extract_one(pcbanks, manifest, placement_scene, raw)
        mx = decode_one(mbin_exe, mb) if mb else None
    if not mx or not os.path.isfile(_long(mx)):
        return []
    stxt = open(_long(mx), "r", encoding="utf-8", errors="replace").read()
    att = _SCENE_ATT_RE.findall(stxt)
    if not att:
        return []
    emx = re.sub(r"\.mbin(\.pc)?$", ".MXML",
                 os.path.join(raw, norm(att[0]).replace("/", os.sep)), flags=re.I)
    if not os.path.isfile(_long(emx)):
        mb = extract_one(pcbanks, manifest, att[0], raw)
        emx = decode_one(mbin_exe, mb) if mb else None
    if not emx or not os.path.isfile(_long(emx)):
        return []
    etxt = open(_long(emx), "r", encoding="utf-8", errors="replace").read()
    rules = []
    for block in re.split(r'value="GcBasePlacementRule"', etxt)[1:]:
        pm = re.search(r'<Property name="PartID" value="([^"]+)"', block)
        lm = re.search(r'<Property name="PositionLocator" value="([^"]*)"', block)
        if pm:
            rules.append((pm.group(1), lm.group(1) if lm else ""))
    if len({loc for _p, loc in rules}) <= 1:
        return []   # одно-локаторная подмена — не сборка
    return [p for p, _loc in rules]


def strip_placement(scene):
    """'..._placement.scene.mbin' -> '....scene.mbin' (базовая сцена с реальными мешами).
    Проверено 07.07.2026: у части деталей (окна, диагональные стены-варианты, флаги,
    силосы, деревья-анализаторы) базовой сцены без _placement НЕТ, тогда strip
    ничего не добавит; но там где она есть — там и лежит настоящая геометрия/материалы.
    Тот же приём у резолвера meshwork ('извлечь базовую минус _placement')."""
    return re.sub(r"_placement(\.scene\.mbin)$", r"\1", scene, flags=re.I)


def parse_material_mxml(path):
    """Декодированный материал → {'flags_f55': bool, 'samplers': [{'name','map'}]}."""
    try:
        with open(_long(path), "r", encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
    except OSError:
        return None
    return {
        "flags_f55": MULTITEX_FLAG in txt,
        "samplers": [{"name": n, "map": m} for n, m in MAT_SAMPLER_RE.findall(txt) if m],
    }


class SceneWalker(object):
    """Рекурсивный сбор материалов детали по сценам.
    Источник графа — parts_links['scenes'] (meshes/references); сцены вне графа
    допарсиваются из raw (при необходимости извлекаются из паков и декодируются)."""

    def __init__(self, db_scenes, raw, pcbanks, manifest, mbin_exe):
        self.db_scenes = db_scenes
        self.raw = raw
        self.pcbanks = pcbanks
        self.manifest = manifest
        self.mbin = mbin_exe
        self.cache = {}          # scene_norm -> (frozenset mats, frozenset refs)
        self.n_extra_parsed = 0  # сцен допарсено из MXML (вне графа индексатора)
        self.n_missing = 0       # сцен не найдено нигде

    def scene_info(self, sn):
        if sn in self.cache:
            return self.cache[sn]
        rec = self.db_scenes.get(sn)
        if rec is not None:
            mats = frozenset(norm(m["material"]) for m in rec.get("meshes") or []
                             if m.get("material"))
            refs = frozenset(norm(r["scene"]) for r in rec.get("references") or []
                             if r.get("scene"))
        else:
            mxml = re.sub(r"\.mbin(\.pc)?$", ".MXML",
                          os.path.join(self.raw, sn.replace("/", os.sep)), flags=re.I)
            if not os.path.isfile(_long(mxml)):
                mb = extract_one(self.pcbanks, self.manifest, sn, self.raw)
                mxml = decode_one(self.mbin, mb) if mb else None
            if mxml and os.path.isfile(_long(mxml)):
                m, r = parse_scene_mxml(mxml)
                mats, refs = frozenset(m), frozenset(r)
                self.n_extra_parsed += 1
            else:
                mats, refs = frozenset(), frozenset()
                self.n_missing += 1
        self.cache[sn] = (mats, refs)
        return self.cache[sn]

    def collect(self, seed_scenes):
        """Все материалы, достижимые из seed-сцен по REFERENCE (глубина ≤ WALK_DEPTH_MAX)."""
        out, seen = set(), set()
        frontier = [(norm(s), 0) for s in seed_scenes if s]
        while frontier:
            sn, d = frontier.pop()
            if sn in seen or d > WALK_DEPTH_MAX:
                continue
            seen.add(sn)
            mats, refs = self.scene_info(sn)
            out |= mats
            for r in refs:
                frontier.append((r, d + 1))
        return out


# ------------------------------------------------------------------ objectstable: дефолты
# стриминг-читалка записей — КОПИЯ по образцу nms_indexer.py

def iter_entries(path, open_prefix):
    close_line = open_prefix.split("<")[0] + "</Property>"
    entry, inside = [], False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            ls = line.rstrip("\n")
            if ls.startswith(open_prefix):
                entry, inside = [ls], True
                continue
            if inside:
                entry.append(ls)
                if ls == close_line:
                    yield "\n".join(entry)
                    inside = False


def prop_val(text, name, default=""):
    m = re.search(r'<Property name="%s" value="([^"]*)"' % re.escape(name), text)
    return m.group(1) if m else default


def parse_defaults(mxml):
    """ObjectID -> {DefaultMaterialId, DefaultColourPaletteId} (только непустые)."""
    out = {}
    for e in iter_entries(mxml, '\t\t<Property name="Objects"'):
        oid = prop_val(e, "ID")
        if not oid:
            continue
        dm = prop_val(e, "DefaultMaterialId")
        dp = prop_val(e, "DefaultColourPaletteId")
        if dm or dp:
            out[oid] = {"DefaultMaterialId": dm, "DefaultColourPaletteId": dp}
    return out


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Слои мультитекстур + отделка по умолчанию (1:1 из игры)")
    ap.add_argument("--index", default=DEF_INDEX, help="папка результата индексатора (NMS_INDEX)")
    ap.add_argument("--pcbanks", default="", help="папка PCBANKS (по умолчанию из parts_links.json)")
    ap.add_argument("--mbin", default=DEF_MBIN, help="путь к MBINCompiler.exe")
    ap.add_argument("--out", default="", help="куда писать (по умолчанию <index>\\finish_layers.json)")
    ap.add_argument("--trees", default=None,
                    help="папка деревьев meshwork для добора entity-цепочки "
                         "(по умолчанию ночной прогон; '' — отключить)")
    args = ap.parse_args()

    links_path = os.path.join(args.index, "parts_links.json")
    manifest_path = os.path.join(args.index, "pak_manifest.json")
    for p in (links_path, manifest_path):
        if not os.path.isfile(p):
            sys.exit("Нет файла (сначала прогнать nms_indexer): " + p)
    raw = os.path.join(args.index, "raw")
    out_path = args.out or os.path.join(args.index, "finish_layers.json")

    print("[1] читаю базу индексатора...")
    with open(links_path, "r", encoding="utf-8") as fh:
        db = json.load(fh)
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pcbanks = args.pcbanks or (db.get("_source") or {}).get("pcbanks") or ""
    if not os.path.isdir(pcbanks):
        sys.exit("Нет папки PCBANKS: %r (задать --pcbanks)" % pcbanks)

    mats = db.get("materials") or {}
    f55 = {k: v for k, v in mats.items()
           if "error" not in v and MULTITEX_FLAG in (v.get("flags") or [])}
    print("    материалов всего: %d, с %s: %d (самопроверка: %d)"
          % (len(mats), MULTITEX_FLAG, len(f55), EXPECT_F55_MATERIALS))

    # --- 2: атласы (лениво): текстура → инфо заголовка DDS из паков
    print("[2] читаю заголовки DDS атласов из паков (по мере надобности)...")
    atlases = {}

    def atlas_info(map_path):
        key = norm(map_path)
        if key in atlases:
            return atlases[key]
        rec = manifest.get(key)
        if rec is None:
            atlases[key] = {"error": "нет в паках"}
        else:
            head = get_pak(pcbanks, rec["pak"]).read_head(rec["index"], 256)
            info = dds_info(head)
            atlases[key] = info if info else {"error": "не DDS"}
        return atlases[key]

    def atlas_layers(map_path):
        return atlas_info(map_path).get("layers", 1) or 1

    # заголовки всех сэмплеров F55-материалов (как раньше)
    for v in f55.values():
        for s in v.get("samplers") or []:
            if s.get("map"):
                atlas_info(s["map"])
    n_multi = sum(1 for a in atlases.values() if a.get("layers", 1) > 1)
    print("    уникальных текстур у F55-материалов: %d, из них массивов (слоёв>1): %d"
          % (len(atlases), n_multi))

    # --- 2б: признак «мультитекстурный материал»: флаг _F55 ИЛИ фактический слоёный атлас
    mat_extra_cache = {}   # материалы вне базы индексатора: path_norm -> parsed|None
    n_mat_extra_parsed = 0

    def material_record(mk):
        """Запись материала: из базы индексатора или допарсенная из MXML."""
        v = mats.get(mk)
        if v is not None and "error" not in v:
            return {"flags_f55": MULTITEX_FLAG in (v.get("flags") or []),
                    "samplers": v.get("samplers") or [],
                    "class": v.get("class", "")}
        if mk in mat_extra_cache:
            return mat_extra_cache[mk]
        mxml = re.sub(r"\.mbin(\.pc)?$", ".MXML",
                      os.path.join(raw, mk.replace("/", os.sep)), flags=re.I)
        if not os.path.isfile(_long(mxml)):
            mb = extract_one(pcbanks, manifest, mk, raw)
            mxml = decode_one(args.mbin, mb) if mb else None
        parsed = parse_material_mxml(mxml) if mxml else None
        if parsed is not None:
            parsed["class"] = ""
            nonlocal_counter["extra_mats"] += 1
        mat_extra_cache[mk] = parsed
        return parsed

    nonlocal_counter = {"extra_mats": 0}
    multitex_verdict = {}   # mk -> "f55" | None (финиш-мультитекстура = флаг _F55)
    array_no_f55_mats = set()  # слоёный атлас БЕЗ _F55 (световой куки и т.п.) — НЕ финиш

    def material_multitex(mk):
        """Финиш-мультитекстура детали = материал с флагом _F55_MULTITEXTURE (система
        отделки игры: gfMultiTextureIndex по слою). Слоёный атлас БЕЗ _F55 (напр.
        light.material — световой куки COOKIE.DDS) отделкой НЕ является — фиксируем в
        array_no_f55_mats информационно, но деталь им мультитекстурной НЕ помечаем."""
        if mk in multitex_verdict:
            return multitex_verdict[mk]
        r = material_record(mk)
        verdict = None
        if r is not None:
            if r["flags_f55"]:
                verdict = "f55"
            else:
                for s in r["samplers"]:
                    if s.get("map") and atlas_layers(s["map"]) > 1:
                        array_no_f55_mats.add(mk)
                        break
        multitex_verdict[mk] = verdict
        return verdict

    # --- 3: слой per-материал (юниформа в .MATERIAL.MXML или рантайм)
    print("[3] проверяю юниформу %s в %d материалах..." % (UNIFORM_NAME, len(f55)))
    out_mats = {}
    n_uniform = n_runtime = n_nomxml = 0
    for k, v in sorted(f55.items()):
        mxml = re.sub(r"\.mbin(\.pc)?$", ".MXML",
                      os.path.join(raw, k.replace("/", os.sep)), flags=re.I)
        if not os.path.isfile(_long(mxml)):
            mb = extract_one(pcbanks, manifest, k, raw)
            mxml = decode_one(args.mbin, mb) if mb else None
        if mxml and os.path.isfile(_long(mxml)):
            layer, src = material_layer(_long(mxml))
        else:
            layer, src = None, "материал не извлёкся/не декодировался"
            n_nomxml += 1
        if layer is not None:
            n_uniform += 1
        elif "рантайм" in src:
            n_runtime += 1
        diffuse = next((s["map"] for s in v.get("samplers") or []
                        if s.get("name") == "gDiffuseMap"), "")
        out_mats[k] = {
            "class": v.get("class", ""),
            "diffuse": diffuse,
            "diffuse_layers": atlases.get(norm(diffuse), {}).get("layers"),
            "layer": layer,
            "layer_source": src,
        }
    print("    слой в юниформе: %d, рантайм (UserData байт 3): %d, без MXML: %d"
          % (n_uniform, n_runtime, n_nomxml))

    # --- 4: отделка/палитра по умолчанию из objectstable (сам, копией парсера)
    print("[4] читаю DefaultMaterialId/DefaultColourPaletteId из objectstable...")
    obj_mbin = os.path.join(raw, TABLE_OBJECTS.replace("/", os.sep))
    if not os.path.isfile(obj_mbin):
        obj_mbin = extract_one(pcbanks, manifest, TABLE_OBJECTS, raw)
        if obj_mbin is None:
            sys.exit("objectstable не найдена в паках")
    obj_mxml = decode_one(args.mbin, obj_mbin)
    if obj_mxml is None:
        sys.exit("objectstable не декодировалась (см. MBINCompiler.log)")
    defaults = parse_defaults(obj_mxml)
    print("    деталей с непустой отделкой/палитрой по умолчанию: %d" % len(defaults))

    # --- 4б: partstable для резолвера СБОРКИ (комнаты фрейтера/корвета за entity-правилами)
    part_mbin = os.path.join(raw, TABLE_PARTS.replace("/", os.sep))
    if not os.path.isfile(part_mbin):
        part_mbin = extract_one(pcbanks, manifest, TABLE_PARTS, raw)
    part_mxml = decode_one(args.mbin, part_mbin) if part_mbin else None
    partstable = parse_partstable(part_mxml) if part_mxml else {}
    print("    partstable: part_id со сценами: %d" % len(partstable))

    # --- 5: свод по деталям — РЕКУРСИВНЫЙ сбор материалов сцен (добор 07.07.2026)
    trees_dir = args.trees if args.trees is not None else DEF_TREES
    have_trees = bool(trees_dir) and os.path.isdir(trees_dir)
    print("[5] обход сцен деталей (стили+сцена+placement+strip _placement+REFERENCE)%s..."
          % (" + деревья meshwork" if have_trees else " (деревьев meshwork нет — пропуск)"))
    walker = SceneWalker(db.get("scenes") or {}, raw, pcbanks, manifest, args.mbin)
    parts_out = {}
    n_mt_parts_old = 0   # старый метод (scene_materials ∩ F55) — регресс-контроль
    n_mt_parts = 0
    gained_walk = []     # получили мультитекстуру только обходом сцен (strip/REFERENCE)
    gained_tree = []     # получили только из дерева meshwork (entity-цепочка)
    gained_asm = []      # получили только резолвером СБОРКИ по entity-правилам
    for oid, rec in sorted((db.get("parts") or {}).items()):
        base = set(norm(m) for m in rec.get("scene_materials") or [])
        old_mt = sorted(m for m in base if m in f55)
        if old_mt:
            n_mt_parts_old += 1
        seeds = list((rec.get("styles") or {}).values())
        if rec.get("scene"):
            seeds.append(rec["scene"])
        pl = (rec.get("object") or {}).get("PlacementScene")
        if pl:
            seeds.append(pl)
        # strip _placement: базовая сцена с реальными мешами (там где она есть)
        for s in list(seeds):
            if s and norm(s) != norm(strip_placement(s)):
                seeds.append(strip_placement(s))
        walked = walker.collect(seeds)
        all_mats = base | walked
        mt, mt_kinds = [], {}
        for m in sorted(all_mats):
            kind = material_multitex(m)
            if kind:
                mt.append(m)
                mt_kinds[m] = kind
        got_by_walk = bool(mt)
        # добор из дерева meshwork (entity-резолвленные материалы корвета/фрейтера/диагоналей)
        tree_src = []
        if have_trees:
            for mk, info in tree_multitex_materials(trees_dir, oid).items():
                if mk not in mt_kinds:
                    mt.append(mk)
                    mt_kinds[mk] = "f55" if info["flags_f55"] else "array"
                    tree_src.append(mk)
                    if mk not in out_mats:
                        out_mats[mk] = {
                            "class": info["class"],
                            "diffuse": info["diffuse"],
                            "diffuse_layers": info["diffuse_layers"],
                            "layer": None,
                            "layer_source": "рантайм (UserData байт 3); материал из дерева meshwork (entity-цепочка)",
                            "from_tree": True,
                        }
            mt = sorted(set(mt))
        # FALLBACK: резолвер СБОРКИ по entity-правилам — для деталей-комнат (фрейтер/
        # корвет-хаб), чей меш собран из под-частей за GcBasePlacementRule, а не за
        # REFERENCE (обход сцен и деревья meshwork их не берут — meshwork даёт «ПУСТО»).
        # Только когда обход+дерево ничего не дали (не трогает уже решённые детали).
        asm_src = []
        if not mt and pl and partstable:
            asm_scenes = set()
            for pid in placement_rule_partids(pl, raw, pcbanks, manifest, args.mbin):
                asm_scenes |= partstable.get(pid.lower(), set())
            if asm_scenes:
                for m in sorted(walker.collect(list(asm_scenes))):
                    kind = material_multitex(m)
                    if kind and m not in mt_kinds:
                        mt.append(m)
                        mt_kinds[m] = kind
                        asm_src.append(m)
                mt = sorted(set(mt))
        d = defaults.get(oid)
        if not mt and not d:
            continue
        entry = {}
        if d:
            entry.update(d)
        if mt:
            entry["multitexture_materials"] = mt
            if tree_src:
                entry["from_meshwork_tree"] = sorted(tree_src)
            if asm_src:
                entry["from_entity_assembly"] = sorted(asm_src)
            n_mt_parts += 1
            if not old_mt:
                if got_by_walk:
                    gained_walk.append(oid)
                elif tree_src:
                    gained_tree.append(oid)
                else:
                    gained_asm.append(oid)
        parts_out[oid] = entry
    # детали из objectstable, которых нет в parts_links (не должно быть, но не терять)
    for oid, d in defaults.items():
        if oid not in parts_out:
            parts_out[oid] = dict(d)
    print("    старый метод: %d деталей (самопроверка: %d); с добором: %d (+%d обходом сцен, +%d из деревьев, +%d сборкой entity)"
          % (n_mt_parts_old, EXPECT_F55_PARTS_OLD, n_mt_parts,
             len(gained_walk), len(gained_tree), len(gained_asm)))
    print("    сцен допарсено вне графа: %d, не найдено: %d, материалов допарсено: %d"
          % (walker.n_extra_parsed, walker.n_missing, nonlocal_counter["extra_mats"]))

    # материалы-«массивы без _F55» (световой куки и т.п.): деталями НЕ считаются
    # финиш-мультитекстурой (система отделки требует флага _F55), фиксируем списком
    array_no_f55 = sorted(array_no_f55_mats)

    n_multi_total = sum(1 for a in atlases.values() if a.get("layers", 1) > 1)

    # --- запись
    result = {
        "_source": {"index": os.path.abspath(args.index), "pcbanks": os.path.abspath(pcbanks)},
        "_summary": {
            "materials_f55": len(f55),
            "materials_layer_uniform": n_uniform,
            "materials_layer_runtime": n_runtime,
            "materials_array_no_f55": array_no_f55,
            "parts_multitexture_old_method": n_mt_parts_old,
            "parts_multitexture": n_mt_parts,
            "parts_gained_by_scene_walk": gained_walk,
            "parts_gained_by_meshwork_tree": gained_tree,
            "parts_gained_by_entity_assembly": gained_asm,
            "parts_with_defaults": len(defaults),
            "atlases_total": len(atlases),
            "atlases_arrays": n_multi_total,
            "scenes_parsed_extra": walker.n_extra_parsed,
            "scenes_missing": walker.n_missing,
            "trees_dir": trees_dir if have_trees else None,
        },
        "atlases": atlases,
        "materials": out_mats,
        "parts": parts_out,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    # --- отчёт
    print()
    print("=== АТЛАСЫ-МАССИВЫ (слоёв > 1) ===")
    for k in sorted(atlases, key=lambda x: (-atlases[x].get("layers", 0), x)):
        a = atlases[k]
        if a.get("layers", 1) > 1:
            print("  %2d слоя(ёв)  %s  (%sx%s, %s)" % (a["layers"], k,
                                                       a.get("width"), a.get("height"),
                                                       a.get("fourcc")))
    warn = []
    if len(f55) != EXPECT_F55_MATERIALS:
        warn.append("F55-материалов %d (ожидалось %d)" % (len(f55), EXPECT_F55_MATERIALS))
    if n_mt_parts_old != EXPECT_F55_PARTS_OLD:
        warn.append("старый метод дал %d деталей (ожидалось %d) — регресс сбора scene_materials?"
                    % (n_mt_parts_old, EXPECT_F55_PARTS_OLD))
    if array_no_f55:
        warn.append("материалов со слоёным атласом БЕЗ %s: %d (слой не определён — см. materials)"
                    % (MULTITEX_FLAG, len(array_no_f55)))
    if n_nomxml:
        warn.append("не извлеклись/не декодировались: %d материалов" % n_nomxml)
    if walker.n_missing:
        warn.append("сцены не найдены ни в графе, ни в паках: %d" % walker.n_missing)
    for w in warn:
        print("  !! " + w)
    if not warn:
        print("  самопроверки сошлись.")
    print()
    print("Результат: %s" % out_path)
    for p in _open_paks.values():
        p.close()


if __name__ == "__main__":
    main()
