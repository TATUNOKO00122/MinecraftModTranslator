import os
import json
import zipfile

CATEGORY_KEY_MAP = {
    "spells": [
        "{ns}.spell.{id}",
        "spell.desc.{id}",
    ],
    "perk": [
        "{ns}.talent.{id}",
    ],
    "unique_gears": [
        "{ns}.unique_gear.{id}.name",
    ],
    "support_gem": [
        "{ns}.support_gem.{id}",
    ],
    "exile_effect": [
        "{ns}.effect.{id}",
    ],
    "stat": [
        "{ns}.stat.{id}",
        "{ns}.stat_desc.{id}",
    ],
    "spell_school": [
        "{ns}.asc_class.{id}",
    ],
    "runeword": [
        "{ns}.runeword.{id}",
    ],
    "aura": [
        "{ns}.aura.{id}",
    ],
    "gear_slot": [
        "{ns}.gearslot.{id}",
    ],
    "base_gear_types": [
        "{ns}.gear_type.{id}",
    ],
    "gear_rarity": [
        "{ns}.rarity.{id}",
    ],
    "mob_rarity": [
        "{ns}.mob_rarity.{id}",
    ],
    "mob_affix": [
        "{ns}.mob_affix.{id}",
    ],
    "omen": [
        "{ns}.omen.{id}",
    ],
    "runes": [
        "{ns}.runes.{id}",
    ],
    "profession": [
        "{ns}.profession.{id}",
    ],
    "gems": [
        "{ns}.gem_type.{id}",
    ],
    "map_affix": [
        "{ns}.map_affix.{id}",
    ],
    "dimension": [
        "{ns}.dimension.{id}",
    ],
    "prophecy_modifier": [
        "{ns}.prophecy_modifier.{id}",
    ],
    "base_stats": [
        "{ns}.stat.{id}",
    ],
    "stat_buff": [
        "{ns}.stat.{id}",
    ],
    "stat_compat": [
        "{ns}.stat.{id}",
    ],
    "stat_condition": [
        "{ns}.stat.{id}",
    ],
    "stat_effect": [
        "{ns}.stat.{id}",
    ],
    "value_calc": [
        "{ns}.stat.{id}",
    ],
    "weapon_type": [
        "{ns}.gear_type.{id}",
    ],
    "custom_item": [
        "{ns}.custom_item.{id}",
    ],
    "auto_item": [
        "{ns}.auto_item.{id}",
    ],
    "talent_tree": [],
    "game_balance": [],
    "profession_recipe": [],
    "loot_tables": [],
    "recipes": [],
    "map_mob_list - obsolete": [],
}

INLINE_FIELDS = {
    "loc_name": "",
    "loc_desc": ".desc",
    "effect_tip": ".tip",
    "flavor_text": ".flavor",
}

SKIP_CATEGORIES = {
    "profession_recipe", "loot_tables", "recipes",
    "talent_tree", "game_balance",
    "map_mob_list - obsolete",
}


def detect_datapack(path):
    if not os.path.isdir(path):
        return False
    has_mcmeta = os.path.exists(os.path.join(path, "pack.mcmeta"))
    has_data = os.path.isdir(os.path.join(path, "data"))
    return has_mcmeta and has_data


def _find_mod_jar(mods_dir, namespace):
    if not os.path.isdir(mods_dir):
        return None

    hints = {
        "mmorpg": ["mine_and_slash", "mineandslash", "mmorpg"],
        "library_of_exile": ["library_of_exile"],
    }

    search_terms = hints.get(namespace, [namespace])

    for f in os.listdir(mods_dir):
        if not f.endswith('.jar') and not f.endswith('.zip'):
            continue
        lower = f.lower()
        for term in search_terms:
            if term in lower:
                return os.path.join(mods_dir, f)
    return None


def _read_lang_from_jar(jar_path, lang_file="en_us.json"):
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            for entry in zf.namelist():
                if entry.endswith(lang_file) and '/lang/' in entry:
                    with zf.open(entry) as f:
                        content = f.read().decode('utf-8')
                        return json.loads(content)
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError):
        pass
    return {}


def _format_id_as_name(item_id):
    return item_id.replace('_', ' ').title()


