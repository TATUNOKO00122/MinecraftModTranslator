"""
FTB Quest Handler - Parse SNBT files and extract translatable text

Translation key format follows FTB Quests official convention:
    {objectType}.{hexID}.{field}[{index}]

Examples:
    chapter.44789E48CD607F46.title
    quest.30403B3DE1E47F33.title
    quest.30403B3DE1E47F33.subtitle
    quest.30403B3DE1E47F33.description0
    task.77FDE21B75D712EC.title
    reward.4E506FC73E0B1EAC.title

objectType is determined by context:
    - Chapter-level fields (title, subtitle) -> "chapter"
    - Quest compound -> "quest"
    - Task compound -> "task"
    - Reward compound -> "reward"
"""
import os
import re
import sys
import traceback

try:
    import ftb_snbt_lib as slib
    from ftb_snbt_lib import tag
    FTB_SNBT_AVAILABLE = True
except ImportError as e:
    FTB_SNBT_AVAILABLE = False
    slib = None
    tag = None
    print(f"WARNING: ftb_snbt_lib not available: {e}")
    traceback.print_exc()


TRANSLATABLE_FIELDS = ("title", "subtitle", "description")


def detect_ftbquests(minecraft_path):
    if not FTB_SNBT_AVAILABLE:
        print("FTB Quest detection skipped: ftb_snbt_lib not available")
        return None

    possible_paths = [
        os.path.join(minecraft_path, "config", "ftbquests", "quests"),
        os.path.join(minecraft_path, "kubejs", "data", "ftbquests", "quests"),
    ]

    for path in possible_paths:
        if os.path.isdir(path):
            return path
    return None


def escape_text(text):
    for match, seq in ((r'%', r'%%'), (r'"', r'\"')):
        text = text.replace(match, seq)
    return text


def filter_text(text):
    if not text:
        return False
    if text.startswith("{") and text.endswith("}"):
        return False
    if text.startswith("[") and text.endswith("]"):
        return False
    return True


def parse_snbt_file(filepath):
    if not FTB_SNBT_AVAILABLE:
        print("Cannot parse SNBT: ftb_snbt_lib not available")
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return slib.loads(content)
    except Exception as e:
        print(f"Failed to parse {filepath}: {e}")
        return None


def _get_id(data):
    if not isinstance(data, tag.Compound):
        return None
    try:
        id_val = data.get("id")
        if id_val is not None:
            return str(id_val).upper()
    except:
        pass
    return None


def _is_converted_text(text):
    return text.startswith("{") and text.endswith("}")


def _extract_translatable_string(field_name, value, key_prefix, lang_dict, converted_keys=None):
    if isinstance(value, tag.String):
        text = str(value)
        if filter_text(text):
            lang_dict[f"{key_prefix}.{field_name}"] = escape_text(text)
        elif converted_keys is not None and _is_converted_text(text):
            converted_keys.add(f"{key_prefix}.{field_name}")
    elif isinstance(value, tag.List):
        try:
            for idx, item in enumerate(value):
                text = str(item)
                if filter_text(text):
                    lang_dict[f"{key_prefix}.{field_name}{idx}"] = escape_text(text)
                elif converted_keys is not None and _is_converted_text(text):
                    converted_keys.add(f"{key_prefix}.{field_name}{idx}")
        except:
            pass


def extract_chapter_texts(chapter_data, lang_dict, converted_keys=None):
    if not isinstance(chapter_data, tag.Compound):
        return

    chapter_id = _get_id(chapter_data)
    if chapter_id:
        for field in ("title", "subtitle"):
            value = chapter_data.get(field)
            if value is not None:
                _extract_translatable_string(field, value, f"chapter.{chapter_id}", lang_dict, converted_keys)

    quests_list = chapter_data.get("quests")
    if isinstance(quests_list, tag.List):
        for quest_item in quests_list:
            if isinstance(quest_item, tag.Compound):
                _extract_quest_texts(quest_item, lang_dict, converted_keys)


