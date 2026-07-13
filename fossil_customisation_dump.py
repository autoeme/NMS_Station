# -*- coding: utf-8 -*-
"""
fossil_customisation_dump.py — дамп АВТОРИТЕТНОЙ привязки вариантов фоссилов (и вообще
модульной кастомизации) из двух таблиц игры:

  metadata/gamestate/playerdata/modularcustomisationdatatable.mbin
     -> конфиги Exhibit* (сцена BaseResource, слоты, какие ItemID куда слотятся,
        какой ActivatedDescriptorGroupID активирует каждый ItemID)
     -> SharedSlottableItemLists (FOS_BODY / FOS_HEAD / FOS_LIMBS_* / FOS_TAIL ...)
     -> ProductLookupList (какой стенд рендерит продукт в UI)
  metadata/gamestate/playerdata/charactercustomisationdescriptorgroupsdata.mbin
     -> GroupID -> ЯВНЫЙ список дескриптор-узлов (_Body_A, _NECK_B, _BodyAacc_Null ...)

Итог: NMS_INDEX/fossil_variants.json
  {
    "groups":   { GroupID: [descriptor, ...] },
    "lists":    { ListID: [ {item, group}, ... ] },
    "exhibits": { ExhibitType: { "scene": путь SCENE.MBIN,
                                  "slots": { SlotID: {"locator":..., "items":[{item,group},...],
                                                       "lists":[ListID,...] } },
                                  "lookup": [ItemID, ...] } },
    "items":    { ItemID: { "groups":[GroupID,...], "descriptors":[...],
                             "exhibits":[ExhibitType,...] } }
  }

Найдено 13.07.2026 при группе Fossils: буквы каталога (FOS_BI_BODY_BN и т.п.)
НЕ являются прямой позиционной мапой на группы дескриптора сцены — игра держит
явный белый список узлов на каждый вариант (пример: BI_BODY_BN = _Pelvis_A +
_Body_A + _BodyAacc_Null + _NECK_B + _NECKBacc_Null: первая буква = ШЕЯ).
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRACT = r"C:\Users\User\Desktop\MBINCompiler\MESHWORK_EXTRACT_2026"
DATATABLE = os.path.join(EXTRACT, "metadata", "gamestate", "playerdata",
                         "modularcustomisationdatatable.MXML")
GROUPSDATA = os.path.join(EXTRACT, "metadata", "gamestate", "playerdata",
                          "charactercustomisationdescriptorgroupsdata.MXML")
OUT = os.path.join(r"C:\Users\User\Desktop\NMS_INDEX", "fossil_variants.json")


def props(node):
    return [c for c in node if c.tag == "Property"]


def prop(node, name):
    for c in props(node):
        if c.get("name") == name:
            return c
    return None


def val(node, name):
    p = prop(node, name)
    return p.get("value") if p is not None else None


def parse_groups(path):
    """GroupID -> [descriptor names]"""
    tree = ET.parse(path)
    out = {}
    for g in tree.getroot().iter("Property"):
        if g.get("value") != "GcCustomisationDescriptorGroup":
            continue
        gid = val(g, "GroupID")
        if not gid:
            continue
        descs = []
        d = prop(g, "Descriptors")
        if d is not None:
            for it in props(d):
                v = it.get("value")
                if v:
                    descs.append(v)
        out[gid] = descs
    return out


def item_data(node):
    """GcModularCustomisationSlotItemData -> (item, [groups])"""
    item = val(node, "ItemID") or ""
    groups = []
    dgd = prop(node, "DescriptorGroupData")
    if dgd is not None:
        for g in props(dgd):
            agid = val(g, "ActivatedDescriptorGroupID")
            if agid:
                groups.append(agid)
    return item, groups


def parse_datatable(path):
    tree = ET.parse(path)
    root = tree.getroot()
    exhibits = {}
    lists = {}
    lookups = {}

    cfgs = prop(root, "ModularCustomisationConfigs")
    if cfgs is not None:
        for cfg in props(cfgs):
            if cfg.get("value") != "GcModularCustomisationConfig":
                continue
            name = cfg.get("name")
            base = prop(cfg, "BaseResource")
            scene = val(base, "Filename") if base is not None else ""
            slots = {}
            slots_node = prop(cfg, "Slots")
            if slots_node is not None:
                for s in props(slots_node):
                    sid = val(s, "SlotID") or "?"
                    locator = val(s, "UILocatorName") or ""
                    items = []
                    sl = prop(s, "SlottableItems")
                    if sl is not None:
                        for it in props(sl):
                            item, groups = item_data(it)
                            if item:
                                items.append({"item": item, "groups": groups})
                    extra = []
                    add = prop(s, "AdditionalSlottableItemLists")
                    if add is not None:
                        for a in props(add):
                            if a.get("value"):
                                extra.append(a.get("value"))
                    slots[sid] = {"locator": locator, "items": items, "lists": extra}
            exhibits[name] = {"scene": scene, "slots": slots, "lookup": []}

    shared = prop(root, "SharedSlottableItemLists")
    if shared is not None:
        for lst in props(shared):
            if lst.get("value") != "GcModularCustomisationSlottableItemList":
                continue
            lid = val(lst, "ListID") or "?"
            items = []
            sl = prop(lst, "SlottableItems")
            if sl is not None:
                for it in props(sl):
                    item, groups = item_data(it)
                    if item:
                        items.append({"item": item, "groups": groups})
            lists[lid] = items

    pll = prop(root, "ProductLookupLists")
    if pll is None:
        # имя узла в 6.45: ProductLookup / ProductLookupLists — ищем по значению типа
        for c in props(root):
            if c.get("name") not in ("ModularCustomisationConfigs", "SharedSlottableItemLists"):
                pll = c
                break
    if pll is not None:
        for ex in props(pll):
            if ex.get("value") != "GcModularCustomisationProductLookupList":
                continue
            name = ex.get("name")
            ids = []
            inner = prop(ex, "ProductLookupList")
            if inner is not None:
                for it in props(inner):
                    if it.get("value"):
                        ids.append(it.get("value"))
            lookups[name] = ids

    for name, ids in lookups.items():
        if name in exhibits:
            exhibits[name]["lookup"] = ids
    return exhibits, lists


def main():
    for p in (DATATABLE, GROUPSDATA):
        if not os.path.exists(p):
            print("НЕТ ФАЙЛА:", p)
            print("Извлечь: pak2mxml --only <имя таблицы> --out MESHWORK_EXTRACT_2026")
            sys.exit(1)

    groups = parse_groups(GROUPSDATA)
    exhibits, lists = parse_datatable(DATATABLE)

    # свод по item: какие группы активирует и в каких exhibit встречается
    items = {}

    def feed(item, item_groups, exhibit=None):
        rec = items.setdefault(item, {"groups": [], "descriptors": [], "exhibits": []})
        for g in item_groups:
            if g not in rec["groups"]:
                rec["groups"].append(g)
            for d in groups.get(g, []):
                if d not in rec["descriptors"]:
                    rec["descriptors"].append(d)
        if exhibit and exhibit not in rec["exhibits"]:
            rec["exhibits"].append(exhibit)

    for ex_name, ex in exhibits.items():
        for sid, s in ex["slots"].items():
            for it in s["items"]:
                feed(it["item"], it["groups"], ex_name)
            for lid in s["lists"]:
                for it in lists.get(lid, []):
                    feed(it["item"], it["groups"], ex_name)
        for pid in ex["lookup"]:
            rec = items.setdefault(pid, {"groups": [], "descriptors": [], "exhibits": []})
            if ex_name not in rec["exhibits"]:
                rec["exhibits"].append(ex_name)

    for lid, lst in lists.items():
        for it in lst:
            feed(it["item"], it["groups"])

    out = {"groups": groups, "lists": lists, "exhibits": exhibits, "items": items}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    fos = [k for k in items if k.startswith("FOS_")]
    print(f"групп дескрипторов: {len(groups)}; exhibit-конфигов: {len(exhibits)}; "
          f"shared-списков: {len(lists)}; items всего: {len(items)}; из них FOS_*: {len(fos)}")
    print("->", OUT)


if __name__ == "__main__":
    main()
