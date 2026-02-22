import json
import os
from typing import Dict, Any
from config import Config

class LocaleManager:
    def __init__(self):
        self.locales = {}
        self.load_locales()
    
    def load_locales(self):
        """Загрузка локализаций из файлов"""
        locales_dir = "locales"
        for filename in os.listdir(locales_dir):
            if filename.endswith(".json"):
                lang = filename.split(".")[0]
                with open(os.path.join(locales_dir, filename), 'r', encoding='utf-8') as f:
                    self.locales[lang] = json.load(f)
    
    def get_text(self, lang: str, key: str, **kwargs) -> str:
        """Получение текста по ключу с подстановкой параметров"""
        keys = key.split(".")
        text = self.locales.get(lang, self.locales["ru"])
        
        for k in keys:
            if isinstance(text, dict):
                text = text.get(k, "")
            else:
                return key
        
        if text and kwargs:
            try:
                return text.format(**kwargs)
            except:
                return text
        
        return text or key
    
    def translate_fruit(self, fruit_name: str, lang: str) -> str:
        """Перевод названия фрукта"""
        if lang == "RUS":
            return Config.FRUIT_TRANSLATIONS.get(fruit_name, fruit_name)
        return fruit_name
    
    def get_fruit_emoji(self, fruit_name: str, lang: str) -> str:
        """Получение эмодзи для фрукта"""
        from utils.filters import MessageFilter
        return MessageFilter.get_fruit_emoji(fruit_name, lang)
    
    def get_fruit_display(self, fruit_name: str, lang: str) -> str:
        """Получение отображаемого названия фрукта с эмодзи"""
        if lang == "RUS":
            translated = self.translate_fruit(fruit_name, lang)
            emoji = self.get_fruit_emoji(fruit_name, lang)
            return f"{emoji} {translated}"
        else:
            emoji = self.get_fruit_emoji(fruit_name, lang)
            return f"{emoji} {fruit_name}"

# Создаем глобальный экземпляр
locale_manager = LocaleManager()