def _extract_quest_texts(quest_data, lang_dict, converted_keys=None):
    quest_id = _get_id(quest_data)
    if not quest_id:
        return

    for field in ("title", "subtitle", "description"):
        value = quest_data.get(field)
        if value is not None:
            _extract_translatable_string(field, value, f"quest.{quest_id}", lang_dict, converted_keys)

    for list_field in ("tasks", "rewards"):
        items = quest_data.get(list_field)
        if isinstance(items, tag.List):
            obj_type = "task" if list_field == "tasks" else "reward"
            for item in items:
                if isinstance(item, tag.Compound):
                    item_id = _get_id(item)
                    if item_id:
                        for field in ("title",):
                            value = item.get(field)
                            if value is not None:
                                _extract_translatable_string(field, value, f"{obj_type}.{item_id}", lang_dict, converted_keys)


def _extract_from_snbt(quest_data, lang_dict, converted_keys=None):
    if "quests" in quest_data and isinstance(quest_data.get("quests"), tag.List):
        extract_chapter_texts(quest_data, lang_dict, converted_keys)
    elif _get_id(quest_data):
        obj_id = _get_id(quest_data)
        for field in ("title", "subtitle"):
            value = quest_data.get(field)
            if value is not None:
                _extract_translatable_string(field, value, f"chapter.{obj_id}", lang_dict, converted_keys)


def _convert_quest_data(quest_data, translations):
    if not isinstance(quest_data, tag.Compound):
        return 0

    count = 0
    quest_id = _get_id(quest_data)

    if quest_id:
        for field in ("title", "subtitle", "description"):
            value = quest_data.get(field)
            if value is None:
                continue

            if isinstance(value, tag.String):
                text = str(value)
                key = f"quest.{quest_id}.{field}"
                if filter_text(text) and key in translations and translations[key]:
                    quest_data[field] = tag.String(f"{{{key}}}")
                    count += 1

            elif isinstance(value, tag.List):
                try:
                    for idx in range(len(value)):
                        text = str(value[idx])
                        key = f"quest.{quest_id}.{field}{idx}"
                        if filter_text(text) and key in translations and translations[key]:
                            value[idx] = tag.String(f"{{{key}}}")
                            count += 1
                except:
                    pass

    for list_field in ("tasks", "rewards"):
        items = quest_data.get(list_field)
        if isinstance(items, tag.List):
            obj_type = "task" if list_field == "tasks" else "reward"
            for item in items:
                if isinstance(item, tag.Compound):
                    item_id = _get_id(item)
                    if item_id:
                        value = item.get("title")
                        if value is not None and isinstance(value, tag.String):
                            text = str(value)
                            key = f"{obj_type}.{item_id}.title"
                            if filter_text(text) and key in translations and translations[key]:
                                item["title"] = tag.String(f"{{{key}}}")
                                count += 1

    return count


def _convert_chapter_data(chapter_data, translations):
    if not isinstance(chapter_data, tag.Compound):
        return 0

    count = 0
    chapter_id = _get_id(chapter_data)

    if chapter_id:
        for field in ("title", "subtitle"):
            value = chapter_data.get(field)
            if value is None:
                continue
            if isinstance(value, tag.String):
                text = str(value)
                key = f"chapter.{chapter_id}.{field}"
                if filter_text(text) and key in translations and translations[key]:
                    chapter_data[field] = tag.String(f"{{{key}}}")
                    count += 1

    quests_list = chapter_data.get("quests")
    if isinstance(quests_list, tag.List):
        for quest_item in quests_list:
            if isinstance(quest_item, tag.Compound):
                count += _convert_quest_data(quest_item, translations)

    return count


def find_backup_file(snbt_path):
    import glob
    backup_pattern = snbt_path + ".backup_*"
    backups = glob.glob(backup_pattern)
    if backups:
        backups.sort(reverse=True)
        return backups[0]
    return None


