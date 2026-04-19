import os
import json


def detect_patchouli_books(minecraft_path):
    results = []

    config_path = os.path.join(minecraft_path, "config", "patchouli_books")
    if os.path.isdir(config_path):
        for book_id in os.listdir(config_path):
            book_dir = os.path.join(config_path, book_id)
            if os.path.isdir(book_dir):
                results.append(book_dir)

    return results


def detect_patchouli_in_datapack(data_dir):
    results = []
    if not os.path.isdir(data_dir):
        return results

    for namespace in os.listdir(data_dir):
        ns_dir = os.path.join(data_dir, namespace)
        patchouli_dir = os.path.join(ns_dir, "patchouli_books")
        if not os.path.isdir(patchouli_dir):
            continue
        for book_id in os.listdir(patchouli_dir):
            book_dir = os.path.join(patchouli_dir, book_id)
            if os.path.isdir(book_dir):
                results.append(book_dir)

    return results


def _load_json(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _extract_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and "text" in value:
        return value["text"].strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            t = _extract_text(item)
            if t:
                parts.append(t)
        return "".join(parts)
    return ""


def load_patchouli_book(book_dir, book_id=None):
    if book_id is None:
        book_id = os.path.basename(book_dir)

    lang_dict = {}

    for lang_dir_name in os.listdir(book_dir):
        lang_dir = os.path.join(book_dir, lang_dir_name)
        if not os.path.isdir(lang_dir):
            continue

        categories_dir = os.path.join(lang_dir, "categories")
        if os.path.isdir(categories_dir):
            for fname in os.listdir(categories_dir):
                if not fname.endswith('.json'):
                    continue
                data = _load_json(os.path.join(categories_dir, fname))
                if not data:
                    continue
                cat_id = data.get("id", os.path.splitext(fname)[0])
                name = _extract_text(data.get("name", ""))
                if name:
                    lang_dict[f"patchouli.{book_id}.category.{cat_id}"] = name
                desc = _extract_text(data.get("description", ""))
                if desc:
                    lang_dict[f"patchouli.{book_id}.category.{cat_id}.description"] = desc

        entries_dir = os.path.join(lang_dir, "entries")
        if os.path.isdir(entries_dir):
            for root, dirs, files in os.walk(entries_dir):
                for fname in files:
                    if not fname.endswith('.json'):
                        continue
                    data = _load_json(os.path.join(root, fname))
                    if not data:
                        continue
                    entry_id = data.get("id", os.path.splitext(fname)[0])
                    name = _extract_text(data.get("name", ""))
                    if name:
                        lang_dict[f"patchouli.{book_id}.entry.{entry_id}"] = name

                    pages = data.get("pages", [])
                    if isinstance(pages, list):
                        for idx, page in enumerate(pages):
                            if not isinstance(page, dict):
                                continue
                            page_type = page.get("type", "patchouli:text")
                            if page_type == "patchouli:link":
                                link_title = _extract_text(page.get("title", ""))
                                if link_title:
                                    lang_dict[f"patchouli.{book_id}.entry.{entry_id}.page.{idx}.title"] = link_title
                                link_text = _extract_text(page.get("text", ""))
                                if link_text:
                                    lang_dict[f"patchouli.{book_id}.entry.{entry_id}.page.{idx}"] = link_text
                                continue

                            page_title = _extract_text(page.get("title", ""))
                            if page_title:
                                lang_dict[f"patchouli.{book_id}.entry.{entry_id}.page.{idx}.title"] = page_title
                            page_text = _extract_text(page.get("text", ""))
                            if page_text:
                                lang_dict[f"patchouli.{book_id}.entry.{entry_id}.page.{idx}"] = page_text

    return book_id, lang_dict
