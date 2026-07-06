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

Использование:
    python nms_finish.py [--index ПАПКА] [--pcbanks ПАПКА] [--mbin EXE] [--out ФАЙЛ]

По умолчанию: --index Desktop\\NMS_INDEX, --pcbanks из parts_links.json (_source),
--out <index>\\finish_layers.json.

Самопроверка (посчитано по базе 06.07.2026): материалов с _F55_MULTITEXTURE = 666,
затронутых деталей = 967; известные слои: тримы = 2, basebuildingexterior = 4, biggs = 4.
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
EXPECT_F55_PARTS = 967


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

    # --- 2: атласы — уникальные текстуры всех сэмплеров F55-материалов + число слоёв
    print("[2] читаю заголовки DDS атласов из паков...")
    want_dds = {}
    for v in f55.values():
        for s in v.get("samplers") or []:
            if s.get("map"):
                want_dds.setdefault(norm(s["map"]), s["map"])
    # полнота: мультитекстурные пути у материалов БЕЗ флага F55 (не должно быть — проверяем)
    no_flag_users = sorted(
        k for k, v in mats.items()
        if "error" not in v and MULTITEX_FLAG not in (v.get("flags") or [])
        and any("multitextures/" in norm(s.get("map", "")) for s in v.get("samplers") or []))
    atlases = {}
    for key in sorted(want_dds):
        rec = manifest.get(key)
        if rec is None:
            atlases[key] = {"error": "нет в паках"}
            continue
        head = get_pak(pcbanks, rec["pak"]).read_head(rec["index"], 256)
        info = dds_info(head)
        atlases[key] = info if info else {"error": "не DDS"}
    n_multi = sum(1 for a in atlases.values() if a.get("layers", 1) > 1)
    print("    уникальных текстур у F55-материалов: %d, из них массивов (слоёв>1): %d"
          % (len(atlases), n_multi))

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

    # --- 5: свод по деталям (какие F55-материалы использует деталь)
    parts_out = {}
    n_mt_parts = 0
    for oid, rec in (db.get("parts") or {}).items():
        mt = sorted(norm(m) for m in rec.get("scene_materials") or [] if norm(m) in f55)
        d = defaults.get(oid)
        if not mt and not d:
            continue
        entry = {}
        if d:
            entry.update(d)
        if mt:
            entry["multitexture_materials"] = mt
            n_mt_parts += 1
        parts_out[oid] = entry
    # детали из objectstable, которых нет в parts_links (не должно быть, но не терять)
    for oid, d in defaults.items():
        if oid not in parts_out:
            parts_out[oid] = dict(d)
    print("    деталей с мультитекстурным материалом: %d (самопроверка: %d)"
          % (n_mt_parts, EXPECT_F55_PARTS))

    # --- запись
    result = {
        "_source": {"index": os.path.abspath(args.index), "pcbanks": os.path.abspath(pcbanks)},
        "_summary": {
            "materials_f55": len(f55),
            "materials_layer_uniform": n_uniform,
            "materials_layer_runtime": n_runtime,
            "parts_multitexture": n_mt_parts,
            "parts_with_defaults": len(defaults),
            "atlases_total": len(atlases),
            "atlases_arrays": n_multi,
            "non_f55_multitexture_users": no_flag_users,
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
    if n_mt_parts != EXPECT_F55_PARTS:
        warn.append("мультитекстурных деталей %d (ожидалось %d)" % (n_mt_parts, EXPECT_F55_PARTS))
    if no_flag_users:
        warn.append("материалы БЕЗ %s ссылаются на MULTITEXTURES: %d шт (см. _summary)"
                    % (MULTITEX_FLAG, len(no_flag_users)))
    if n_nomxml:
        warn.append("не извлеклись/не декодировались: %d материалов" % n_nomxml)
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