def _extract_text_component(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and "text" in value:
        return value["text"].strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            extracted = _extract_text_component(item)
            if extracted:
                parts.append(extracted)
        return "".join(parts)
    return ""


def _strip_ns_prefix(category_dir, namespace):
    prefix = namespace + "_"
    if category_dir.startswith(prefix):
        return category_dir[len(prefix):]
    return category_dir


def load_datapack(datapack_path, mods_dir=None):
    data_dir = os.path.join(datapack_path, "data")
    if not os.path.isdir(data_dir):
        return None, None, {}, {}

    namespaces = [
        d for d in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, d)) and not d.startswith('_')
        and d != "minecraft"
    ]

    if not namespaces:
        return None, None, {}, {}

    namespace_counts = {}
    for ns in namespaces:
        ns_dir = os.path.join(data_dir, ns)
        count = sum(
            1 for _, _, files in os.walk(ns_dir)
            for f in files if f.endswith('.json')
        )
        namespace_counts[ns] = count

    primary_ns = max(namespace_counts, key=namespace_counts.get)

    mod_lang = {}
    for ns in namespaces:
        jar = _find_mod_jar(mods_dir, ns) if mods_dir else None
        if jar:
            lang_data = _read_lang_from_jar(jar)
            if lang_data:
                mod_lang.update(lang_data)

    category_items = {}
    inline_overrides = {}

    for ns in namespaces:
        ns_dir = os.path.join(data_dir, ns)
        _scan_namespace(ns_dir, ns, category_items, inline_overrides)

    lang_dict = {}
    item_sources = {}
    inline_used = set()

    for (ns, category_dir, item_id), sources in category_items.items():
        cat_key = _strip_ns_prefix(category_dir, ns)

        if cat_key in SKIP_CATEGORIES:
            continue

        key_templates = CATEGORY_KEY_MAP.get(cat_key)

        if key_templates is None:
            key_templates = [f"{ns}.{cat_key}.{{id}}"]

        for template in key_templates:
            key = template.format(ns=ns, id=item_id)
            source = ";".join(sources)

            if key in mod_lang:
                lang_dict[key] = mod_lang[key]
                item_sources[key] = source
            elif key in inline_overrides:
                lang_dict[key] = inline_overrides[key]
                item_sources[key] = source
                inline_used.add(key)
            else:
                fallback = _format_id_as_name(item_id)
                lang_dict[key] = fallback
                item_sources[key] = source

        for field, suffix in INLINE_FIELDS.items():
            override_key = f"{ns}.{cat_key}.{item_id}{suffix}"
            if override_key in inline_overrides and override_key not in lang_dict:
                lang_dict[override_key] = inline_overrides[override_key]
                item_sources[override_key] = ";".join(sources)
                inline_used.add(override_key)

    for key in inline_overrides:
        if key not in inline_used and key not in lang_dict:
            lang_dict[key] = inline_overrides[key]
            item_sources[key] = "datapack_inline"

    _scan_display_texts(data_dir, namespaces, lang_dict, item_sources, mod_lang)

    pack_name = os.path.basename(datapack_path)
    return pack_name, primary_ns, lang_dict, item_sources


def _scan_namespace(ns_dir, namespace, category_items, inline_overrides):
    for root, dirs, files in os.walk(ns_dir):
        dirs[:] = [d for d in dirs if d not in ("lang",)]

        for filename in files:
            if not filename.endswith('.json'):
                continue

            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, ns_dir)

            parts = rel_path.replace('\\', '/').split('/')
            if len(parts) < 2:
                continue

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not isinstance(data, dict):
                continue

            item_id = data.get(
                "identifier",
                data.get("guid", data.get("id", os.path.splitext(filename)[0]))
            )
            category_dir = parts[0]

            key = (namespace, category_dir, item_id)
            if key not in category_items:
                category_items[key] = []
            category_items[key].append(rel_path.replace('\\', '/'))

            cat_key = _strip_ns_prefix(category_dir, namespace)

            for field, suffix in INLINE_FIELDS.items():
                value = data.get(field)
                if not value or not isinstance(value, str) or not value.strip():
                    continue
                override_key = f"{namespace}.{cat_key}.{item_id}{suffix}"
                inline_overrides[override_key] = value.strip()


def _scan_display_texts(data_dir, namespaces, lang_dict, item_sources, mod_lang):
    for ns in namespaces:
        ns_dir = os.path.join(data_dir, ns)
        for root, dirs, files in os.walk(ns_dir):
            dirs[:] = [d for d in dirs if d not in ("lang",)]

            for filename in files:
                if not filename.endswith('.json'):
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, ns_dir)

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue

                if not isinstance(data, dict):
                    continue

                display = data.get("display")
                if not isinstance(display, dict):
                    continue

                item_id = data.get(
                    "identifier",
                    data.get("id", os.path.splitext(filename)[0]))
                parts = rel_path.replace('\\', '/').split('/')
                category_dir = parts[0] if len(parts) >= 2 else ""

                cat_key = _strip_ns_prefix(category_dir, ns)

                title = display.get("title")
                if title:
                    text = _extract_text_component(title)
                    if text:
                        key = f"{ns}.{cat_key}.{item_id}"
                        if key not in lang_dict:
                            lang_dict[key] = text
                            item_sources[key] = rel_path.replace('\\', '/')

                desc = display.get("description")
                if desc:
                    text = _extract_text_component(desc)
                    if text:
                        key = f"{ns}.{cat_key}.{item_id}.description"
                        if key not in lang_dict:
                            lang_dict[key] = text
                            item_sources[key] = rel_path.replace('\\', '/')


def export_datapack_translations(output_dir, namespace, translations, pack_format=15, target_lang="ja_jp"):
    from logic.file_handler import FileHandler

    fh = FileHandler()
    lang_path = f"assets/{namespace}/lang/{target_lang}.json"

    full_path = os.path.join(output_dir, lang_path)
    existing = {}
    if os.path.exists(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update(translations)

    os.makedirs(output_dir, exist_ok=True)
    mcmeta_path = os.path.join(output_dir, "pack.mcmeta")
    if not os.path.exists(mcmeta_path):
        pack_meta = {
            "pack": {
                "pack_format": pack_format,
                "description": f"Translations for datapack: {namespace}"
            }
        }
        with open(mcmeta_path, 'w', encoding='utf-8') as f:
            json.dump(pack_meta, f, indent=2)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    normalized = fh._normalize_translations(existing)
    with open(full_path, 'w', encoding='utf-8') as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