def load_all_quests(quests_folder, modpack_name="modpack"):
    lang_dict = {}

    if not os.path.isdir(quests_folder):
        print(f"FTB Quest folder not found: {quests_folder}")
        return lang_dict

    snbt_count = 0
    backup_used = 0
    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if not filename.endswith('.snbt'):
                continue
            if '.backup_' in filename:
                continue

            snbt_count += 1
            filepath = os.path.join(root, filename)

            quest_data = parse_snbt_file(filepath)
            if not quest_data:
                continue

            before_count = len(lang_dict)
            converted_keys = set()

            _extract_from_snbt(quest_data, lang_dict, converted_keys)

            need_backup = (converted_keys or len(lang_dict) == before_count)
            if need_backup:
                for k in converted_keys:
                    lang_dict.pop(k, None)

                backup_path = find_backup_file(filepath)
                if backup_path:
                    backup_data = parse_snbt_file(backup_path)
                    if backup_data:
                        _extract_from_snbt(backup_data, lang_dict)
                        backup_used += 1

    msg = f"FTB Quest: Parsed {snbt_count} SNBT files, extracted {len(lang_dict)} texts"
    if backup_used > 0:
        msg += f" ({backup_used} from backups)"
    print(msg)
    return lang_dict


def get_quest_file_count(quests_folder):
    count = 0
    if not os.path.isdir(quests_folder):
        return count

    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if filename.endswith('.snbt') and '.backup_' not in filename:
                count += 1
    return count


def export_ftbquest(quests_folder, output_folder, modpack_name, translations, target_lang="ja_jp"):
    import json

    lang_dict = {}

    lang_output = os.path.join(output_folder, "assets", "ftbquests", "lang")
    os.makedirs(lang_output, exist_ok=True)

    for key, value in translations.items():
        if value:
            normalized_value = value.replace('\\"', '"').replace('\\\"', '"')
            lang_dict[key] = normalized_value

    lang_path = os.path.join(lang_output, f"{target_lang}.json")
    with open(lang_path, 'w', encoding='utf-8') as f:
        json.dump(lang_dict, f, ensure_ascii=False, indent=2)

    print(f"FTB Quest Export: {len(lang_dict)} translations to resource pack ({target_lang})")
    return len(lang_dict)


def apply_snbt_with_backup(quests_folder, modpack_name, translations):
    import shutil
    from datetime import datetime

    converted_count = 0
    backup_count = 0

    backup_suffix = f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if not filename.endswith('.snbt'):
                continue
            if '.backup_' in filename:
                continue

            filepath = os.path.join(root, filename)
            backup_path = filepath + backup_suffix

            quest_data = parse_snbt_file(filepath)
            if not quest_data:
                continue

            conversion_count = _convert_chapter_data(quest_data, translations)

            if conversion_count > 0:
                shutil.copy2(filepath, backup_path)
                backup_count += 1

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(slib.dumps(quest_data))
                converted_count += 1

    print(f"FTB Quest Apply: {converted_count} files converted, {backup_count} backups created")
    return converted_count, backup_count


def migrate_old_keys(old_translations, quests_folder):
    if not old_translations:
        return {}

    new_lang = load_all_quests(quests_folder)
    if not new_lang:
        return {}

    old_by_text = {}
    for key, text in old_translations.items():
        if text:
            old_by_text[text] = key

    migrated = {}
    for new_key, original_text in new_lang.items():
        normalized = original_text.replace('\\"', '"').replace('\\\"', '"')
        if normalized in old_by_text:
            old_key = old_by_text[normalized]
            if old_key in old_translations and old_translations[old_key]:
                migrated[new_key] = old_translations[old_key]
        elif original_text in old_by_text:
            old_key = old_by_text[original_text]
            if old_key in old_translations and old_translations[old_key]:
                migrated[new_key] = old_translations[old_key]

    print(f"FTB Quest Migration: {len(migrated)}/{len(new_lang)} keys migrated from old format")
    return migrated
