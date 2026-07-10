import unreal, json, os

# Дамп ФАКТИЧЕСКИХ материал-слотов StaticMesh по списку имён (порядок важен!).
# Список = JSON-массив имён ассетов GameMeshes; путь берётся из env NMS_SLOTS_LIST
# (по умолчанию — рядом: dump_slots_list_input.json). Выход: env NMS_SLOTS_OUT
# (по умолчанию ueslots_list.json рядом). Вариант dump_slots_all.py для одной группы.

HERE = os.path.dirname(os.path.abspath(__file__))
INP = os.environ.get("NMS_SLOTS_LIST", os.path.join(HERE, "dump_slots_list_input.json"))
OUT = os.environ.get("NMS_SLOTS_OUT", os.path.join(HERE, "ueslots_list.json"))

names = json.load(open(INP))
res = {}
for name in names:
    path = "/Game/NMSBaseBuilder/GameMeshes/%s.%s" % (name, name)
    mesh = unreal.EditorAssetLibrary.load_asset(path)
    if mesh is None:
        unreal.log_warning("SLOTS: net asseta %s" % name)
        res[name] = None
        continue
    res[name] = [str(sm.material_slot_name) for sm in mesh.static_materials]

json.dump(res, open(OUT, "w"), indent=0)
unreal.log("SLOTS DONE %d" % len(res))
unreal.SystemLibrary.quit_editor()
