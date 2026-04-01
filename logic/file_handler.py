import zipfile
import json
import os
import re

PACK_FORMATS = {
    "1.16.x": 6,
    "1.17.x": 7,
    "1.18.x": 8,
    "1.19.0-1.19.3": 9,
    "1.19.4": 12,
    "1.20.0-1.20.1": 15,
    "1.20.2": 18,
    "1.20.3-1.20.4": 22,
    "1.21.0-1.21.3": 34,
    "1.21.4": 42,
}

TARGET_LANGUAGES = {
    "ja_jp": ("Japanese", "日本語"),
    "zh_cn": ("Simplified Chinese", "簡体字中国語"),
    "ko_kr": ("Korean", "韓国語"),
    "fr_fr": ("French", "フランス語"),
    "de_de": ("German", "ドイツ語"),
    "es_es": ("Spanish", "スペイン語"),
    "pt_br": ("Portuguese (BR)", "ポルトガル語 (ブラジル)"),
    "ru_ru": ("Russian", "ロシア語"),
    "it_it": ("Italian", "イタリア語"),
    "pl_pl": ("Polish", "ポーランド語"),
}


class FileHandler:
    def _normalize_escapes(self, text):
        """Fix escaped characters that should be plain in final output.
        
        In the editor, user sees: \"プロローグ\" (backslash + quote)
        This should become just: "プロローグ" (plain quotes)
        """
        if not isinstance(text, str):
            return text
        # Replace literal backslash+quote with just quote
        # In Python: '\\\"' represents the 2-character sequence: \ and "
        result = text.replace('\\\"', '"')
        # Also handle the case where it's just backslash before quote
        result = result.replace('\\"', '"')
        return result
    
    def _normalize_translations(self, translations):
        """Normalize all translation values to fix double escapes."""
        return {k: self._normalize_escapes(v) for k, v in translations.items()}
    
    def load_zip(self, file_path):
        """
        Loads a zip/jar file and finds translation files.
        Returns: (mod_name, found_files)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError("File not found")

        mod_name = os.path.splitext(os.path.basename(file_path))[0]
        
        found_files = []
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                file_list = zf.namelist()
                for f in file_list:
                    if f.startswith('assets/') and '/lang/' in f:
                        if f.endswith('.json') or f.endswith('.lang'):
                            found_files.append(f)
        except zipfile.BadZipFile:
            raise Exception("Invalid ZIP/JAR file")
            
        return mod_name, found_files

    def load_folder(self, folder_path):
        """
        Loads a folder and finds translation files.
        Returns: (mod_name, found_files)
        """
        if not os.path.isdir(folder_path):
            raise FileNotFoundError("Folder not found")

        mod_name = os.path.basename(folder_path)
        
        found_files = []
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if f.endswith('.json') or f.endswith('.lang'):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, folder_path)
                    if 'assets' in rel_path and 'lang' in rel_path:
                        found_files.append(rel_path)
            
        return mod_name, found_files

    def read_translation_file(self, source_path, internal_path):
        """Reads a translation file from a zip or folder."""
        # Check if source is a folder
        if os.path.isdir(source_path):
            return self.read_translation_file_from_folder(source_path, internal_path)
        else:
            return self.read_translation_file_from_zip(source_path, internal_path)
    
    def read_translation_file_from_zip(self, zip_path, internal_path):
        """Reads a translation file from a zip."""
        with zipfile.ZipFile(zip_path, 'r') as zf:
            with zf.open(internal_path) as f:
                content = f.read().decode('utf-8', errors='replace')
                
                if internal_path.endswith('.json'):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {}
                else:
                    return self._parse_lang(content)

    def read_translation_file_from_folder(self, folder_path, internal_path):
        """Reads a translation file from a folder."""
        full_path = os.path.join(folder_path, internal_path)
        if not os.path.exists(full_path):
            return {}
            
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            
        if internal_path.endswith('.json'):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {}
        else:
            return self._parse_lang(content)

    def _parse_lang(self, content):
        result = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            result[key.strip()] = val.strip()
        return result

    def save_resource_pack(self, output_path, mod_name, translations, lang_path="en_us.json",
                           pack_format=15, target_lang="ja_jp"):
        target_path = lang_path.replace('en_us', target_lang)
        if target_path == lang_path and target_lang not in target_path:
             pass 
        
        if not os.path.exists(output_path):
            os.makedirs(output_path)
            
        pack_meta = {
            "pack": {
                "pack_format": pack_format,
                "description": f"Translations for {mod_name}"
            }
        }
        with open(os.path.join(output_path, 'pack.mcmeta'), 'w', encoding='utf-8') as f:
            json.dump(pack_meta, f, indent=2)
            
        full_target_path = os.path.join(output_path, target_path)
        os.makedirs(os.path.dirname(full_target_path), exist_ok=True)
        
        normalized = self._normalize_translations(translations)
        with open(full_target_path, 'w', encoding='utf-8') as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)

    def save_merged_resource_pack(self, output_path, mod_data_list,
                                   pack_format=15, target_lang="ja_jp"):
        if not os.path.exists(output_path):
            os.makedirs(output_path)

        pack_meta = {
            "pack": {
                "pack_format": pack_format,
                "description": "Merged Translations Pack"
            }
        }
        with open(os.path.join(output_path, 'pack.mcmeta'), 'w', encoding='utf-8') as f:
            json.dump(pack_meta, f, indent=2)
            
        for mod in mod_data_list:
            translations = mod["translations"]
            if not translations:
                continue
                
            target_path = mod["target_file"].replace('en_us', target_lang)
            
            full_target_path = os.path.join(output_path, target_path)
            os.makedirs(os.path.dirname(full_target_path), exist_ok=True)
            
            normalized = self._normalize_translations(translations)
            with open(full_target_path, 'w', encoding='utf-8') as f:
                json.dump(normalized, f, indent=2, ensure_ascii=False)
