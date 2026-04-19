import zipfile
import json
import os
import re
import io

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
    @staticmethod
    def _is_safe_zip_path(path):
        if not path:
            return False
        norm = os.path.normpath(path)
        if norm.startswith('..') or os.path.isabs(norm):
            return False
        if '..' in norm.split(os.sep):
            return False
        return True

    def _normalize_escapes(self, text):
        if not isinstance(text, str):
            return text
        result = text.replace('\\\\', '\x00')
        result = result.replace('\\"', '"')
        result = result.replace('\x00', '\\')
        return result
    
    def _normalize_translations(self, translations):
        """Normalize all translation values to fix double escapes."""
        return {k: self._normalize_escapes(v) for k, v in translations.items()}
    
    def _is_lang_file(self, filename):
        return filename.endswith('.json') or filename.endswith('.lang') or filename.endswith('.toml')

    def _is_lang_path(self, path):
        if '/lang/' not in path:
            return False
        if path.startswith('assets/') or path.startswith('data/'):
            return True
        return False

    def load_zip(self, file_path):
        """
        Loads a zip/jar file and finds translation files.
        Scans assets/*/lang/ and data/*/lang/ for .json, .lang, .toml files.
        Also scans nested JARs inside META-INF/jars/.
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
                    if not self._is_safe_zip_path(f):
                        continue
                    if self._is_lang_path(f) and self._is_lang_file(f):
                        found_files.append(f)

                nested_jars = [f for f in file_list
                               if f.startswith('META-INF/jars/') and f.endswith('.jar')]
                for nested in nested_jars:
                    if not self._is_safe_zip_path(nested):
                        continue
                    try:
                        with zf.open(nested) as nested_file:
                            nested_data = io.BytesIO(nested_file.read())
                            with zipfile.ZipFile(nested_data, 'r') as nzf:
                                for nf in nzf.namelist():
                                    if not self._is_safe_zip_path(nf):
                                        continue
                                    if self._is_lang_path(nf) and self._is_lang_file(nf):
                                        found_files.append(f"jarjar:{nested}!{nf}")
                    except (zipfile.BadZipFile, OSError):
                        pass
        except zipfile.BadZipFile:
            raise Exception("Invalid ZIP/JAR file")
            
        return mod_name, found_files

    def load_folder(self, folder_path):
        """
        Loads a folder and finds translation files.
        Searches both assets/ and data/ paths for lang files.
        Returns: (mod_name, found_files)
        """
        if not os.path.isdir(folder_path):
            raise FileNotFoundError("Folder not found")

        mod_name = os.path.basename(folder_path)
        
        found_files = []
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if not self._is_lang_file(f):
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, folder_path)
                if ('assets' in rel_path and 'lang' in rel_path) or \
                   ('data' in rel_path and 'lang' in rel_path):
                    found_files.append(rel_path)
            
        return mod_name, found_files

    def read_translation_file(self, source_path, internal_path):
        if internal_path.startswith("jarjar:"):
            return self._read_from_nested_jar(source_path, internal_path)
        if os.path.isdir(source_path):
            return self.read_translation_file_from_folder(source_path, internal_path)
        else:
            return self.read_translation_file_from_zip(source_path, internal_path)

    def _read_from_nested_jar(self, source_path, internal_path):
        prefix = "jarjar:"
        rest = internal_path[len(prefix):]
        sep = rest.index('!')
        outer_entry = rest[:sep]
        inner_path = rest[sep + 1:]

        if not self._is_safe_zip_path(outer_entry) or not self._is_safe_zip_path(inner_path):
            raise ValueError(f"安全でないパス: {internal_path}")

        with zipfile.ZipFile(source_path, 'r') as outer_zf:
            with outer_zf.open(outer_entry) as nested_file:
                nested_data = io.BytesIO(nested_file.read())
                with zipfile.ZipFile(nested_data, 'r') as inner_zf:
                    with inner_zf.open(inner_path) as f:
                        content = f.read().decode('utf-8', errors='replace')
                        return self._parse_lang_content(content, inner_path)

    @staticmethod
    def _clean_json(content):
        content = content.lstrip('\ufeff')
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            cleaned = re.sub(r',\s*}', '}', content)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                return {}

    def _parse_lang_content(self, content, path):
        if path.endswith('.json'):
            return self._clean_json(content)
        elif path.endswith('.toml'):
            return self._parse_toml_lang(content)
        else:
            return self._parse_lang(content)
    
    def read_translation_file_from_zip(self, zip_path, internal_path):
        if not self._is_safe_zip_path(internal_path):
            raise ValueError(f"安全でないパス: {internal_path}")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            with zf.open(internal_path) as f:
                content = f.read().decode('utf-8', errors='replace')
                return self._parse_lang_content(content, internal_path)

    def read_translation_file_from_folder(self, folder_path, internal_path):
        full_path = os.path.join(folder_path, internal_path)
        if not os.path.exists(full_path):
            return {}
            
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            
        return self._parse_lang_content(content, internal_path)

    def _parse_lang(self, content):
        result = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            result[key.strip()] = val.strip()
        return result

    def _parse_toml_lang(self, content):
        result = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('['):
                continue
            if '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip()
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = val[1:-1]
                val = val.replace('\\"', '"').replace('\\\\', '\\')
            elif val.startswith("'") and val.endswith("'") and len(val) >= 2:
                val = val[1:-1]
            else:
                continue
            result[key] = val
        return result

    @staticmethod
    def _ensure_json_ext(path):
        if path.endswith('.toml'):
            return path[:-5] + '.json'
        if path.endswith('.lang'):
            return path[:-5] + '.json'
        return path

    def save_resource_pack(self, output_path, mod_name, translations, lang_path="en_us.json",
                           pack_format=15, target_lang="ja_jp"):
        lang_path = self._ensure_json_ext(lang_path)
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
        real_output = os.path.realpath(output_path)
        real_target = os.path.realpath(os.path.dirname(full_target_path))
        if not real_target.startswith(real_output):
            raise ValueError(f"パストラバーサル検出: {target_path}")
        os.makedirs(real_target, exist_ok=True)
        
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
                
            target_path = self._ensure_json_ext(mod["target_file"]).replace('en_us', target_lang)
            
            full_target_path = os.path.join(output_path, target_path)
            real_output = os.path.realpath(output_path)
            real_target = os.path.realpath(os.path.dirname(full_target_path))
            if not real_target.startswith(real_output):
                raise ValueError(f"パストラバーサル検出: {target_path}")
            os.makedirs(real_target, exist_ok=True)
            
            normalized = self._normalize_translations(translations)
            with open(full_target_path, 'w', encoding='utf-8') as f:
                json.dump(normalized, f, indent=2, ensure_ascii=False)
