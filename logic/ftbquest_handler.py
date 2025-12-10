"""
FTB Quest Handler - Parse SNBT files and extract translatable text
"""
import os
import re
import ftb_snbt_lib as slib
from ftb_snbt_lib import tag


def detect_ftbquests(minecraft_path):
    """Detect FTB Quests folder in a Minecraft directory"""
    possible_paths = [
        os.path.join(minecraft_path, "config", "ftbquests", "quests"),
        os.path.join(minecraft_path, "kubejs", "data", "ftbquests", "quests"),
    ]
    
    for path in possible_paths:
        if os.path.isdir(path):
            return path
    return None


def safe_name(name):
    """Convert name to safe key format"""
    return re.compile(r'\W+').sub("", name.lower().replace(" ", "_"))


def escape_text(text):
    """Escape special characters for lang file"""
    for match, seq in ((r'%', r'%%'), (r'"', r'\"')):
        text = text.replace(match, seq)
    return text


def filter_text(text):
    """Filter out texts that should not be translated"""
    if not text:
        return False
    if text.startswith("{") and text.endswith("}"):
        return False
    if text.startswith("[") and text.endswith("]"):
        return False
    return True


def parse_snbt_file(filepath):
    """Parse a single SNBT file and return the data"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return slib.loads(content)
    except Exception as e:
        print(f"Failed to parse {filepath}: {e}")
        return None


def extract_texts(quest_data, prefix, lang_dict=None):
    """
    Recursively extract translatable texts from quest data.
    Returns dict of {key: original_text}
    """
    if lang_dict is None:
        lang_dict = {}
    
    if not isinstance(quest_data, tag.Compound):
        return lang_dict
    
    for element in quest_data:
        value = quest_data[element]
        if value is None:
            continue
            
        # Recurse into compound tags
        if isinstance(value, tag.Compound):
            extract_texts(value, f"{prefix}.{element}", lang_dict)
        
        # Recurse into lists of compound tags
        elif isinstance(value, tag.List):
            try:
                for idx, item in enumerate(value):
                    if isinstance(item, tag.Compound):
                        extract_texts(item, f"{prefix}.{element}{idx}", lang_dict)
            except:
                pass
        
        # Extract translatable fields
        if element in ("title", "subtitle", "description"):
            if isinstance(value, tag.String):
                text = str(value)
                if filter_text(text):
                    lang_dict[f"{prefix}.{element}"] = escape_text(text)
            
            elif isinstance(value, tag.List):
                try:
                    for idx, item in enumerate(value):
                        text = str(item)
                        if filter_text(text):
                            lang_dict[f"{prefix}.{element}{idx}"] = escape_text(text)
                except:
                    pass
    
    return lang_dict


def find_backup_file(snbt_path):
    """Find the most recent backup file for an SNBT file"""
    import glob
    backup_pattern = snbt_path + ".backup_*"
    backups = glob.glob(backup_pattern)
    if backups:
        backups.sort(reverse=True)
        return backups[0]
    return None


def load_all_quests(quests_folder, modpack_name="modpack"):
    """
    Load all SNBT files from a quests folder and extract translatable texts.
    If SNBT is already converted (contains {key}), try to read from backup file.
    Returns dict of {key: original_text}
    """
    lang_dict = {}
    modpack_key = safe_name(modpack_name)
    
    if not os.path.isdir(quests_folder):
        print(f"FTB Quest folder not found: {quests_folder}")
        return lang_dict
    
    snbt_count = 0
    backup_used = 0
    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if filename.endswith('.snbt'):
                snbt_count += 1
                filepath = os.path.join(root, filename)
                quest_name = safe_name(os.path.splitext(filename)[0])
                
                quest_data = parse_snbt_file(filepath)
                if quest_data:
                    quest_key = f"{modpack_key}.{quest_name}"
                    before_count = len(lang_dict)
                    extract_texts(quest_data, quest_key, lang_dict)
                    
                    if len(lang_dict) == before_count:
                        backup_path = find_backup_file(filepath)
                        if backup_path:
                            backup_data = parse_snbt_file(backup_path)
                            if backup_data:
                                extract_texts(backup_data, quest_key, lang_dict)
                                backup_used += 1
    
    msg = f"FTB Quest: Parsed {snbt_count} SNBT files, extracted {len(lang_dict)} texts"
    if backup_used > 0:
        msg += f" ({backup_used} from backups)"
    print(msg)
    return lang_dict


def get_quest_file_count(quests_folder):
    """Count the number of SNBT files in a folder"""
    count = 0
    if not os.path.isdir(quests_folder):
        return count
    
    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if filename.endswith('.snbt'):
                count += 1
    return count


def convert_quest_data(quest_data, prefix, translations):
    """
    Recursively convert quest data by replacing translatable texts with keys.
    Updates quest_data in place and returns count of conversions.
    """
    if not isinstance(quest_data, tag.Compound):
        return 0
    
    count = 0
    for element in list(quest_data.keys()):
        value = quest_data[element]
        if value is None:
            continue
        
        if isinstance(value, tag.Compound):
            count += convert_quest_data(value, f"{prefix}.{element}", translations)
        
        elif isinstance(value, tag.List):
            try:
                for idx, item in enumerate(value):
                    if isinstance(item, tag.Compound):
                        count += convert_quest_data(item, f"{prefix}.{element}{idx}", translations)
            except:
                pass
        
        if element in ("title", "subtitle", "description"):
            if isinstance(value, tag.String):
                text = str(value)
                key = f"{prefix}.{element}"
                if filter_text(text) and key in translations and translations[key]:
                    quest_data[element] = tag.String(f"{{{key}}}")
                    count += 1
            
            elif isinstance(value, tag.List):
                try:
                    for idx in range(len(value)):
                        text = str(value[idx])
                        key = f"{prefix}.{element}{idx}"
                        if filter_text(text) and key in translations and translations[key]:
                            value[idx] = tag.String(f"{{{key}}}")
                            count += 1
                except:
                    pass
    
    return count


def export_ftbquest(quests_folder, output_folder, modpack_name, translations):
    """
    Export FTB Quest language file to resource pack format.
    Only outputs ja_jp.json to assets/ftbquests/lang/ (no SNBT conversion)
    Returns lang_count
    """
    import json
    
    lang_dict = {}
    
    lang_output = os.path.join(output_folder, "assets", "ftbquests", "lang")
    os.makedirs(lang_output, exist_ok=True)
    
    for key, value in translations.items():
        if value:
            # Normalize escaped quotes before saving
            normalized_value = value.replace('\\"', '"').replace('\\\"', '"')
            lang_dict[key] = normalized_value
    
    ja_jp_path = os.path.join(lang_output, "ja_jp.json")
    with open(ja_jp_path, 'w', encoding='utf-8') as f:
        json.dump(lang_dict, f, ensure_ascii=False, indent=2)
    
    print(f"FTB Quest Export: {len(lang_dict)} translations to resource pack")
    return len(lang_dict)


def apply_snbt_with_backup(quests_folder, modpack_name, translations):
    """
    Apply translations by converting SNBT files in place with backup.
    - Renames original files to .snbt.backup
    - Writes converted files with {key} placeholders
    Returns tuple of (converted_count, backup_count)
    """
    import shutil
    from datetime import datetime
    
    modpack_key = safe_name(modpack_name)
    converted_count = 0
    backup_count = 0
    
    backup_suffix = f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    for root, dirs, files in os.walk(quests_folder):
        for filename in files:
            if not filename.endswith('.snbt'):
                continue
            
            filepath = os.path.join(root, filename)
            backup_path = filepath + backup_suffix
            
            quest_data = parse_snbt_file(filepath)
            if not quest_data:
                continue
            
            quest_name = safe_name(os.path.splitext(filename)[0])
            quest_key = f"{modpack_key}.{quest_name}"
            
            conversion_count = convert_quest_data(quest_data, quest_key, translations)
            
            if conversion_count > 0:
                shutil.copy2(filepath, backup_path)
                backup_count += 1
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(slib.dumps(quest_data))
                converted_count += 1
    
    print(f"FTB Quest Apply: {converted_count} files converted, {backup_count} backups created")
    return converted_count, backup_count


